"""
Couche d'accès aux données : une fonction par opération, en SQL brut.

Pas d'ORM (pour rester sur des dépendances minimales) : le schéma est
assez simple pour que ce soit lisible tel quel. Le curseur MySQL renvoie
déjà des dict (voir app/db.py) ; `row_to_dict` / `rows_to_dicts` restent
là pour découpler les templates du pilote.
"""
import re
from datetime import datetime, date
from app.config import PINNED_CLIENT_NAME
from app.db import get_db
from app.utils import (legs_time_summary, now_paris, parse_iso_date,
                       reschedule_stops, retime_legs_from_stops)


def row_to_dict(row):
    return dict(row) if row is not None else None


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


def now_iso():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------- personnel (table crew)
def list_drivers(include_inactive=True):
    with get_db() as db:
        q = "SELECT * FROM crew"
        if not include_inactive:
            q += " WHERE active = 1"
        q += " ORDER BY last_name, first_name"
        return rows_to_dicts(db.execute(q).fetchall())


def get_driver(driver_id):
    with get_db() as db:
        return row_to_dict(db.execute("SELECT * FROM crew WHERE id = ?", (driver_id,)).fetchone())


def create_driver(data):
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO crew (last_name, first_name, email, phone, license_number, active, color,
               send_itinerary, can_login, is_admin, must_change_password, username,
               password_hash, notes, remarks, partner_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data["last_name"], data["first_name"], data["email"], data.get("phone"),
             data.get("license_number"), 1 if data.get("active", True) else 0,
             data.get("color") or None, 1 if data.get("send_itinerary") else 0,
             1 if data.get("can_login") else 0, 1 if data.get("is_admin") else 0,
             1 if data.get("must_change_password") else 0, data.get("username") or None,
             data.get("password_hash") or None, data.get("notes"), data.get("remarks"),
             data.get("partner_id") or None),
        )
        return cur.lastrowid


def update_driver(driver_id, data):
    """`password_hash` absent du dict = mot de passe inchangé (le formulaire
    laisse le champ vide quand on ne veut pas le remplacer)."""
    fields = ["last_name=?", "first_name=?", "email=?", "phone=?", "license_number=?",
              "active=?", "color=?", "send_itinerary=?", "can_login=?", "is_admin=?",
              "username=?", "notes=?", "remarks=?", "partner_id=?", "updated_at=?"]
    params = [data["last_name"], data["first_name"], data["email"], data.get("phone"),
              data.get("license_number"), 1 if data.get("active", True) else 0,
              data.get("color") or None, 1 if data.get("send_itinerary") else 0,
              1 if data.get("can_login") else 0, 1 if data.get("is_admin") else 0,
              data.get("username") or None, data.get("notes"), data.get("remarks"),
              data.get("partner_id") or None, now_iso()]
    if "password_hash" in data:
        fields.insert(-1, "password_hash=?")
        params.insert(-1, data.get("password_hash") or None)
    if "must_change_password" in data:
        fields.insert(-1, "must_change_password=?")
        params.insert(-1, 1 if data.get("must_change_password") else 0)
    with get_db() as db:
        db.execute(
            "UPDATE crew SET " + ", ".join(fields) + " WHERE id=?",
            tuple(params) + (driver_id,),
        )


def delete_driver(driver_id):
    with get_db() as db:
        db.execute("DELETE FROM crew WHERE id = ?", (driver_id,))


# ----------------------------------------------- accès à l'application
def get_driver_by_username(username):
    """Fiche chauffeur portant cet identifiant de connexion, ou None.
    L'identifiant est comparé sans tenir compte de la casse."""
    if not username:
        return None
    with get_db() as db:
        return row_to_dict(db.execute(
            "SELECT * FROM crew WHERE LOWER(username) = LOWER(?)", (username,)
        ).fetchone())


def username_taken(username, exclude_driver_id=None):
    """Vrai si l'identifiant est déjà utilisé par un *autre* chauffeur."""
    other = get_driver_by_username(username)
    return bool(other) and other["id"] != exclude_driver_id


def count_drivers_with_login():
    """Nombre de comptes réellement utilisables pour se connecter. Un
    chauffeur inactif ou sans mot de passe ne compte pas : sinon, retirer
    l'accès au dernier compte actif passerait le garde-fou de l'écran
    Chauffeurs et fermerait l'application à tout le monde."""
    with get_db() as db:
        row = db.execute(
            """SELECT COUNT(*) AS n FROM crew
               WHERE can_login = 1 AND active = 1 AND password_hash IS NOT NULL"""
        ).fetchone()
        return int(row["n"]) if row else 0


def find_driver_by_name(last_name, first_name):
    with get_db() as db:
        return row_to_dict(db.execute(
            """SELECT * FROM crew WHERE LOWER(last_name) = LOWER(?)
               AND LOWER(first_name) = LOWER(?) LIMIT 1""",
            (last_name, first_name),
        ).fetchone())


def grant_login(driver_id, username, password_hash):
    """Amorçage : donne l'accès ET les droits d'administration. C'est le
    compte de secours, il doit pouvoir rouvrir l'accès aux autres."""
    with get_db() as db:
        db.execute(
            """UPDATE crew SET can_login=1, is_admin=1, must_change_password=0,
               username=?, password_hash=?, active=1, updated_at=? WHERE id=?""",
            (username, password_hash, now_iso(), driver_id),
        )


