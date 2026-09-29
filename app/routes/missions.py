from pathlib import Path

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, Response, abort, jsonify
)
from werkzeug.utils import secure_filename

from app import repo
from app.auth import current_user, is_admin, wants_json
from app.config import COMPANY, GOOGLE_MAPS_API_KEY, PAUSE_LABELS
from app.pdf_service import (
    generate_mission_pdf, generate_bc_pdf, extract_pdf_pages, PdfGenerationError,
    POSITION_BEFORE_OM, POSITION_AFTER_OM, POSITION_AFTER_BC,
)
from app.email_service import (send_mission_email, send_bulk_email,
                               send_followup_email, EmailError)
from app.routing import estimate_route, format_duration, add_minutes, build_driver_itinerary_url, RoutingError
from app.routes.settings import get_address_search_provider
from app.utils import (
    DEFAULT_PARTNER_EMAIL_BODY, DEFAULT_PARTNER_EMAIL_SUBJECT,
    balance_passenger_counts, day_label, fmt_date_full, fmt_date_long, fmt_date_short,
    fmt_hours_minutes, fmt_time, is_valid_time, legs_distance_summary, legs_time_summary,
    normalize_time, now_paris, service_time_range, shuttle_number,
)

bp = Blueprint("missions", __name__, url_prefix="/missions")

ATTACHMENT_POSITIONS = [
    (POSITION_BEFORE_OM, "Avant l'Ordre de mission"),
    (POSITION_AFTER_OM, "Après l'OM, avant le BC (page 2 — recommandé pour la feuille de référence)"),
    (POSITION_AFTER_BC, "Après le Billet collectif"),
]

ALLOWED_ATTACHMENT_EXT = {".pdf", ".png", ".jpg", ".jpeg"}

PER_PAGE = 50


def _at(lst, i, default=""):
    return lst[i] if i < len(lst) else default


def _parse_legs(form):
    # Indexation "sûre" plutôt que zip() : si un champ optionnel est absent
    # du formulaire pour certaines lignes, on ne désaligne pas les autres.
    starts = form.getlist("leg_start_time[]")
    ends = form.getlist("leg_end_time[]")
    vehicle_ids = form.getlist("leg_vehicle_id[]")
    labels = form.getlist("leg_label[]")
    relay_drivers = form.getlist("leg_relay_driver_id[]")
    # Distance estimée par le formulaire (champ caché, rempli au fil de la
    # saisie par mission_form.js) : on la stocke telle quelle plutôt que de
    # rappeler le service d'itinéraire à chaque enregistrement.
    distances = form.getlist("leg_distance_m[]")
    n = max(len(starts), len(ends), len(vehicle_ids), len(labels))
    legs = []
    for i in range(n):
        s, e, v, l = _at(starts, i).strip(), _at(ends, i).strip(), _at(vehicle_ids, i), _at(labels, i).strip()
        if not s and not e and not l:
            continue
        # Le champ est du texte libre : normalise "11h00" saisi par réflexe
        # (c'est le format affiché partout ailleurs) vers "11:00".
        s, e = normalize_time(s), normalize_time(e)
        is_relay = v == "relais"
        rd = _at(relay_drivers, i)
        # Point de contrôle : début = fin, ou une prise/fin de service (dont
        # les heures sont laissées vides, remplies à la main par le chauffeur).
        is_service = l.lower().startswith(("prise de service", "fin de service"))
        meters = _at(distances, i).strip()
        legs.append({
            "distance_m": int(meters) if meters.isdigit() else None,
            "start_time": s,
            "end_time": e,
            "vehicle_id": int(v) if (v and v.isdigit()) else None,
            "label": l,
            "is_checkpoint": ((bool(s) and s == e) or is_service) and not is_relay,
            "is_relay": is_relay,
            "relay_driver_id": int(rd) if (is_relay and rd and rd.isdigit()) else None,
        })
    return legs


def _parse_stops(form):
    types = form.getlist("stop_type[]")
    dates = form.getlist("stop_date[]")
    times = form.getlist("stop_time[]")
    addresses = form.getlist("stop_address[]")
    cities = form.getlist("stop_city[]")
    counts = form.getlist("stop_passenger_count[]")
    names = form.getlist("stop_passenger_name[]")
    phones = form.getlist("stop_passenger_phone[]")
    refs = form.getlist("stop_booking_ref[]")
    n = len(addresses)
    stops = []
    for i in range(n):
        a = _at(addresses, i).strip()
        if not a:
            continue
        cnt = _at(counts, i).strip()
        stops.append({
            "stop_type": _at(types, i) if _at(types, i) in ("prise_en_charge", "depose") else "depose",
            "stop_date": _at(dates, i) or None,
            "stop_time": _at(times, i).strip(),
            "address": a,
            "city": _at(cities, i).strip() or None,
            "passenger_count": int(cnt) if cnt.isdigit() else 1,
            "passenger_name": _at(names, i).strip() or None,
            "passenger_phone": _at(phones, i).strip() or None,
            "booking_ref": _at(refs, i).strip() or None,
        })
    return stops


