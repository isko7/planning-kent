"""
Partenaires : les agences d'interim.

Une agence porte ses coordonnees (nom, telephone, email) et son modele
d'email. Le modele sert au bouton « Envoyer a l'interim » de la liste des
missions : le destinataire et le texte pre-remplis viennent de l'agence
rattachee aux chauffeurs des missions cochees (champ « Interim » de leur
fiche Personnel), et non plus d'une adresse figee dans le .env.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash

from app import repo
from app.utils import (DEFAULT_PARTNER_EMAIL_BODY, DEFAULT_PARTNER_EMAIL_SUBJECT,
                       PARTNER_EMAIL_FIELDS)

bp = Blueprint("partners", __name__, url_prefix="/partenaires")


def _form_to_data(form):
    # Un modele laisse vide retombe sur celui par defaut plutot que de
    # produire un email vide : on ne peut pas « effacer » un modele, on le
    # remplace.
    subject = form.get("email_subject", "").strip()
    # Un <textarea> renvoie des fins de ligne CRLF : on les ramene a "\n"
    # avant de stocker, sinon le corps de l'email melange les deux et
    # _body_to_html (email_service.py), qui decoupe sur "\n", laisse un
    # "\r" en fin de chaque ligne.
    body = form.get("email_body", "").replace(chr(13) + chr(10), chr(10)).strip()
    return {
        "name": form.get("name", "").strip(),
        "phone": form.get("phone", "").strip() or None,
        "email": form.get("email", "").strip(),
        "email_subject": subject or DEFAULT_PARTNER_EMAIL_SUBJECT,
        "email_body": body or DEFAULT_PARTNER_EMAIL_BODY,
        "position": form.get("position", type=int) or 0,
    }


def _errors(data):
    """Nom et email sont exiges : l'email est le destinataire de l'envoi
    groupe « Envoyer a l'interim », une agence sans adresse ne servirait a
    rien. Le champ Interim d'une fiche Personnel reste, lui, facultatif."""
    errors = []
    if not data["name"]:
        errors.append("Le nom est obligatoire.")
    if not data["email"]:
        errors.append("L'email est obligatoire : c'est le destinataire de "
                      "l'envoi groupé à cette agence.")
    return errors


def _context(partner, is_new, partner_id=None):
    return {
        "partner": partner, "is_new": is_new, "partner_id": partner_id,
        "placeholders": PARTNER_EMAIL_FIELDS,
    }


@bp.route("/")
def list_partners_view():
    partners = repo.list_partners()
    # Nombre de fiches Personnel rattachees : indique d'un coup d'oeil
    # quelle agence est reellement utilisee, et ce qu'une suppression
    # detacherait.
    counts = {p["id"]: repo.count_crew_by_partner(p["id"]) for p in partners}
    return render_template("partners/list.html", partners=partners, counts=counts)


@bp.route("/nouveau", methods=["GET", "POST"])
def new_partner():
    if request.method == "POST":
        data = _form_to_data(request.form)
        errors = _errors(data)
        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("partners/form.html", **_context(data, True))
        repo.create_partner(data)
        flash(f"Partenaire {data['name']} créé.", "success")
        return redirect(url_for("partners.list_partners_view"))
    modele = {"email_subject": DEFAULT_PARTNER_EMAIL_SUBJECT,
              "email_body": DEFAULT_PARTNER_EMAIL_BODY}
    return render_template("partners/form.html", **_context(modele, True))


@bp.route("/<int:partner_id>", methods=["GET", "POST"])
def edit_partner(partner_id):
    partner = repo.get_partner(partner_id)
    if not partner:
        flash("Partenaire introuvable.", "error")
        return redirect(url_for("partners.list_partners_view"))
    if request.method == "POST":
        data = _form_to_data(request.form)
        errors = _errors(data)
        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("partners/form.html", **_context(data, False, partner_id))
        repo.update_partner(partner_id, data)
        flash("Partenaire mis à jour.", "success")
        return redirect(url_for("partners.list_partners_view"))
    return render_template("partners/form.html", **_context(partner, False, partner_id))


@bp.route("/<int:partner_id>/supprimer", methods=["POST"])
def delete_partner(partner_id):
    attached = repo.count_crew_by_partner(partner_id)
    repo.delete_partner(partner_id)
    message = "Partenaire supprimé."
    if attached:
        message += f" {attached} fiche(s) Personnel n'ont plus d'intérim renseigné."
    flash(message, "success")
    return redirect(url_for("partners.list_partners_view"))
