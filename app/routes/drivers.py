import re

from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   make_response)

from app import repo
from app.auth import current_user, hash_password, issue_token, set_auth_cookie
from app.utils import DRIVER_COLOR_PALETTE, invalid_emails, normalize_emails

bp = Blueprint("drivers", __name__, url_prefix="/chauffeurs")

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,80}$")
MIN_PASSWORD_LENGTH = 8


def _form_to_data(form):
    color = form.get("color", "").strip()
    return {
        "last_name": form.get("last_name", "").strip().upper(),
        "first_name": form.get("first_name", "").strip(),
        # Plusieurs adresses possibles, separees par des points-virgules :
        # une personne peut vouloir ses ordres de mission sur sa boite
        # perso et celle de son agence. Stockees remises au propre (voir
        # utils.normalize_emails) pour que la relecture de la fiche soit
        # stable, quelle que soit la facon dont elles ont ete collees.
        "email": normalize_emails(form.get("email", "")),
        "phone": form.get("phone", "").strip() or None,
        "license_number": form.get("license_number", "").strip() or None,
        "active": form.get("active") == "on",
        "color": color if HEX_COLOR_RE.match(color) else None,
        "send_itinerary": form.get("send_itinerary") == "on",
        "can_login": form.get("can_login") == "on",
        "is_admin": form.get("is_admin") == "on",
        # Identifiant normalisé en minuscules : la connexion l'accepte déjà
        # sans tenir compte de la casse, autant le stocker d'une seule façon.
        "username": form.get("username", "").strip().lower() or None,
        "notes": form.get("notes", "").strip() or None,
        "remarks": form.get("remarks", "").strip() or None,
        # Agence d'interim : facultative, « — Aucun — » renvoie une
        # chaine vide que `type=int` transforme en None.
        "partner_id": form.get("partner_id", type=int) or None,
    }


def _form_context():
    """Listes deroulantes du formulaire : palette de couleurs et agences
    d'interim (ecran Partenaires)."""
    return {"palette": DRIVER_COLOR_PALETTE, "partners": repo.list_partners()}


def _validate(data):
    """Controles communs a la creation et a la modification."""
    errors = []
    if not data["last_name"] or not data["first_name"] or not data["email"]:
        errors.append("Nom, prénom et email sont obligatoires.")
    mauvaises = invalid_emails(data["email"])
    if mauvaises:
        errors.append("Adresse email invalide : " + ", ".join(mauvaises)
                      + ". Séparez plusieurs adresses par un point-virgule.")
    return errors


def _apply_credentials(data, form, existing=None):
    """Complète `data` avec les identifiants de connexion et renvoie la
    liste des erreurs. `existing` = fiche actuelle en modification (None en
    création) : le mot de passe n'est redemandé que s'il n'y en a pas
    encore. Laisser `password_hash` absent du dict signifie « ne pas
    toucher au mot de passe existant » (voir repo.update_driver)."""
    errors = []
    password = form.get("password", "")
    confirm = form.get("password_confirm", "")
    driver_id = existing["id"] if existing else None
    has_password = bool((existing or {}).get("password_hash"))

    if not data["can_login"]:
        # Accès retiré : on efface aussi les identifiants et les droits,
        # pour ne pas laisser un mot de passe dormant sur la fiche.
        data["username"] = None
        data["password_hash"] = None
        data["is_admin"] = False
        data["must_change_password"] = False
        return errors

    if not data["username"]:
        errors.append("Un identifiant de connexion est obligatoire pour donner l'accès "
                      "à l'application.")
    elif not USERNAME_RE.match(data["username"]):
        errors.append("L'identifiant ne peut contenir que des lettres, chiffres, point, "
                      "tiret ou souligné (3 caractères minimum).")
    elif repo.username_taken(data["username"], exclude_driver_id=driver_id):
        errors.append("Cet identifiant est déjà utilisé par un autre utilisateur.")

    if password or not has_password:
        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append("Le mot de passe doit faire au moins "
                          f"{MIN_PASSWORD_LENGTH} caractères.")
        elif password != confirm:
            errors.append("Les deux mots de passe saisis ne correspondent pas.")
        else:
            data["password_hash"] = hash_password(password)
            # Mot de passe défini par un administrateur POUR QUELQU'UN
            # D'AUTRE : il est provisoire, son titulaire doit en choisir un
            # lui-même à sa première connexion. Quand on saisit le sien, en
            # revanche, il n'y a rien à renouveler.
            user = current_user()
            data["must_change_password"] = not (user and user["id"] == driver_id)

    return errors