def set_mission_notes(mission_id, notes):
    """Notes libres d'un ordre de mission. Écrites depuis sa fiche
    uniquement, et jamais reprises dans le PDF."""
    with get_db() as db:
        db.execute("UPDATE missions SET notes=?, updated_at=? WHERE id=?",
                   (notes or None, now_iso(), mission_id))


def set_personal_notes(driver_id, notes):
    """Bloc-notes personnel, propre à chaque compte."""
    with get_db() as db:
        db.execute("UPDATE crew SET personal_notes=?, updated_at=? WHERE id=?",
                   (notes or None, now_iso(), driver_id))


def set_admin(driver_id, is_admin):
    """Donne ou retire les droits d'administration, sans rien changer
    d'autre (ni identifiant, ni mot de passe)."""
    with get_db() as db:
        db.execute(
            "UPDATE crew SET is_admin=?, updated_at=? WHERE id=?",
            (1 if is_admin else 0, now_iso(), driver_id),
        )


def count_admins():
    """Administrateurs réellement capables de se connecter. Sert à refuser
    le retrait du dernier d'entre eux (plus personne ne pourrait gérer les
    accès, ni même rouvrir le sien)."""
    with get_db() as db:
        row = db.execute(
            """SELECT COUNT(*) AS n FROM crew
               WHERE is_admin = 1 AND can_login = 1 AND active = 1
               AND password_hash IS NOT NULL"""
        ).fetchone()
        return int(row["n"]) if row else 0


def set_password(driver_id, password_hash, must_change=False):
    """Change le mot de passe d'une fiche sans toucher au reste."""
    with get_db() as db:
        db.execute(
            """UPDATE crew SET password_hash=?, must_change_password=?, updated_at=?
               WHERE id=?""",
            (password_hash, 1 if must_change else 0, now_iso(), driver_id),
        )


# --------------------------------------------------------------- vehicles
def list_vehicles(include_inactive=True):
    with get_db() as db:
        q = "SELECT * FROM vehicles"
        if not include_inactive:
            q += " WHERE active = 1"
        q += " ORDER BY name, plate"
        return rows_to_dicts(db.execute(q).fetchall())


def get_vehicle(vehicle_id):
    with get_db() as db:
        return row_to_dict(db.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone())


def create_vehicle(data):
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO vehicles (name, plate, seats, active, notes, remarks,
               technical_control_date, maintenance_date, last_maintenance_km)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.get("name"), data["plate"], data.get("seats") or None,
             1 if data.get("active", True) else 0, data.get("notes"), data.get("remarks"),
             data.get("technical_control_date") or None, data.get("maintenance_date") or None,
             data.get("last_maintenance_km")),
        )
        return cur.lastrowid


def update_vehicle(vehicle_id, data):
    with get_db() as db:
        db.execute(
            """UPDATE vehicles SET name=?, plate=?, seats=?, active=?, notes=?, remarks=?,
               technical_control_date=?, maintenance_date=?, last_maintenance_km=?,
               updated_at=? WHERE id=?""",
            (data.get("name"), data["plate"], data.get("seats") or None,
             1 if data.get("active", True) else 0, data.get("notes"), data.get("remarks"),
             data.get("technical_control_date") or None, data.get("maintenance_date") or None,
             data.get("last_maintenance_km"), now_iso(), vehicle_id),
        )


def delete_vehicle(vehicle_id):
    with get_db() as db:
        db.execute("DELETE FROM vehicles WHERE id = ?", (vehicle_id,))


def count_vehicles_ct_due(deadline):
    """Nombre de véhicules dont le contrôle technique arrive à échéance au
    plus tard le `deadline` ('YYYY-MM-DD'), retards compris : c'est la
    pastille rouge du menu Véhicules (voir app/__init__.py). Les véhicules
    inactifs comptent aussi — la liste les signale de la même façon."""
    with get_db() as db:
        row = db.execute(
            """SELECT COUNT(*) AS n FROM vehicles
               WHERE technical_control_date IS NOT NULL AND technical_control_date <> ''
                 AND technical_control_date <= ?""",
            (deadline,),
        ).fetchone()
        return int(row["n"]) if row else 0


# ---------------------------------------------------------------- clients
def list_clients(pinned_first=False):
    """pinned_first : remonte PINNED_CLIENT_NAME en tête (menu déroulant du
    formulaire de mission) ; le reste reste alphabétique."""
    with get_db() as db:
        if pinned_first and PINNED_CLIENT_NAME:
            return rows_to_dicts(db.execute(
                "SELECT * FROM clients ORDER BY (name = ?) DESC, name", (PINNED_CLIENT_NAME,)
            ).fetchall())
        return rows_to_dicts(db.execute("SELECT * FROM clients ORDER BY name").fetchall())


def get_client(client_id):
    with get_db() as db:
        return row_to_dict(db.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone())


def create_client(data):
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO clients (name, address, postal_code, city, phone, email, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (data["name"], data.get("address"), data.get("postal_code"), data.get("city"),
             data.get("phone"), data.get("email"), data.get("notes")),
        )
        return cur.lastrowid


def update_client(client_id, data):
    with get_db() as db:
        db.execute(
            """UPDATE clients SET name=?, address=?, postal_code=?, city=?, phone=?, email=?,
               notes=?, updated_at=? WHERE id=?""",
            (data["name"], data.get("address"), data.get("postal_code"), data.get("city"),
             data.get("phone"), data.get("email"), data.get("notes"), now_iso(), client_id),
        )