def _mission_form_to_data(form):
    data = {
        "driver_id": int(form["driver_id"]) if form.get("driver_id") else None,
        "mission_date": form.get("mission_date") or None,
        "mission_name": form.get("mission_name", "").strip() or None,
        "shuttle_label": form.get("shuttle_label", "").strip() or None,
        "motif": form.get("motif", "").strip() or "Transport Occasionnel",
        "remarks": form.get("remarks", "").strip() or None,
        "client_id": int(form["client_id"]) if form.get("client_id") else None,
        # Coordonnées portées sur le BC : recopiées de la fiche client à la
        # sélection, puis modifiables pour cette mission seulement.
        "bc_client_name": form.get("bc_client_name", "").strip() or None,
        "bc_client_address": form.get("bc_client_address", "").strip() or None,
        "bc_client_postal_code": form.get("bc_client_postal_code", "").strip() or None,
        "bc_client_city": form.get("bc_client_city", "").strip() or None,
        "bc_client_phone": form.get("bc_client_phone", "").strip() or None,
        "emission_date": form.get("emission_date") or None,
        "price": form.get("price", "").strip() or None,
        # Bascule « Masquer le prix » : un champ caché piloté par le bouton
        # du formulaire (mission_form.js:initPriceVisibility), pour que le
        # réglage soit le même qu'au bouton de la fiche.
        "price_hidden": form.get("price_hidden") == "1",
        "status": form.get("status") or "brouillon",
        "om_template_id": int(form["om_template_id"]) if form.get("om_template_id") else None,
        "bc_template_id": int(form["bc_template_id"]) if form.get("bc_template_id") else None,
    }
    # Les arrêts (BC) alimentent aussi par défaut les dates des trajets (OM)
    # via mission_date déjà fourni ; on complète stop_date manquant.
    stops = _parse_stops(form)
    for s in stops:
        s["stop_date"] = s["stop_date"] or data["mission_date"]
    # L'arrêt qui regroupe tous les voyageurs porte la somme des autres.
    # Le formulaire le calcule déjà en direct (champ en lecture seule) ;
    # on le refait ici pour que la règle tienne aussi sans JavaScript.
    balance_passenger_counts(stops)
    data["legs"] = _parse_legs(form)
    data["stops"] = stops
    data["linked_mission_ids"] = _parse_ids(form.getlist("linked_mission_ids[]"))
    return data


def _check_form(data):
    """Envoi du formulaire OM : (message d'erreur ou None, pièce jointe
    éventuelle). Le fichier est contrôlé avant d'enregistrer quoi que ce
    soit, pour ne pas créer une mission à qui il manquerait sa pièce."""
    if not data["driver_id"] or not data["mission_date"]:
        return "Chauffeur et date de mission sont obligatoires.", None
    try:
        return None, _read_attachment(request.form, request.files)
    except ValueError as e:
        return str(e), None


def _form_context(mission=None):
    # Liens renvoyés par le formulaire (erreur de saisie), sinon ceux déjà
    # enregistrés pour la mission modifiée.
    linked_ids = mission.get("linked_mission_ids")
    if linked_ids is None:
        linked_ids = repo.get_linked_mission_ids(mission["id"]) if mission.get("id") else []
    return {
        "linked_missions": _linked_rows_for(linked_ids),
        "positions": ATTACHMENT_POSITIONS,
        "drivers": repo.list_drivers(include_inactive=False),
        "vehicles": repo.list_vehicles(include_inactive=False),
        "clients": repo.list_clients(pinned_first=True),
        "clients_by_id": {
            c["id"]: {"name": c["name"], "address": c.get("address") or "",
                      "postal_code": c.get("postal_code") or "", "city": c.get("city") or "",
                      "phone": c.get("phone") or c.get("email") or ""}
            for c in repo.list_clients()
        },
        "om_templates": repo.list_templates("OM"),
        "bc_templates": repo.list_templates("BC"),
        "mission": mission,
        "google_maps_api_key": GOOGLE_MAPS_API_KEY,
        "address_search_provider": get_address_search_provider(),
        "depot_address": f"{COMPANY['address']}, {COMPANY['postal_code']} {COMPANY['city']}",
        "pause_labels": PAUSE_LABELS,
    }


def _add_service_times(missions):
    """Prise / fin de service et amplitude de chaque mission, calculées
    depuis ses trajets : colonnes de la liste des OM et des missions liées."""
    repo.attach_legs(missions)
    for m in missions:
        m["service_start"], m["service_end"] = service_time_range(m["legs"])
        summary = legs_time_summary(m["legs"])
        m["amplitude_minutes"] = summary["amplitude"] if summary else None
    return missions


