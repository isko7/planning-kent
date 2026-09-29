from flask import Blueprint, render_template, request, redirect, url_for, flash, Response

from app import repo
from app.pdf_service import render_template_string, build_om_context, build_bc_context, PdfGenerationError
from app.utils import fmt_time, fmt_date_short, fmt_date_long, day_label
from app.config import COMPANY, OM_LEGAL_REF, BC_LEGAL_REF
from app.seeding import refresh_default_templates

bp = Blueprint("templates_admin", __name__, url_prefix="/templates")

DEMO_MISSION = {
    "driver": {"last_name": "MARTIN", "first_name": "Yannis"},
    "client": {"name": "Simplon Voyages", "address": "39 Route de la Libération",
               "postal_code": "41240", "city": "BEAUCE LA ROMAINE", "phone": "06.60.41.54.58"},
    "mission_date": "2026-09-15",
    "mission_name": "Navette aéroport",
    "shuttle_label": "3",
    "emission_date": "2026-09-01",
    "motif": "Transport Occasionnel",
    "remarks": "Exemple de remarque.",
    "price": "480 €",
    "show_price": True,
    "legs": [
        {"start_time": "11:00", "end_time": "11:00", "vehicle_plate": "FK-066-ME",
         "label": "Prise de service - Dépôt KENT", "is_checkpoint": True},
        {"start_time": "11:00", "end_time": "13:30", "vehicle_plate": "FK-066-ME",
         "label": "Dépôt KENT → Roissy CDG 2B", "is_checkpoint": False},
        {"start_time": "13:30", "end_time": "14:00", "vehicle_plate": None,
         "label": "Pause 30min", "is_checkpoint": False},
        {"start_time": "14:00", "end_time": "14:15", "vehicle_plate": None,
         "label": "Relais chauffeur", "is_checkpoint": False, "is_relay": True},
        {"start_time": "14:15", "end_time": "15:50", "vehicle_plate": "FK-066-ME",
         "label": "Roissy CDG 2B → Chartres", "is_checkpoint": False},
        {"start_time": "20:30", "end_time": "20:30", "vehicle_plate": "FK-066-ME",
         "label": "Fin de service - Dépôt KENT", "is_checkpoint": True},
    ],
    "stops": [
        {"stop_type": "prise_en_charge", "stop_date": "2026-09-15", "stop_time": "13:00",
         "address": "Aéroport Roissy CDG 2B", "city": "", "passenger_count": 7},
        {"stop_type": "depose", "stop_date": "2026-09-15", "stop_time": "15:50",
         "address": "13 Bis Route de Voves", "city": "CHARTRES", "passenger_count": 1},
        {"stop_type": "depose", "stop_date": "2026-09-15", "stop_time": "18:30",
         "address": "18 Rue de la Gare", "city": "VALENCAY", "passenger_count": 1},
    ],
}


@bp.route("/")
def list_templates_view():
    om_templates = repo.list_templates("OM")
    bc_templates = repo.list_templates("BC")
    return render_template("templates_admin/list.html", om_templates=om_templates, bc_templates=bc_templates)


@bp.route("/rafraichir", methods=["POST"])
def refresh_defaults():
    """Recharge le HTML des templates « … — standard » depuis
    app/templates_data/ (utile après une mise à jour de l'appli). N'écrase
    que ces deux templates-là, pas vos copies personnalisées."""
    actions = refresh_default_templates()
    flash("Templates par défaut mis à jour : " + ", ".join(actions), "success")
    return redirect(url_for("templates_admin.list_templates_view"))


@bp.route("/<int:template_id>", methods=["GET", "POST"])
def edit_template(template_id):
    tpl = repo.get_template(template_id)
    if not tpl:
        flash("Template introuvable.", "error")
        return redirect(url_for("templates_admin.list_templates_view"))
    if request.method == "POST":
        name = request.form.get("name", "").strip() or tpl["name"]
        definition_html = request.form.get("definition_html", "")
        repo.update_template(template_id, name, definition_html)
        flash("Template enregistré.", "success")
        return redirect(url_for("templates_admin.edit_template", template_id=template_id))
    return render_template("templates_admin/edit.html", tpl=tpl)


@bp.route("/<int:template_id>/activer", methods=["POST"])
def activate_template(template_id):
    repo.activate_template(template_id)
    flash("Template activé.", "success")
    return redirect(url_for("templates_admin.list_templates_view"))


@bp.route("/<int:template_id>/dupliquer", methods=["POST"])
def duplicate_template(template_id):
    tpl = repo.get_template(template_id)
    if not tpl:
        flash("Template introuvable.", "error")
        return redirect(url_for("templates_admin.list_templates_view"))
    new_id = repo.create_template(tpl["type"], tpl["name"] + " (copie)", tpl["definition_html"])
    flash("Copie créée, vous pouvez la modifier librement.", "success")
    return redirect(url_for("templates_admin.edit_template", template_id=new_id))


@bp.route("/<int:template_id>/apercu", methods=["GET", "POST"])
def preview_template(template_id):
    tpl = repo.get_template(template_id)
    if not tpl:
        return "Template introuvable", 404
    # En GET : aperçu de la version enregistrée. En POST (bouton "Aperçu" du
    # formulaire d'édition) : aperçu du brouillon en cours, sans l'enregistrer.
    definition_html = request.form.get("definition_html") if request.method == "POST" else None
    definition_html = definition_html or tpl["definition_html"]
    context = build_om_context(DEMO_MISSION) if tpl["type"] == "OM" else build_bc_context(DEMO_MISSION)
    try:
        pdf_bytes = render_template_string(definition_html, context)
    except PdfGenerationError as e:
        return f"Erreur de génération : {e}", 400
    return Response(pdf_bytes, mimetype="application/pdf")