def delete_client(client_id):
    with get_db() as db:
        db.execute("DELETE FROM clients WHERE id = ?", (client_id,))


# ------------------------------------------------------------- partenaires
# Agences d'interim : fournissent le destinataire et le modele d'email de
# l'envoi groupe (« Envoyer a l'interim »), et se rattachent aux fiches
# Personnel par crew.partner_id.
def list_partners():
    """Triees par `position` : l'ordre voulu au menu deroulant d'une fiche
    Personnel. A position egale (agences ajoutees ensuite), alphabetique."""
    with get_db() as db:
        return rows_to_dicts(db.execute(
            "SELECT * FROM partners ORDER BY position, name"
        ).fetchall())


def get_partner(partner_id):
    if not partner_id:
        return None
    with get_db() as db:
        return row_to_dict(db.execute(
            "SELECT * FROM partners WHERE id = ?", (partner_id,)
        ).fetchone())


def create_partner(data):
    with get_db() as db:
        row = db.execute("SELECT COALESCE(MAX(position), 0) AS p FROM partners").fetchone()
        position = data.get("position")
        if position is None:
            position = int(row["p"]) + 1
        cur = db.execute(
            """INSERT INTO partners (name, phone, email, email_subject, email_body, position)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (data["name"], data.get("phone"), data.get("email"),
             data.get("email_subject"), data.get("email_body"), position),
        )
        return cur.lastrowid


def update_partner(partner_id, data):
    with get_db() as db:
        db.execute(
            """UPDATE partners SET name=?, phone=?, email=?, email_subject=?, email_body=?,
               position=?, updated_at=? WHERE id=?""",
            (data["name"], data.get("phone"), data.get("email"), data.get("email_subject"),
             data.get("email_body"), data.get("position") or 0, now_iso(), partner_id),
        )


def delete_partner(partner_id):
    """Detache d'abord les fiches Personnel qui pointaient dessus : sans
    cela leur menu « Interim » afficherait un partenaire disparu (il n'y a
    pas de FOREIGN KEY, voir l'entete de db.py)."""
    with get_db() as db:
        db.execute("UPDATE crew SET partner_id = NULL WHERE partner_id = ?", (partner_id,))
        db.execute("DELETE FROM partners WHERE id = ?", (partner_id,))


def count_crew_by_partner(partner_id):
    with get_db() as db:
        row = db.execute(
            "SELECT COUNT(*) AS n FROM crew WHERE partner_id = ?", (partner_id,)
        ).fetchone()
        return int(row["n"]) if row else 0


# -------------------------------------------------------------- templates
def list_templates(type_=None):
    with get_db() as db:
        if type_:
            rows = db.execute(
                "SELECT * FROM templates WHERE type = ? ORDER BY updated_at DESC", (type_,)
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM templates ORDER BY type, updated_at DESC").fetchall()
        return rows_to_dicts(rows)


def get_template(template_id):
    with get_db() as db:
        return row_to_dict(db.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone())


def get_active_template(type_):
    with get_db() as db:
        return row_to_dict(
            db.execute(
                "SELECT * FROM templates WHERE type = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
                (type_,),
            ).fetchone()
        )


def create_template(type_, name, definition_html, activate=False):
    with get_db() as db:
        if activate:
            db.execute("UPDATE templates SET is_active = 0 WHERE type = ?", (type_,))
        cur = db.execute(
            """INSERT INTO templates (type, name, is_active, definition_html, version)
               VALUES (?, ?, ?, ?, 1)""",
            (type_, name, 1 if activate else 0, definition_html),
        )
        return cur.lastrowid


def update_template(template_id, name, definition_html):
    with get_db() as db:
        db.execute(
            """UPDATE templates SET name=?, definition_html=?, version=version+1, updated_at=?
               WHERE id=?""",
            (name, definition_html, now_iso(), template_id),
        )


def activate_template(template_id):
    with get_db() as db:
        row = db.execute("SELECT type FROM templates WHERE id=?", (template_id,)).fetchone()
        if not row:
            return
        db.execute("UPDATE templates SET is_active = 0 WHERE type = ?", (row["type"],))
        db.execute("UPDATE templates SET is_active = 1 WHERE id = ?", (template_id,))


# --------------------------------------------------------------- missions
def _next_reference(db, mission_date_str):
    # Basé sur le suffixe numérique max existant, pas un COUNT(*) : un
    # COUNT se désynchronise dès qu'une mission est supprimée (le numéro
    # libéré redevient "disponible" alors qu'un numéro plus élevé existe
    # déjà -> collision sur la contrainte UNIQUE à l'INSERT suivant).
    year = mission_date_str[:4] if mission_date_str else str(date.today().year)
    prefix = f"OM-{year}-"
    rows = db.execute(
        "SELECT reference FROM missions WHERE reference LIKE ?", (f"{prefix}%",)
    ).fetchall()
    max_n = 0
    for row in rows:
        suffix = (row["reference"] or "")[len(prefix):]
        if suffix.isdigit():
            max_n = max(max_n, int(suffix))
    return f"{prefix}{max_n + 1:04d}"


_MISSIONS_FROM = """FROM missions m
       JOIN crew d ON d.id = m.driver_id
       LEFT JOIN clients c ON c.id = m.client_id
       WHERE 1=1"""


def _missions_filters(driver_id, date_from, date_to, status, name, ids=None,
                      service_cutoff=None, archived=None):
    """Fragment WHERE + paramètres, partagé par list_missions() et
    count_missions() pour que le total de la pagination corresponde
    exactement aux lignes affichées.

    `archived` : False = missions actives seulement, True = archivées
    seulement, None = les deux (comportement de tous les appels qui ne s'en
    préoccupent pas, dont le planning)."""
    q, params = "", []
    if archived is True:
        q += " AND m.archived_at IS NOT NULL"
    elif archived is False:
        q += " AND m.archived_at IS NULL"
    if ids is not None:
        # Liste vide : aucune mission (« IN () » serait invalide en SQL).
        q += f" AND m.id IN ({','.join(['?'] * len(ids))})" if ids else " AND 1=0"
        params.extend(ids)
    if driver_id:
        q += " AND m.driver_id = ?"
        params.append(driver_id)
    if date_from:
        q += " AND m.mission_date >= ?"
        params.append(date_from)
    if date_to:
        q += " AND m.mission_date <= ?"
        params.append(date_to)
    if status:
        q += " AND m.status = ?"
        params.append(status)
    if name:
        # Le joker % est laissé à la main de l'utilisateur ; sans joker,
        # on cherche « contient » (comportement attendu par défaut).
        q += " AND m.mission_name LIKE ?"
        params.append(name if "%" in name else f"%{name}%")
    if service_cutoff:
        cutoff_sql, cutoff_params = _service_cutoff(*service_cutoff)
        q += cutoff_sql
        params.extend(cutoff_params)
    return q, params


def _sql_time(column):
    """Heure d'un trajet ramenée au format H:MM en SQL (« 9h30 », « 9 h 30 »
    -> « 9:30 ») : les heures saisies avant la normalisation à
    l'enregistrement (utils.normalize_time) sont restées telles quelles."""
    return f"REPLACE(REPLACE(LOWER({column}), ' ', ''), 'h', ':')"


# Pas de « ? » dans le motif (« [01]?[0-9] ») : db.py le prendrait pour un
# paramètre, d'où l'alternative à trois branches.
_SQL_VALID_TIME = "REGEXP '^(2[0-3]|[01][0-9]|[0-9]):[0-5][0-9]$'"

# Heure de prise de service, pour trier la liste des OM en base (le tri doit
# précéder la pagination) : début du premier trajet, dans l'ordre, dont les
# deux heures sont valides — même règle que utils.service_time_range().
# Zéro-paddée pour que « 9:30 » passe avant « 10:00 » ; NULL sans horaire.
_SQL_SERVICE_START = f"""(SELECT LPAD({_sql_time('l.start_time')}, 5, '0')
       FROM mission_legs l
       WHERE l.mission_id = m.id
         AND {_sql_time('l.start_time')} {_SQL_VALID_TIME}
         AND {_sql_time('l.end_time')} {_SQL_VALID_TIME}
       ORDER BY l.position LIMIT 1)"""


# Fin de service, même règle que _SQL_SERVICE_START mais sur le DERNIER
# trajet horodaté : sert à décider si une mission du jour est déjà passée.
_SQL_SERVICE_END = f"""(SELECT LPAD({_sql_time('l.end_time')}, 5, '0')
       FROM mission_legs l
       WHERE l.mission_id = m.id
         AND {_sql_time('l.start_time')} {_SQL_VALID_TIME}
         AND {_sql_time('l.end_time')} {_SQL_VALID_TIME}
       ORDER BY l.position DESC LIMIT 1)"""


def _service_cutoff(mode, today, now_hm):
    """Fragment WHERE séparant les missions passées des missions à venir à
    la minute près (et non à la journée) : une mission du jour n'est passée
    qu'une fois sa fin de service dépassée.

    `mode` : 'past' ou 'current'. Les deux fragments sont strictement
    complémentaires, une mission apparaît donc dans un onglet et un seul.
    Une mission du jour sans horaire exploitable, ou qui se termine le
    lendemain (fin < début, mission de nuit), reste « à venir »."""
    end, start = _SQL_SERVICE_END, _SQL_SERVICE_START
    if mode == "past":
        q = (f" AND (m.mission_date < ? OR (m.mission_date = ? AND {end} IS NOT NULL"
             f" AND {end} >= {start} AND {end} < ?))")
    else:
        q = (f" AND (m.mission_date > ? OR (m.mission_date = ? AND ({end} IS NULL"
             f" OR {end} < {start} OR {end} >= ?)))")
    return q, [today, today, now_hm]


def count_missions(driver_id=None, date_from=None, date_to=None, status=None, name=None,
                   service_cutoff=None, archived=None):
    where, params = _missions_filters(driver_id, date_from, date_to, status, name,
                                      service_cutoff=service_cutoff, archived=archived)
    with get_db() as db:
        return db.execute(f"SELECT COUNT(*) AS c {_MISSIONS_FROM}{where}", params).fetchone()["c"]


def list_missions(driver_id=None, date_from=None, date_to=None, status=None, name=None,
                  ascending=False, limit=None, offset=0, ids=None, service_cutoff=None,
                  archived=None):
    where, params = _missions_filters(driver_id, date_from, date_to, status, name, ids,
                                      service_cutoff=service_cutoff, archived=archived)
    # driver_partner_id : la liste s'en sert pour savoir si l'envoi a
    # l'interim est possible pour cette mission (chauffeur rattache a une
    # agence ou non).
    q = f"""SELECT m.*, d.last_name AS driver_last_name, d.first_name AS driver_first_name,
                   d.partner_id AS driver_partner_id, c.name AS client_name
            {_MISSIONS_FROM}{where}"""
    # Un même jour se lit toujours dans l'ordre des prises de service, quel
    # que soit le sens des dates ; les missions sans horaire en tête (NULL
    # d'abord), comme « toute la journée » sur le planning.
    q += (f" ORDER BY m.mission_date {'ASC' if ascending else 'DESC'},"
          f" {_SQL_SERVICE_START} ASC, m.id ASC")
    if limit is not None:
        q += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    with get_db() as db:
        return rows_to_dicts(db.execute(q, params).fetchall())


def attach_legs(missions):
    """Complète chaque mission de `missions` avec sa liste de trajets
    (`m['legs']`), en une seule requête IN plutôt qu'un aller-retour par
    mission (évite le N+1) — utilisé par la liste des OM (heures de
    service) et le planning/calendrier."""
    if not missions:
        return missions
    ids = [m["id"] for m in missions]
    with get_db() as db:
        placeholders = ",".join(["?"] * len(ids))
        legs = rows_to_dicts(db.execute(
            f"""SELECT l.mission_id, l.start_time, l.end_time, v.plate AS vehicle_plate
                FROM mission_legs l LEFT JOIN vehicles v ON v.id = l.vehicle_id
                WHERE l.mission_id IN ({placeholders}) ORDER BY l.mission_id, l.position""",
            ids,
        ).fetchall())
    by_mission = {}
    for leg in legs:
        by_mission.setdefault(leg["mission_id"], []).append(leg)
    for m in missions:
        m["legs"] = by_mission.get(m["id"], [])
    return missions


def get_linked_mission_ids(mission_id):
    with get_db() as db:
        rows = db.execute(
            "SELECT linked_mission_id FROM mission_links WHERE mission_id = ?", (mission_id,)
        ).fetchall()
    return [r["linked_mission_id"] for r in rows]


def _replace_links(db, mission_id, linked_ids):
    """Remplace les missions liées à `mission_id`. Un lien vaut dans les
    deux sens : il est écrit A->B et B->A, si bien que « les missions liées
    à X » se lit par mission_id = X, et qu'un lien retiré depuis l'une des
    deux missions disparaît aussi de l'autre. La mission elle-même et les
    identifiants inconnus (mission supprimée entre-temps) sont ignorés."""
    wanted = {i for i in linked_ids if i != mission_id}
    if wanted:
        placeholders = ",".join(["?"] * len(wanted))
        wanted = [r["id"] for r in db.execute(
            f"SELECT id FROM missions WHERE id IN ({placeholders})", list(wanted)
        ).fetchall()]
    db.execute("DELETE FROM mission_links WHERE mission_id = ? OR linked_mission_id = ?",
               (mission_id, mission_id))
    for other in wanted:
        db.execute("INSERT INTO mission_links (mission_id, linked_mission_id) VALUES (?, ?), (?, ?)",
                   (mission_id, other, other, mission_id))


def set_mission_links(mission_id, linked_ids):
    with get_db() as db:
        _replace_links(db, mission_id, linked_ids)


def list_missions_for_planning(date_from, date_to, driver_id=None):
    """Missions d'une période (bornes incluses) avec leurs trajets, pour le
    planning/calendrier."""
    missions = list_missions(driver_id=driver_id, date_from=date_from, date_to=date_to, ascending=True)
    return attach_legs(missions)


def get_mission(mission_id):
    with get_db() as db:
        mission = row_to_dict(db.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone())
        if not mission:
            return None
        mission["driver"] = row_to_dict(
            db.execute("SELECT * FROM crew WHERE id = ?", (mission["driver_id"],)).fetchone()
        )
        mission["client"] = row_to_dict(
            db.execute("SELECT * FROM clients WHERE id = ?", (mission["client_id"],)).fetchone()
        ) if mission["client_id"] else None
        legs = db.execute(
            """SELECT l.*, v.plate AS vehicle_plate, v.name AS vehicle_name
               FROM mission_legs l LEFT JOIN vehicles v ON v.id = l.vehicle_id
               WHERE l.mission_id = ? ORDER BY l.position""",
            (mission_id,),
        ).fetchall()
        mission["legs"] = rows_to_dicts(legs)
        stops = db.execute(
            "SELECT * FROM mission_stops WHERE mission_id = ? ORDER BY position", (mission_id,)
        ).fetchall()
        mission["stops"] = rows_to_dicts(stops)
        # Métadonnées seulement : le contenu (LONGBLOB) est chargé à la
        # demande par le service PDF via list_attachment_contents().
        attachments = db.execute(
            """SELECT id, mission_id, filename, content_type, insert_after_page, position, created_at
               FROM attachments WHERE mission_id = ? ORDER BY position""",
            (mission_id,),
        ).fetchall()
        mission["attachments"] = rows_to_dicts(attachments)
        return mission


def list_attachment_contents(mission_id):
    """Pièces jointes d'une mission avec leur contenu binaire, triées."""
    with get_db() as db:
        return rows_to_dicts(
            db.execute(
                "SELECT * FROM attachments WHERE mission_id = ? ORDER BY position", (mission_id,)
            ).fetchall()
        )


def _legs_summary_fields(legs):
    """(amplitude, conduite, pause) en minutes à partir des trajets, pour
    les colonnes missions.*_minutes — mêmes règles que legs_time_summary()
    (prise/fin de service -> amplitude ; véhicule réel affecté -> conduite).
    (None, None, None) si pas assez d'horaires pour calculer."""
    summary = legs_time_summary(legs)
    if not summary:
        return None, None, None
    return summary["amplitude"], summary["driving"], summary["pause"]


def create_mission(data):
    amplitude, driving, pause = _legs_summary_fields(data.get("legs") or [])
    with get_db() as db:
        reference = _next_reference(db, data["mission_date"])
        cur = db.execute(
            """INSERT INTO missions (reference, mission_name, shuttle_label, driver_id,
               mission_date, motif, remarks, client_id, bc_client_name, bc_client_address,
               bc_client_postal_code, bc_client_city, bc_client_phone,
               emission_date, price, price_hidden, status,
               om_template_id, bc_template_id, amplitude_minutes, driving_minutes, pause_minutes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (reference, data.get("mission_name") or None,
             data.get("shuttle_label") or None, data["driver_id"], data["mission_date"],
             data.get("motif") or "Transport Occasionnel",
             data.get("remarks"), data.get("client_id") or None,
             data.get("bc_client_name"), data.get("bc_client_address"),
             data.get("bc_client_postal_code"), data.get("bc_client_city"),
             data.get("bc_client_phone"), data.get("emission_date"),
             data.get("price"), 1 if data.get("price_hidden") else 0,
             data.get("status") or "brouillon",
             data.get("om_template_id") or None, data.get("bc_template_id") or None,
             amplitude, driving, pause),
        )
        mission_id = cur.lastrowid
        _replace_legs(db, mission_id, data.get("legs") or [])
        _replace_stops(db, mission_id, data.get("stops") or [])
        # Absent pour une duplication : les liens ne se copient pas.
        if data.get("linked_mission_ids") is not None:
            _replace_links(db, mission_id, data["linked_mission_ids"])
        return mission_id


def _copy_base_fields(src):
    """Champs mission communs à duplicate_mission()/create_return_mission()."""
    return {
        "driver_id": src["driver_id"],
        "mission_date": src["mission_date"],
        "bc_client_name": src.get("bc_client_name"),
        "bc_client_address": src.get("bc_client_address"),
        "bc_client_postal_code": src.get("bc_client_postal_code"),
        "bc_client_city": src.get("bc_client_city"),
        "bc_client_phone": src.get("bc_client_phone"),
        "mission_name": src.get("mission_name"),
        "shuttle_label": src.get("shuttle_label"),
        "motif": src["motif"],
        "remarks": src["remarks"],
        "client_id": src["client_id"],
        # Duplication et trajet retour donnent un document neuf : il est
        # émis aujourd'hui, pas à la date de l'original.
        "emission_date": now_paris().date().isoformat(),
        "price": src["price"],
        "price_hidden": src.get("price_hidden") or 0,
        "status": "brouillon",
        "om_template_id": src["om_template_id"],
        "bc_template_id": src["bc_template_id"],
    }


def _copy_leg(l):
    return {"start_time": l["start_time"], "end_time": l["end_time"], "vehicle_id": l["vehicle_id"],
            "label": l["label"], "is_checkpoint": l["is_checkpoint"], "is_relay": l.get("is_relay"),
            "relay_driver_id": l.get("relay_driver_id"), "distance_m": l.get("distance_m")}


def _copy_stop(s):
    return {"stop_type": s["stop_type"], "stop_date": s["stop_date"], "stop_time": s["stop_time"],
            "address": s["address"], "city": s["city"], "passenger_count": s["passenger_count"],
            "passenger_name": s["passenger_name"], "passenger_phone": s["passenger_phone"],
            "booking_ref": s["booking_ref"]}


def duplicate_mission(mission_id):
    """Crée une copie de la mission (nouvelle référence, statut réinitialisé
    à « brouillon », trajets/arrêts recopiés). Les pièces jointes et
    l'historique d'envoi ne sont pas dupliqués. Renvoie le nouvel id."""
    src = get_mission(mission_id)
    if not src:
        return None
    data = _copy_base_fields(src)
    data["legs"] = [_copy_leg(l) for l in src["legs"]]
    data["stops"] = [_copy_stop(s) for s in src["stops"]]
    return create_mission(data)


_STOP_TYPE_SWAP = {"prise_en_charge": "depose", "depose": "prise_en_charge"}


def _reverse_leg_label(label):
    if not label:
        return label
    if label.startswith("Prise de service"):
        return label.replace("Prise de service", "Fin de service", 1)
    if label.startswith("Fin de service"):
        return label.replace("Fin de service", "Prise de service", 1)
    if " → " in label:
        left, right = label.split(" → ", 1)
        return f"{right} → {left}"
    return label


_DIRECTION_SWAP = {"A": "R", "R": "A", "a": "r", "r": "a"}


def _swap_direction_suffix(name):
    """« NAVETTE 3 A » <-> « NAVETTE 3 R » (ou « A » <-> « R » seul) :
    bascule le sens du trajet dans le nom de mission, en ne touchant que la
    lettre A/R isolée en toute fin de nom (mot entier). Laisse les autres
    noms inchangés."""
    if not name:
        return name
    m = re.match(r"^(?:(.*\S)(\s+))?([ARar])$", name)
    if not m:
        return name
    prefix, sep, letter = m.groups()
    return f"{prefix or ''}{sep or ''}{_DIRECTION_SWAP[letter]}"


def create_return_mission(mission_id, start_date=None, start_time=None):
    """Crée le trajet retour : arrêts et trajets dans l'ordre inverse,
    prise en charge <-> dépose inversées, libellés de trajet retournés
    (« A → B » devient « B → A », prise/fin de service échangées), et
    suffixe de sens A/R du nom de mission basculé.

    Avec `start_date` et `start_time` (fenêtre « Créer le retour »), les
    arrêts sont replanifiés : le premier à l'heure donnée, les suivants aux
    mêmes écarts qu'à l'aller (app/utils.py:reschedule_stops).

    Les heures des trajets sont ensuite **recalculées depuis ces arrêts**
    (retime_legs_from_stops), et non décalées : les trajets du retour sont
    ceux de l'aller pris à l'envers, leurs heures d'origine ne veulent plus
    rien dire dans ce sens-là. Le résultat est celui qu'aurait donné le
    bouton « Générer les trajets depuis les arrêts » du formulaire."""
    src = get_mission(mission_id)
    if not src:
        return None
    data = _copy_base_fields(src)
    data["mission_name"] = _swap_direction_suffix(data.get("mission_name"))
    data["legs"] = [_copy_leg(l) | {"label": _reverse_leg_label(l["label"])}
                     for l in reversed(src["legs"])]
    data["stops"] = [_copy_stop(s) | {"stop_type": _STOP_TYPE_SWAP.get(s["stop_type"], s["stop_type"])}
                      for s in reversed(src["stops"])]
    if reschedule_stops(data["stops"], start_date, start_time) is not None:
        data["mission_date"] = parse_iso_date(start_date).isoformat()
    # Recalé même sans date saisie : dans les deux cas les arrêts sont la
    # seule source d'heures cohérente pour le sens retour.
    retime_legs_from_stops(data["legs"], data["stops"])
    # L'aller et son retour sont liés d'office (section « Missions liées »).
    data["linked_mission_ids"] = [mission_id]
    return create_mission(data)


def update_mission(mission_id, data):
    amplitude, driving, pause = _legs_summary_fields(data.get("legs") or [])
    with get_db() as db:
        # Mission confiée à quelqu'un d'autre : l'OM déjà envoyé ne vaut
        # plus (ni pour l'interim, ni pour l'ancien chauffeur). Les deux
        # indicateurs d'envoi repartent à zéro, il faut renvoyer.
        row = db.execute("SELECT driver_id FROM missions WHERE id = ?", (mission_id,)).fetchone()
        driver_changed = bool(row) and row["driver_id"] != data["driver_id"]
        resend = ", sent_randstad_at=NULL, sent_driver_at=NULL" if driver_changed else ""
        db.execute(
            """UPDATE missions SET driver_id=?, mission_date=?, mission_name=?,
               shuttle_label=?, motif=?, remarks=?,
               client_id=?, bc_client_name=?, bc_client_address=?, bc_client_postal_code=?,
               bc_client_city=?, bc_client_phone=?,
               emission_date=?, price=?, price_hidden=?, status=?, om_template_id=?, bc_template_id=?,
               amplitude_minutes=?, driving_minutes=?, pause_minutes=?,
               updated_at=?""" + resend + " WHERE id=?",
            (data["driver_id"], data["mission_date"], data.get("mission_name") or None,
             data.get("shuttle_label") or None,
             data.get("motif") or "Transport Occasionnel",
             data.get("remarks"), data.get("client_id") or None,
             data.get("bc_client_name"), data.get("bc_client_address"),
             data.get("bc_client_postal_code"), data.get("bc_client_city"),
             data.get("bc_client_phone"), data.get("emission_date"),
             data.get("price"), 1 if data.get("price_hidden") else 0,
             data.get("status") or "brouillon",
             data.get("om_template_id") or None, data.get("bc_template_id") or None,
             amplitude, driving, pause,
             now_iso(), mission_id),
        )
        _replace_legs(db, mission_id, data.get("legs") or [])
        _replace_stops(db, mission_id, data.get("stops") or [])
        if data.get("linked_mission_ids") is not None:
            _replace_links(db, mission_id, data["linked_mission_ids"])


def _replace_legs(db, mission_id, legs):
    db.execute("DELETE FROM mission_legs WHERE mission_id = ?", (mission_id,))
    for i, leg in enumerate(legs):
        db.execute(
            """INSERT INTO mission_legs (mission_id, position, start_time, end_time, vehicle_id,
               label, is_checkpoint, is_relay, relay_driver_id, distance_m)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mission_id, i, leg["start_time"], leg["end_time"], leg.get("vehicle_id") or None,
             leg["label"], 1 if leg.get("is_checkpoint") else 0,
             1 if leg.get("is_relay") else 0, leg.get("relay_driver_id") or None,
             leg.get("distance_m")),
        )


def _replace_stops(db, mission_id, stops):
    db.execute("DELETE FROM mission_stops WHERE mission_id = ?", (mission_id,))
    for i, stop in enumerate(stops):
        db.execute(
            """INSERT INTO mission_stops (mission_id, position, stop_type, stop_date, stop_time,
               address, city, passenger_count, passenger_name, passenger_phone, booking_ref)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mission_id, i, stop["stop_type"], stop["stop_date"], stop["stop_time"], stop["address"],
             stop.get("city"), stop.get("passenger_count") or 1, stop.get("passenger_name"),
             stop.get("passenger_phone"), stop.get("booking_ref")),
        )


def delete_mission(mission_id):
    # Pas de FK ON DELETE CASCADE (schéma sans contraintes) -> on supprime
    # explicitement les lignes filles.
    with get_db() as db:
        for table in ("mission_legs", "mission_stops", "attachments", "email_log"):
            db.execute(f"DELETE FROM {table} WHERE mission_id = ?", (mission_id,))
        db.execute("DELETE FROM mission_links WHERE mission_id = ? OR linked_mission_id = ?",
                   (mission_id, mission_id))
        db.execute("DELETE FROM missions WHERE id = ?", (mission_id,))


def set_mission_status(mission_id, status):
    with get_db() as db:
        db.execute("UPDATE missions SET status=?, updated_at=? WHERE id=?", (status, now_iso(), mission_id))


def set_missions_archived(mission_ids, archived):
    """Archive ou desarchive plusieurs missions d'un coup. Renvoie le nombre
    de lignes reellement changees (une mission deja dans l'etat vise n'est
    pas recomptee)."""
    if not mission_ids:
        return 0
    placeholders = ",".join(["?"] * len(mission_ids))
    with get_db() as db:
        if archived:
            cur = db.execute(
                f"UPDATE missions SET archived_at = ? WHERE id IN ({placeholders}) "
                "AND archived_at IS NULL",
                [now_iso()] + list(mission_ids))
        else:
            cur = db.execute(
                f"UPDATE missions SET archived_at = NULL WHERE id IN ({placeholders}) "
                "AND archived_at IS NOT NULL",
                list(mission_ids))
        return cur.rowcount


def set_price_hidden(mission_id, hidden):
    """Bascule « prix masque » d'une mission. Colonne a part du formulaire :
    l'enregistrement d'un ordre de mission n'y touche pas."""
    with get_db() as db:
        db.execute("UPDATE missions SET price_hidden=?, updated_at=? WHERE id=?",
                   (1 if hidden else 0, now_iso(), mission_id))


def mark_sent_randstad(mission_id):
    with get_db() as db:
        db.execute("UPDATE missions SET sent_randstad_at=? WHERE id=?", (now_iso(), mission_id))


def mark_sent_driver(mission_id):
    with get_db() as db:
        db.execute("UPDATE missions SET sent_driver_at=? WHERE id=?", (now_iso(), mission_id))


# ------------------------------------------------------------ attachments
def add_attachment(mission_id, filename, content, content_type, insert_after_page):
    """`content` : octets bruts du fichier (stockés en LONGBLOB)."""
    with get_db() as db:
        max_pos = db.execute(
            "SELECT COALESCE(MAX(position), -1) AS m FROM attachments WHERE mission_id = ?", (mission_id,)
        ).fetchone()["m"]
        cur = db.execute(
            """INSERT INTO attachments (mission_id, filename, content, content_type,
               insert_after_page, position) VALUES (?, ?, ?, ?, ?, ?)""",
            (mission_id, filename, content, content_type, insert_after_page, max_pos + 1),
        )
        return cur.lastrowid


def get_attachment(attachment_id):
    with get_db() as db:
        return row_to_dict(db.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone())


def delete_attachment(attachment_id):
    with get_db() as db:
        db.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))


# ------------------------------------------------------------- email log
def log_email(mission_id, to_addresses, cc_addresses, subject, body, status, error_message=None,
              message_id=None, provider_message_id=None):
    with get_db() as db:
        db.execute(
            """INSERT INTO email_log (mission_id, to_addresses, cc_addresses, subject, body, status,
               error_message, message_id, provider_message_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mission_id, to_addresses, cc_addresses, subject, body, status, error_message,
             message_id, provider_message_id),
        )


def last_email_to(mission_id, address):
    """Dernier email parti avec succes pour cette mission vers cette
    adresse. Sert a repondre dans le fil de l'ordre de mission deja envoye
    au chauffeur : on y reprend l'objet et, s'il a ete pose, le Message-ID.
    None si rien n'est encore parti a cette adresse."""
    if not address:
        return None
    with get_db() as db:
        return row_to_dict(db.execute(
            """SELECT * FROM email_log
               WHERE mission_id = ? AND status = 'sent' AND to_addresses LIKE ?
               ORDER BY sent_at DESC, id DESC LIMIT 1""",
            (mission_id, f"%{address}%"),
        ).fetchone())


def list_email_log(mission_id):
    with get_db() as db:
        return rows_to_dicts(
            db.execute(
                "SELECT * FROM email_log WHERE mission_id = ? ORDER BY sent_at DESC", (mission_id,)
            ).fetchall()
        )


# -------------------------------------------------------------- réglages
# Petite table clé/valeur pour les réglages modifiables depuis l'écran
# Réglages (ex. fournisseur de recherche d'adresse), par opposition aux
# réglages fixés en .env (clés d'API, config serveur).
def get_setting(key, default=None):
    with get_db() as db:
        row = db.execute("SELECT setting_value FROM app_settings WHERE setting_key = ?", (key,)).fetchone()
        return row["setting_value"] if row else default


def set_setting(key, value):
    with get_db() as db:
        db.execute(
            """INSERT INTO app_settings (setting_key, setting_value) VALUES (?, ?)
               ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value), updated_at = CURRENT_TIMESTAMP""",
            (key, value),
        )