@bp.route("/")
def list_missions_view():
    driver_id = request.args.get("driver_id", type=int)
    # Un chauffeur non administrateur ne voit que ses propres OM, quel que
    # soit le paramètre d'URL.
    own = _own_driver_id()
    if own is not None:
        driver_id = own
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    status = request.args.get("status") or None
    name = request.args.get("name", "").strip() or None
    tab = request.args.get("tab")
    tab = tab if tab in ("past", "archived") else "current"

    # L'onglet pose une borne de date automatique, combinée (ET) avec les
    # bornes saisies dans les filtres : c'est la plus restrictive qui gagne.
    # La journée en cours appartient aux deux onglets : c'est l'heure de fin
    # de service qui tranche, mission par mission (repo._service_cutoff).
    # L'onglet Archivées, lui, ne borne rien : une mission archivée s'y
    # retrouve quelle que soit sa date.
    now = now_paris()
    today = now.date().isoformat()
    if tab == "archived":
        eff_from, eff_to, cutoff = date_from, date_to, None
    elif tab == "past":
        eff_from, eff_to = date_from, min(date_to, today) if date_to else today
        cutoff = (tab, today, now.strftime("%H:%M"))
    else:
        eff_from, eff_to = (max(date_from, today) if date_from else today), date_to
        cutoff = (tab, today, now.strftime("%H:%M"))

    criteria = dict(driver_id=driver_id, date_from=eff_from, date_to=eff_to,
                    status=status, name=name, service_cutoff=cutoff,
                    archived=(tab == "archived"))
    total = repo.count_missions(**criteria)
    total_pages = max(1, -(-total // PER_PAGE))  # division entière arrondie au supérieur
    page = min(max(request.args.get("page", type=int) or 1, 1), total_pages)

    missions = repo.list_missions(
        **criteria,
        ascending=(tab == "current"),  # à venir : le plus proche d'abord
        limit=PER_PAGE, offset=(page - 1) * PER_PAGE,
    )
    _add_service_times(missions)
    return render_template(
        "missions/list.html", missions=missions, drivers=repo.list_drivers(), tab=tab,
        total=total, page=page, total_pages=total_pages,
        filters={"driver_id": driver_id, "date_from": date_from, "date_to": date_to,
                 "status": status, "name": name},
    )


@bp.route("/estimer-duree", methods=["POST"])
def estimate_leg_duration():
    """Estimation de la durée d'une ligne de trajet (bouton « Estimer »
    du formulaire). Appelé en fetch, répond en JSON. La clé TomTom reste
    côté serveur."""
    origin = request.form.get("from", "").strip()
    destination = request.form.get("to", "").strip()
    if not origin or not destination:
        return jsonify({"ok": False, "error": "Le libellé doit être de la forme « départ → arrivée »."}), 400
    # Heure de début prioritaire ; à défaut, heure de fin (arriver à l'heure).
    start_time = request.form.get("start_time", "").strip() or None
    end_time = request.form.get("end_time", "").strip() or None
    try:
        result = estimate_route(
            origin, destination,
            mission_date=request.form.get("mission_date") or None,
            start_time=start_time, end_time=end_time,
        )
    except RoutingError as e:
        return jsonify({"ok": False, "error": str(e)}), 502

    return jsonify({
        "ok": True,
        "duration": format_duration(result["duration_s"]),
        "km": round(result["distance_m"] / 1000),
        # Mètres bruts : c'est ce que le formulaire enregistre, pour que la
        # somme de plusieurs trajets courts ne parte pas en arrondis.
        "meters": result["distance_m"],
        "traffic_min": round(result["traffic_delay_s"] / 60),
        "with_traffic_at": result["departure"],
        # Heure estimée, affichée seulement — les champs ne sont pas modifiés.
        "arrival_time": add_minutes(start_time, result["duration_s"]) if start_time else None,
        "departure_time": add_minutes(end_time, -result["duration_s"]) if not start_time and end_time else None,
    })


@bp.route("/nouveau", methods=["GET", "POST"])
def new_mission():
    if request.method == "POST":
        data = _mission_form_to_data(request.form)
        error, attachment = _check_form(data)
        if error:
            flash(error, "error")
            return render_template("missions/form.html", is_new=True, **_form_context(data))
        mission_id = repo.create_mission(data)
        if attachment:
            repo.add_attachment(mission_id, *attachment)
        flash("Ordre de mission créé.", "success")
        return (_created_return_redirect(mission_id)
                or redirect(url_for("missions.detail_mission", mission_id=mission_id)))
    return render_template("missions/form.html", is_new=True, **_form_context({
        "status": "brouillon", "motif": "Transport Occasionnel",
        "driver_id": None, "client_id": None, "om_template_id": None, "bc_template_id": None,
        "mission_date": "", "mission_name": "", "emission_date": now_paris().date().isoformat(),
        "shuttle_label": "", "price": "", "remarks": "",
        "bc_client_name": "", "bc_client_address": "", "bc_client_postal_code": "",
        "bc_client_city": "", "bc_client_phone": "",
        "legs": [], "stops": [],
    }))


def _own_driver_id():
    """Identifiant du chauffeur connecté quand il n'est PAS administrateur
    — c'est-à-dire quand sa vue doit être limitée à ses propres missions.
    None pour un administrateur (aucune restriction)."""
    if is_admin():
        return None
    user = current_user()
    return user["id"] if user else None


def _require_mission_access(mission):
    """404 si la mission n'appartient pas au chauffeur connecté. 404 plutôt
    que 403 : inutile de confirmer l'existence d'une mission qu'il n'a pas
    à voir."""
    own = _own_driver_id()
    if own is not None and mission.get("driver_id") != own:
        abort(404)


def _billing_summary(mission):
    """Récapitulatif à copier-coller pour la facturation (affiché sur la
    fiche mission, jamais dans le PDF) :

        NAVETTE 3 - Aller - 24/08/2026 03h30

        Prise en charge : Illiers-Combray, 1 rue A - 2 pax
        Dépose : Fleury-les-Aubrais, PK Simplon - 6 pax

    Le sens est déduit des arrêts : une dépose unique = aller (on ramasse
    puis on dépose tout le monde au même endroit), une prise en charge
    unique = retour."""
    stops = mission.get("stops") or []

    n_pickup = sum(1 for s in stops if s["stop_type"] == "prise_en_charge")
    n_dropoff = len(stops) - n_pickup
    if n_pickup == 1 and n_dropoff > 1:
        direction = "Retour"
    elif n_dropoff == 1 and n_pickup > 1:
        direction = "Aller"
    else:
        direction = "Aller" if n_pickup >= n_dropoff else "Retour"

    number = shuttle_number(mission.get("shuttle_label"))
    # Heure du 1er arrêt du Billet Collectif (pas celle de la prise de service).
    start = stops[0]["stop_time"] if stops else ""
    header = " - ".join([
        # Sans numéro de navette renseigné, on écrit « NAVETTE » tout court
        # plutôt qu'un « NAVETTE X » qui se retrouverait tel quel en facture.
        f"NAVETTE {number}".strip(),
        direction,
        f"{fmt_date_long(mission['mission_date'])} {fmt_time(start)}".strip(),
    ])

    lines = []
    for s in stops:
        place = ", ".join(p for p in [(s.get("city") or "").strip(),
                                      (s.get("address") or "").strip()] if p)
        if not place:
            continue
        label = "Prise en charge" if s["stop_type"] == "prise_en_charge" else "Dépose"
        lines.append(f"{label} : {place} - {s.get('passenger_count') or 1} pax")
    return header + "\n\n" + "\n".join(lines) if lines else header


@bp.route("/<int:mission_id>")
def detail_mission(mission_id):
    mission = repo.get_mission(mission_id)
    if not mission:
        abort(404)
    _require_mission_access(mission)
    emails = repo.list_email_log(mission_id)
    # ?embed=1 : fiche ouverte dans le panneau flottant d'une mission liée.
    # Seule l'enveloppe de la page change (base.html : ni barre du haut, ni
    # pied de page) — le contenu, lui, est la fiche entière, droits compris.
    embed = bool(request.args.get("embed"))
    linked = _linked_rows_for(repo.get_linked_mission_ids(mission_id)) if is_admin() else []
    return render_template("missions/detail.html", mission=mission, emails=emails,
                            positions=ATTACHMENT_POSITIONS,
                            billing_summary=_billing_summary(mission),
                            legs_summary=legs_time_summary(mission["legs"]),
                            legs_distance=legs_distance_summary(mission["legs"]),
                            linked_missions=linked, embed=embed)


def _detail_url(mission_id, anchor=""):
    """Adresse de la fiche après une action. `embed=1` est reconduit quand
    l'action est partie du panneau flottant d'une mission liée : sans lui, la
    barre de navigation réapparaîtrait à l'intérieur du panneau."""
    url = url_for("missions.detail_mission", mission_id=mission_id,
                  embed=1 if request.args.get("embed") else None)
    return url + anchor


# ---------------------------------------------------------- missions liées
def _parse_ids(values):
    return [int(v) for v in values if v.isdigit()]


def _name_codes(name):
    """Codes en tête d'un nom de mission, en majuscules : « 26MONTENEG A »
    -> {'26MONTENEG'}. Un nom combiné « 26POUILLES A + 26MONTENEG R »
    compte ses deux parties."""
    return {part.split()[0].upper() for part in (name or "").split("+") if part.split()}


def _link_rows(missions):
    """Missions au format du tableau « Missions liées » et de la fenêtre de
    sélection : textes déjà mis en forme (mêmes formats que la liste des
    OM), affichés tels quels par linked_missions.js."""
    rows = []
    for m in _add_service_times(missions):
        rows.append({
            "id": m["id"],
            "name": m.get("mission_name") or m["reference"],
            "day": day_label(m["mission_date"]),
            "date": fmt_date_long(m["mission_date"]),
            "start": fmt_time(m["service_start"]) or "—",
            "end": fmt_time(m["service_end"]) or "—",
            "driver": f"{m['driver_last_name']} {m['driver_first_name']}",
            "amplitude": fmt_hours_minutes(m["amplitude_minutes"]),
            # Tri du tableau côté navigateur : date, puis prise de service.
            "sort": f"{m['mission_date']} {m['service_start'] or ''}",
            "url": url_for("missions.detail_mission", mission_id=m["id"]),
            "embed_url": url_for("missions.detail_mission", mission_id=m["id"], embed=1),
        })
    return rows


def _linked_rows_for(ids):
    return _link_rows(repo.list_missions(ids=ids, ascending=True)) if ids else []


@bp.route("/a-lier")
def link_candidates():
    """Missions proposées par la fenêtre « Missions liées » (fiche et
    formulaire OM), en JSON : d'abord celles dont le nom commence par le
    même code que la mission en cours (?name=), puis toutes les autres,
    chaque groupe par date et heure de prise de service croissantes.
    ?exclude= écarte la mission elle-même."""
    exclude = request.args.get("exclude", type=int)
    codes = _name_codes(request.args.get("name"))
    rows = _link_rows([m for m in repo.list_missions(ascending=True) if m["id"] != exclude])
    for row in rows:
        row["same_code"] = bool(codes & _name_codes(row["name"]))
    rows.sort(key=lambda row: not row["same_code"])  # tri stable : l'ordre par date tient
    return jsonify({"ok": True, "missions": rows})


@bp.route("/<int:mission_id>/missions-liees", methods=["POST"])
def save_mission_links(mission_id):
    """Enregistre la sélection faite depuis la fiche (dans le formulaire,
    les liens sont enregistrés avec la mission)."""
    if not repo.get_mission(mission_id):
        abort(404)
    repo.set_mission_links(mission_id, _parse_ids(request.form.getlist("linked_mission_ids[]")))
    flash("Missions liées enregistrées.", "success")
    return redirect(_detail_url(mission_id, "#missions-liees"))


@bp.route("/<int:mission_id>/modifier", methods=["GET", "POST"])
def edit_mission(mission_id):
    existing = repo.get_mission(mission_id)
    if not existing:
        abort(404)
    if request.method == "POST":
        data = _mission_form_to_data(request.form)
        error, attachment = _check_form(data)
        if error:
            flash(error, "error")
            data["id"] = mission_id
            data["attachments"] = existing["attachments"]
            return render_template("missions/form.html", is_new=False, mission_id=mission_id,
                                    **_form_context(data))
        repo.update_mission(mission_id, data)
        # Pièces jointes cochées « Retirer » dans le formulaire.
        for attachment_id in _parse_ids(request.form.getlist("remove_attachment_ids[]")):
            att = repo.get_attachment(attachment_id)
            if att and att["mission_id"] == mission_id:
                repo.delete_attachment(attachment_id)
        if attachment:
            repo.add_attachment(mission_id, *attachment)
        flash("Ordre de mission mis à jour.", "success")
        return (_created_return_redirect(mission_id)
                or redirect(url_for("missions.detail_mission", mission_id=mission_id)))
    return render_template("missions/form.html", is_new=False, mission_id=mission_id,
                            **_form_context(existing))


@bp.route("/<int:mission_id>/notes", methods=["POST"])
def save_mission_notes(mission_id):
    """Notes diverses attachées à l'ordre de mission. Réservées aux
    administrateurs (comme toute écriture) et absentes du PDF."""
    mission = repo.get_mission(mission_id)
    if not mission:
        abort(404)
    repo.set_mission_notes(mission_id, request.form.get("notes", "").strip())
    flash("Notes enregistrées.", "success")
    return redirect(_detail_url(mission_id, "#notes"))


@bp.route("/<int:mission_id>/supprimer", methods=["POST"])
def delete_mission(mission_id):
    repo.delete_mission(mission_id)
    flash("Ordre de mission supprimé.", "success")
    return redirect(url_for("missions.list_missions_view"))


@bp.route("/<int:mission_id>/dupliquer", methods=["POST"])
def duplicate_mission(mission_id):
    new_id = repo.duplicate_mission(mission_id)
    if not new_id:
        abort(404)
    flash("Ordre de mission dupliqué — pensez à ajuster la date.", "success")
    return redirect(url_for("missions.edit_mission", mission_id=new_id))


def _return_created_message(start_time):
    replanned = normalize_time(start_time or "")
    if is_valid_time(replanned):
        return (f"Trajet retour créé et lié à l'aller : arrêts inversés et replanifiés à partir de "
                f"{fmt_time(replanned)}, aux mêmes écarts. Vérifiez les trajets — « Générer les "
                f"trajets depuis les arrêts » les refait d'un clic.")
    return "Trajet retour créé et lié à l'aller (arrêts et trajets inversés) — vérifiez date et horaires."


def _created_return_redirect(mission_id):
    """« Enregistrer et créer le retour » : le formulaire vient d'être
    enregistré, la fenêtre a donné date et heure du premier arrêt du retour.
    Renvoie la redirection vers le retour créé, ou None si ce n'est pas ce
    qui a été demandé."""
    # Nom du bouton : envoyé seulement si c'est lui qui a soumis le
    # formulaire, jamais par un « Enregistrer » ordinaire.
    if not request.form.get("create_return"):
        return None
    start_date = request.form.get("create_return_date")
    if not start_date:
        return None
    new_id = repo.create_return_mission(mission_id, start_date=start_date,
                                        start_time=request.form.get("create_return_time"))
    if not new_id:
        return None
    flash(_return_created_message(request.form.get("create_return_time")), "success")
    return redirect(url_for("missions.edit_mission", mission_id=new_id))


@bp.route("/<int:mission_id>/retour", methods=["POST"])
def create_return_mission(mission_id):
    """« Créer le retour » : la fenêtre qui précède demande la date et
    l'heure du premier arrêt du retour, d'où les deux champs."""
    new_id = repo.create_return_mission(mission_id,
                                        start_date=request.form.get("return_date"),
                                        start_time=request.form.get("return_time"))
    if not new_id:
        abort(404)
    flash(_return_created_message(request.form.get("return_time")), "success")
    return redirect(url_for("missions.edit_mission", mission_id=new_id))


@bp.route("/<int:mission_id>/pdf")
def mission_pdf(mission_id):
    mission = repo.get_mission(mission_id)
    if not mission:
        abort(404)
    _require_mission_access(mission)
    try:
        pdf_bytes, filename = generate_mission_pdf(mission_id)
    except PdfGenerationError as e:
        # Aperçu dans la visionneuse (fetch) : l'erreur s'y affiche.
        if wants_json():
            return jsonify({"ok": False, "error": str(e)}), 502
        flash(str(e), "error")
        return redirect(url_for("missions.detail_mission", mission_id=mission_id))
    disposition = "inline" if request.args.get("inline") else "attachment"
    return Response(
        pdf_bytes, mimetype="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@bp.route("/<int:mission_id>/bc.pdf")
def mission_bc_pdf(mission_id):
    """Billet Collectif seul, en téléchargement (« Télécharger BC ») —
    le PDF complet, lui, reste sur /pdf."""
    mission = repo.get_mission(mission_id)
    if not mission:
        abort(404)
    _require_mission_access(mission)
    try:
        pdf_bytes, filename = generate_bc_pdf(mission_id)
    except PdfGenerationError as e:
        flash(str(e), "error")
        return redirect(url_for("missions.detail_mission", mission_id=mission_id))
    return Response(
        pdf_bytes, mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _read_attachment(form, files):
    """Pièce jointe envoyée avec un formulaire — celui de la fiche, ou le
    formulaire OM qui l'enregistre avec la mission. None sans fichier,
    sinon les arguments de repo.add_attachment (hors mission) : nom,
    contenu (pages choisies seulement), type, position. ValueError avec
    le message à afficher si le fichier est refusé."""
    file = files.get("file")
    if not file or not file.filename:
        return None
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_ATTACHMENT_EXT:
        raise ValueError("Formats acceptés : PDF, PNG, JPG.")
    content = file.read()
    if not content:
        raise ValueError("Fichier vide.")

    filename = secure_filename(file.filename)
    pages_spec = form.get("pages", "").strip()
    if ext == ".pdf" and pages_spec:
        try:
            content = extract_pdf_pages(content, pages_spec)
        except PdfGenerationError as e:
            raise ValueError(f"Sélection de pages invalide : {e}")
        filename = f"{Path(filename).stem}_p{pages_spec.replace(',', '+')}.pdf"

    insert_after_page = form.get("insert_after_page", type=int)
    if insert_after_page is None:
        insert_after_page = POSITION_AFTER_OM
    return filename, content, file.mimetype, insert_after_page


@bp.route("/<int:mission_id>/pieces-jointes", methods=["POST"])
def upload_attachment(mission_id):
    mission = repo.get_mission(mission_id)
    if not mission:
        abort(404)
    try:
        attachment = _read_attachment(request.form, request.files)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(_detail_url(mission_id))
    if attachment is None:
        flash("Aucun fichier sélectionné.", "error")
        return redirect(_detail_url(mission_id))
    repo.add_attachment(mission_id, *attachment)
    flash("Pièce jointe ajoutée.", "success")
    return redirect(_detail_url(mission_id))


@bp.route("/<int:mission_id>/pieces-jointes/<int:attachment_id>/supprimer", methods=["POST"])
def delete_attachment(mission_id, attachment_id):
    att = repo.get_attachment(attachment_id)
    if att and att["mission_id"] == mission_id:
        repo.delete_attachment(attachment_id)
        flash("Pièce jointe supprimée.", "success")
    return redirect(_detail_url(mission_id))


@bp.route("/<int:mission_id>/email", methods=["GET", "POST"])
def email_mission(mission_id):
    mission = repo.get_mission(mission_id)
    if not mission:
        abort(404)
    driver = mission["driver"]

    if request.method == "POST":
        to_list = [e.strip() for e in request.form.get("to", "").split(",") if e.strip()]
        cc_raw = request.form.get("cc", "")
        cc_list = [e.strip() for e in cc_raw.split(",") if e.strip()]
        subject = request.form.get("subject", "").strip()
        body = request.form.get("body", "")
        if not to_list or not subject:
            flash("Au moins un destinataire et un objet sont requis.", "error")
            return redirect(url_for("missions.email_mission", mission_id=mission_id))
        try:
            pdf_bytes, filename = generate_mission_pdf(mission_id)
            send_mission_email(mission_id, to_list, cc_list, subject, body, pdf_bytes, filename)
        except (PdfGenerationError, EmailError) as e:
            flash(f"Échec de l'envoi : {e}", "error")
            return redirect(url_for("missions.email_mission", mission_id=mission_id))
        repo.set_mission_status(mission_id, "envoyé")
        repo.mark_sent_driver(mission_id)
        flash(f"Ordre de mission envoyé à {', '.join(to_list)}.", "success")
        return redirect(url_for("missions.detail_mission", mission_id=mission_id))

    default_subject, default_body = _driver_email_defaults(mission)
    return render_template(
        "missions/email.html", mission=mission, driver=driver,
        default_subject=default_subject, default_body=default_body,
    )


def _driver_email_defaults(mission):
    """Objet / corps de l'email envoyé au chauffeur d'une mission. Partagé
    entre l'envoi unitaire (page de rédaction) et l'envoi groupé « chaque
    mission à son chauffeur ». Objet : « Ordre de mission du [jour]
    [dd/mm/YYYY] : [prise de service] - [fin de service] ([nom de
    mission]) »."""
    label = fmt_date_full(mission["mission_date"])
    start, end = service_time_range(mission.get("legs") or [])
    name = mission.get("mission_name") or mission["reference"]
    detail = f" : {fmt_time(start)} - {fmt_time(end)} ({name})" if start and end else f" ({name})"
    subject = f"Ordre de mission du {label}{detail}"
    body = (
        f"Bonjour {mission['driver']['first_name']},\n\n"
        f"Veuillez trouver ci-joint votre ordre de mission et le billet collectif "
        f"pour le {label}{detail}.\n\n"
    )
    if mission["driver"].get("send_itinerary"):
        itinerary_url = build_driver_itinerary_url(mission.get("legs") or [])
        if itinerary_url:
            body += f"Itinéraire : {itinerary_url}\n\n"
    body += f"Cordialement,\n{COMPANY['name']}"
    return subject, body


def _partner_of(mission, cache):
    """Agence d'intérim du chauffeur d'une mission, ou None. `cache` évite
    de relire la même agence pour chaque mission. Une agence effacée entre
    temps est traitée comme « pas d'intérim »."""
    partner_id = (mission["driver"] or {}).get("partner_id")
    if not partner_id:
        return None
    if partner_id not in cache:
        cache[partner_id] = repo.get_partner(partner_id)
    return cache[partner_id]


def _bulk_groups(missions):
    """Missions regroupées par agence d'intérim, dans l'ordre d'apparition
    — un groupe = un email, avec uniquement ses propres missions en pièces
    jointes. Les missions dont le chauffeur n'a pas d'intérim forment un
    groupe de clé 0, sans destinataire pré-rempli."""
    cache, order, par_agence = {}, [], {}
    for m in missions:
        partner = _partner_of(m, cache)
        key = partner["id"] if partner else 0
        if key not in par_agence:
            par_agence[key] = {"key": key, "partner": partner, "missions": []}
            order.append(key)
        par_agence[key]["missions"].append(m)

    groups = []
    for key in order:
        g = par_agence[key]
        subject, body = _bulk_email_defaults(g["missions"], g["partner"])
        g.update({"to": (g["partner"] or {}).get("email") or "", "cc": "",
                  "subject": subject, "body": body})
        groups.append(g)
    return groups


def _groups_from_form(missions, form):
    """Reconstruit les groupes avec ce qui a été saisi, pour ré-afficher la
    page sans perdre les modifications quand un envoi échoue."""
    par_id = {m["id"]: m for m in missions}
    groups = []
    for key in form.getlist("group_keys", type=int):
        ids = form.getlist(f"mission_ids_{key}", type=int)
        partner = repo.get_partner(key) if key else None
        groups.append({
            "key": key, "partner": partner,
            "missions": [par_id[i] for i in ids if i in par_id],
            "to": form.get(f"to_{key}", ""), "cc": form.get(f"cc_{key}", ""),
            "subject": form.get(f"subject_{key}", ""), "body": form.get(f"body_{key}", ""),
        })
    return groups


def _addresses(raw):
    return [e.strip() for e in (raw or "").split(",") if e.strip()]


def _fill_partner_template(text, names, missions_block):
    """Remplace les marqueurs du modèle d'une agence. `str.replace` et non
    `str.format` : le modèle est saisi à la main, une accolade isolée ne
    doit pas faire échouer l'envoi."""
    return ((text or "")
            .replace(chr(13) + chr(10), chr(10))
            .replace("{noms}", names)
            .replace("{missions}", missions_block)
            .replace("{societe}", COMPANY["name"]))


def _bulk_email_defaults(missions, partner=None):
    """Regroupe les missions sélectionnées par chauffeur (ordre
    d'apparition), trie les dates de chacun, et construit l'objet/corps
    par défaut du bouton « Envoyer à l'intérim ». `partner` = l'agence dont
    on applique le modèle ; sans elle, le modèle d'origine (utils.py)."""
    order = []
    groups = {}
    for m in missions:
        driver = m["driver"]
        key = driver["id"]
        if key not in groups:
            groups[key] = {"name": f"{driver['last_name']} {driver['first_name']}", "rows": []}
            order.append(key)
        legs = m.get("legs") or []
        time_range = f"{fmt_time(legs[0]['start_time'])}-{fmt_time(legs[-1]['end_time'])}" if legs else ""
        groups[key]["rows"].append((m["mission_date"], time_range))
    for key in groups:
        groups[key]["rows"].sort(key=lambda r: r[0])

    names = " + ".join(groups[k]["name"] for k in order)

    blocks = []
    for key in order:
        g = groups[key]
        lines = [f"{g['name']} :", ""]
        for mission_date, time_range in g["rows"]:
            row = fmt_date_short(mission_date)
            if time_range:
                row += f" : {time_range}"
            lines.append(row)
        blocks.append("\n".join(lines))
    missions_block = "\n\n".join(blocks)

    partner = partner or {}
    subject = partner.get("email_subject") or DEFAULT_PARTNER_EMAIL_SUBJECT
    body = partner.get("email_body") or DEFAULT_PARTNER_EMAIL_BODY
    return (_fill_partner_template(subject, names, missions_block),
            _fill_partner_template(body, names, missions_block))


@bp.route("/envoi-groupe", methods=["GET", "POST"])
def bulk_email():
    """Sélection multiple sur la liste des missions -> bouton « Envoyer à
    l'intérim ». Les missions sont regroupées par agence d'intérim (celle de
    la fiche Personnel de leur chauffeur) : **un email par agence**, avec
    seulement ses propres missions en pièces jointes (un PDF par mission).
    Destinataire et modèle viennent de la fiche de l'agence (écran
    Partenaires), et restent modifiables avant l'envoi."""
    ids = (request.form if request.method == "POST" else request.args).getlist("mission_ids", type=int)
    missions = [m for m in (repo.get_mission(i) for i in ids) if m]
    if not missions:
        flash("Sélectionnez au moins un ordre de mission.", "error")
        return redirect(url_for("missions.list_missions_view"))

    # Même règle que le bouton de la liste, qui se désactive dans ce cas :
    # sans agence sur la fiche du chauffeur, l'envoi n'a pas de
    # destinataire. Contrôlé ici aussi, l'URL étant atteignable à la main.
    orphelines = [m for m in missions if not (m["driver"] or {}).get("partner_id")]
    if orphelines:
        noms = ", ".join(m.get("mission_name") or m["reference"] for m in orphelines)
        flash(f"Pas d'intérim sur la fiche du chauffeur pour : {noms}. "
              "Renseignez-le dans Ressources → Personnel, ou retirez ces missions "
              "de la sélection.", "error")
        return redirect(url_for("missions.list_missions_view"))

    def page(groups):
        return render_template("missions/bulk_email.html", missions=missions,
                               mission_ids=ids, groups=groups)

    if request.method != "POST":
        return page(_bulk_groups(missions))

    groups = _groups_from_form(missions, request.form)
    incomplets = [g for g in groups if not _addresses(g["to"]) or not g["subject"].strip()]
    if incomplets:
        for g in incomplets:
            nom = g["partner"]["name"] if g["partner"] else "les missions sans intérim"
            flash(f"Destinataire et objet sont requis pour {nom}.", "error")
        return page(groups)

    # Chaque agence est envoyée indépendamment : l'échec de l'une ne prive
    # pas les autres de leur email, et seules les missions réellement
    # parties sont marquées comme envoyées.
    envoyes, echecs = [], []
    for g in groups:
        group_ids = [m["id"] for m in g["missions"]]
        nom = g["partner"]["name"] if g["partner"] else "sans intérim"
        to_list = _addresses(g["to"])
        try:
            attachments = [generate_mission_pdf(m["id"]) for m in g["missions"]]
            send_bulk_email(group_ids, to_list, _addresses(g["cc"]),
                            g["subject"].strip(),
                            g["body"].replace(chr(13) + chr(10), chr(10)), attachments)
        except (PdfGenerationError, EmailError) as e:
            echecs.append(f"{nom} : {e}")
            continue
        for mission_id in group_ids:
            repo.mark_sent_randstad(mission_id)
        envoyes.append(f"{nom} → {', '.join(to_list)} "
                       f"({len(group_ids)} OM)")

    if envoyes:
        flash(f"{len(envoyes)} email(s) envoyé(s) : {' ; '.join(envoyes)}.", "success")
    if echecs:
        flash(f"Échec de l'envoi : {' ; '.join(echecs)}.", "error")
        return page(groups)
    return redirect(url_for("missions.list_missions_view"))


def _reply_subject(subject):
    """« Re: » en tête de l'objet, sans l'empiler si l'objet en porte déjà
    un : c'est ce que les clients de messagerie attendent pour rattacher
    deux messages au même fil."""
    subject = (subject or "").strip()
    return subject if subject[:3].lower() == "re:" else f"Re: {subject}"


@bp.route("/<int:mission_id>/envoyer-itineraire", methods=["POST"])
def send_itinerary(mission_id):
    """Bouton « Envoyer l'itinéraire » de la fiche mission, proposé dès que
    l'ordre de mission est parti chez le chauffeur. Envoie le lien Google
    Maps de la mission en réponse à cet email-là (même objet précédé de
    « Re: », et In-Reply-To quand le mode d'envoi le permet — voir
    email_service) pour que le chauffeur retrouve les deux au même endroit.

    Indépendant de la case « Envoyer l'itinéraire » de la fiche du
    chauffeur : celle-ci ne décide que de la ligne ajoutée d'office à
    l'email d'ordre de mission."""
    mission = repo.get_mission(mission_id)
    if not mission:
        abort(404)
    retour = redirect(_detail_url(mission_id))

    if not mission.get("sent_driver_at"):
        flash("L'ordre de mission n'a pas encore été envoyé au chauffeur : "
              "envoyez-le d'abord, l'itinéraire viendra en réponse.", "error")
        return retour

    driver = mission["driver"]
    to = (driver.get("email") or "").strip()
    if not to:
        flash(f"{driver['last_name']} {driver['first_name']} n'a pas d'email sur sa fiche.", "error")
        return retour

    itinerary_url = build_driver_itinerary_url(mission.get("legs") or [])
    if not itinerary_url:
        flash("Pas d'itinéraire exploitable : il faut au moins deux lieux de conduite "
              "dans les trajets de la mission.", "error")
        return retour

    # Objet repris de l'email réellement parti au chauffeur, pour tomber
    # dans le même fil ; à défaut (journal purgé), celui qu'on aurait mis.
    previous = repo.last_email_to(mission_id, to)
    base_subject = (previous or {}).get("subject") or _driver_email_defaults(mission)[0]
    label = fmt_date_full(mission["mission_date"])
    body = (
        f"Bonjour {driver['first_name']},\n\n"
        f"Voici l'itinéraire de votre mission du {label}.\n\n"
        f"Itinéraire : {itinerary_url}\n\n"
        f"Cordialement,\n{COMPANY['name']}"
    )
    try:
        send_followup_email(
            mission_id, [to], _reply_subject(base_subject), body,
            in_reply_to=(previous or {}).get("message_id"),
            reply_to_graph_id=(previous or {}).get("provider_message_id"),
        )
    except EmailError as e:
        flash(f"Échec de l'envoi de l'itinéraire : {e}", "error")
        return retour

    flash(f"Itinéraire envoyé à {to}, en réponse à son ordre de mission.", "success")
    return retour


@bp.route("/<int:mission_id>/prix-visibilite", methods=["POST"])
def toggle_price_visibility(mission_id):
    """Bascule « prix affiché / masqué » de la fiche mission. Masqué, le
    prix disparaît du PDF (pour tout le monde : c'est le document qui part
    au chauffeur et à l'agence) et des écrans pour les non-administrateurs.
    Un administrateur continue de le voir dans l'application.

    Route hors DRIVER_ENDPOINTS : un chauffeur ne peut pas l'appeler."""
    mission = repo.get_mission(mission_id)
    if not mission:
        abort(404)
    hidden = not mission.get("price_hidden")
    repo.set_price_hidden(mission_id, hidden)
    flash("Prix masqué : il n'apparaîtra plus sur le PDF ni pour les chauffeurs."
          if hidden else "Prix affiché : il réapparaît sur le PDF et pour les chauffeurs.",
          "success")
    return redirect(_detail_url(mission_id))


@bp.route("/archiver", methods=["POST"])
def archive_missions():
    """Sélection multiple de l'onglet « Missions passées » -> bouton
    « Archiver » : les missions sortent des onglets À venir / Missions
    passées et basculent dans « Missions archivées », d'où le bouton
    « Désarchiver » les ramène. Rien n'est supprimé, et le planning
    continue de les afficher."""
    ids = request.form.getlist("mission_ids", type=int)
    archiver = request.form.get("archived") != "0"
    retour = url_for("missions.list_missions_view",
                     tab=request.form.get("tab") or "past")
    if not ids:
        flash("Sélectionnez au moins un ordre de mission.", "error")
        return redirect(retour)

    changed = repo.set_missions_archived(ids, archiver)
    verbe = "archivé" if archiver else "désarchivé"
    if changed:
        flash(f"{changed} ordre(s) de mission {verbe}(s).", "success")
    else:
        flash(f"Aucun ordre de mission à {'archiver' if archiver else 'désarchiver'} "
              "dans la sélection.", "error")
    return redirect(retour)


@bp.route("/envoi-chauffeurs", methods=["POST"])
def bulk_email_drivers():
    """Sélection multiple -> un email distinct par mission, adressé au
    chauffeur de cette mission (contrairement à l'envoi à l'intérim qui
    regroupe tout dans un seul email)."""
    ids = request.form.getlist("mission_ids", type=int)
    missions = [m for m in (repo.get_mission(i) for i in ids) if m]
    if not missions:
        flash("Sélectionnez au moins un ordre de mission.", "error")
        return redirect(url_for("missions.list_missions_view"))

    sent, skipped, failed = [], [], []
    for m in missions:
        driver = m["driver"]
        to = (driver.get("email") or "").strip()
        if not to:
            skipped.append(f"{m['reference']} ({driver['last_name']} : pas d'email)")
            continue
        subject, body = _driver_email_defaults(m)
        try:
            pdf_bytes, filename = generate_mission_pdf(m["id"])
            send_mission_email(m["id"], [to], [], subject, body, pdf_bytes, filename)
        except (PdfGenerationError, EmailError) as e:
            failed.append(f"{m['reference']} : {e}")
            continue
        repo.set_mission_status(m["id"], "envoyé")
        repo.mark_sent_driver(m["id"])
        sent.append(f"{m['reference']} → {to}")

    if sent:
        flash(f"{len(sent)} email(s) envoyé(s) : {', '.join(sent)}.", "success")
    if skipped:
        flash(f"Ignoré(s), chauffeur sans email : {', '.join(skipped)}.", "error")
    if failed:
        flash(f"Échec(s) : {'; '.join(failed)}.", "error")
    return redirect(url_for("missions.list_missions_view"))