def _guard_last_account(driver_id, data):
    """Empêche de se fermer la porte. Deux niveaux : l'accès tout court, et
    les droits d'administration (sans administrateur, plus personne ne peut
    gérer les accès ni se les redonner)."""
    user = current_user()
    existing = repo.get_driver(driver_id) or {}
    # Mêmes critères que repo.count_drivers_with_login / count_admins.
    usable = (existing.get("can_login") and existing.get("active")
              and existing.get("password_hash"))

    keeps_access = data["can_login"] and data["active"]
    if not keeps_access:
        if user and user["id"] == driver_id:
            return "Vous ne pouvez pas retirer votre propre accès à l'application."
        if usable and repo.count_drivers_with_login() <= 1:
            return ("Cette fiche est le dernier compte pouvant se connecter : donnez l'accès "
                    "à un autre utilisateur avant de le retirer.")
        return None

    if existing.get("is_admin") and not data["is_admin"]:
        if user and user["id"] == driver_id:
            return ("Vous ne pouvez pas retirer vos propres droits d'administration. "
                    "Demandez à un autre administrateur de le faire.")
        if usable and repo.count_admins() <= 1:
            return ("Cette fiche est le dernier administrateur : nommez-en un autre "
                    "avant de lui retirer ce droit.")
    return None


@bp.route("/")
def list_drivers_view():
    drivers = repo.list_drivers()
    partner_names = {p["id"]: p["name"] for p in repo.list_partners()}
    return render_template("drivers/list.html", drivers=drivers, partner_names=partner_names)


@bp.route("/nouveau", methods=["GET", "POST"])
def new_driver():
    if request.method == "POST":
        data = _form_to_data(request.form)
        errors = _validate(data)
        errors += _apply_credentials(data, request.form)
        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("drivers/form.html", driver=data, is_new=True,
                                   **_form_context())
        repo.create_driver(data)
        flash(f"Utilisateur {data['first_name']} {data['last_name']} créé.", "success")
        return redirect(url_for("drivers.list_drivers_view"))
    return render_template("drivers/form.html", driver={"active": True}, is_new=True,
                           **_form_context())


@bp.route("/<int:driver_id>", methods=["GET", "POST"])
def edit_driver(driver_id):
    driver = repo.get_driver(driver_id)
    if not driver:
        flash("Utilisateur introuvable.", "error")
        return redirect(url_for("drivers.list_drivers_view"))
    if request.method == "POST":
        data = _form_to_data(request.form)
        errors = _validate(data)
        errors += _apply_credentials(data, request.form, existing=driver)
        blocking = _guard_last_account(driver_id, data)
        if blocking:
            errors.append(blocking)
        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("drivers/form.html", driver=dict(driver, **data), is_new=False,
                                   driver_id=driver_id, **_form_context())
        repo.update_driver(driver_id, data)
        flash("Utilisateur mis à jour.", "success")
        response = redirect(url_for("drivers.list_drivers_view"))
        # Le jeton porte une empreinte du mot de passe : changer le sien
        # sans réémettre le cookie déconnecterait immédiatement.
        user = current_user()
        if user and user["id"] == driver_id and data.get("password_hash"):
            response = set_auth_cookie(make_response(response),
                                       issue_token(repo.get_driver(driver_id)))
        return response
    return render_template("drivers/form.html", driver=driver, is_new=False, driver_id=driver_id,
                           **_form_context())


@bp.route("/<int:driver_id>/reinitialiser-mot-de-passe", methods=["POST"])
def reset_password(driver_id):
    """Remet le mot de passe à l'identifiant de l'utilisateur et force son
    renouvellement à la prochaine connexion. Sert quand quelqu'un a perdu
    son mot de passe : l'administrateur lui redonne son identifiant comme
    mot de passe provisoire, de vive voix."""
    driver = repo.get_driver(driver_id)
    if not driver:
        flash("Utilisateur introuvable.", "error")
        return redirect(url_for("drivers.list_drivers_view"))
    if not driver.get("can_login") or not driver.get("username"):
        flash("Cet utilisateur n'a pas d'accès à l'application.", "error")
        return redirect(url_for("drivers.edit_driver", driver_id=driver_id))

    username = driver["username"]
    repo.set_password(driver_id, hash_password(username), must_change=True)
    flash(f"Mot de passe réinitialisé : il vaut maintenant l'identifiant « {username} ». "
          f"Un nouveau mot de passe sera demandé à la prochaine connexion.", "success")

    response = redirect(url_for("drivers.edit_driver", driver_id=driver_id))
    # Se réinitialiser soi-même : on garde la session ouverte (le jeton
    # suit le nouveau mot de passe), l'écran de renouvellement prend le
    # relais dès la requête suivante.
    user = current_user()
    if user and user["id"] == driver_id:
        response = set_auth_cookie(make_response(response),
                                   issue_token(repo.get_driver(driver_id)))
    return response


@bp.route("/<int:driver_id>/supprimer", methods=["POST"])
def delete_driver(driver_id):
    user = current_user()
    if user and user["id"] == driver_id:
        flash("Vous ne pouvez pas supprimer votre propre fiche.", "error")
        return redirect(url_for("drivers.list_drivers_view"))
    repo.delete_driver(driver_id)
    flash("Utilisateur supprimé.", "success")
    return redirect(url_for("drivers.list_drivers_view"))
