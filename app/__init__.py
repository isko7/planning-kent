from flask import Flask, flash, g, jsonify, redirect, request, url_for

from app.config import SECRET_KEY, MAX_UPLOAD_MB
from app.db import init_db
from app.utils import register_jinja_filters


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

    try:
        init_db()
        # Au moins un chauffeur doit pouvoir se connecter, sinon
        # l'application deviendrait inaccessible à tout le monde.
        from app.seeding import ensure_login_access
        for msg in ensure_login_access():
            app.logger.warning("Amorçage de l'accès : %s", msg)
    except Exception as e:  # pragma: no cover - visible dans les logs Vercel
        app.logger.warning("init_db a échoué au démarrage : %s", e)
    register_jinja_filters(app)

    from app.routes.auth import bp as auth_bp
    from app.routes.drivers import bp as drivers_bp
    from app.routes.vehicles import bp as vehicles_bp
    from app.routes.clients import bp as clients_bp
    from app.routes.partners import bp as partners_bp
    from app.routes.missions import bp as missions_bp
    from app.routes.planning import bp as planning_bp
    from app.routes.tours import bp as tours_bp
    from app.routes.templates_admin import bp as templates_bp
    from app.routes.admin import bp as admin_bp
    from app.routes.settings import bp as settings_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(drivers_bp)
    app.register_blueprint(vehicles_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(partners_bp)
    app.register_blueprint(missions_bp)
    app.register_blueprint(planning_bp)
    app.register_blueprint(tours_bp)
    app.register_blueprint(templates_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(settings_bp)

    _register_auth_guard(app)

    @app.route("/")
    def index():
        return redirect(url_for("planning.calendar_view"))

    @app.route("/healthz")
    def healthz():
        return {"ok": True}

    @app.errorhandler(404)
    def not_found(e):
        return "Page introuvable", 404

    return app


def _register_auth_guard(app):
    """Toute l'application est fermée par défaut : plutôt que de décorer
    chaque vue (et risquer d'en oublier une), on contrôle avant chaque
    requête, dans l'ordre :

    1. connecté ? sinon -> page de connexion (app.auth.PUBLIC_ENDPOINTS) ;
    2. mot de passe à renouveler ? sinon -> écran dédié, rien d'autre ;
    3. droits suffisants ? un chauffeur non administrateur n'atteint que
       app.auth.DRIVER_ENDPOINTS.
    """
    from app.auth import (PASSWORD_CHANGE_ENDPOINTS, current_user, is_admin,
                          is_public_request, load_current_user,
                          must_change_password, requires_admin_endpoint, wants_json)

    def _refuse(message, status):
        if wants_json():
            return jsonify({"ok": False, "error": message}), status
        return None

    @app.before_request
    def authenticate():
        try:
            g.current_user = load_current_user()
        except Exception as e:  # base injoignable : on ne connecte personne
            app.logger.warning("Lecture du chauffeur connecté impossible : %s", e)
            g.current_user = None

        # Fichiers statiques, écran de connexion, routes portant leur propre
        # secret : hors de tout contrôle de droits, connecté ou non. À
        # vérifier en premier — un chauffeur connecté n'est pas autorisé sur
        # l'endpoint `static`, il ne verrait plus ni CSS ni logo.
        if is_public_request():
            return None

        if g.current_user is None:
            return (_refuse("Session expirée. Reconnectez-vous.", 401)
                    or redirect(url_for("auth.login", next=request.full_path)))

        # Mot de passe réinitialisé par un administrateur : tant qu'il n'a
        # pas été renouvelé, l'application se limite à cet écran.
        if must_change_password() and request.endpoint not in PASSWORD_CHANGE_ENDPOINTS:
            return (_refuse("Vous devez choisir un nouveau mot de passe.", 403)
                    or redirect(url_for("auth.change_password")))

        if requires_admin_endpoint() and not is_admin():
            refused = _refuse("Vous n'avez pas les droits pour cette action.", 403)
            if refused:
                return refused
            # Renvoi vers son planning plutôt qu'une page d'erreur : le cas
            # normal est un lien devenu hors de portée, pas une intrusion.
            flash("Cette page est réservée aux administrateurs.", "error")
            return redirect(url_for("planning.calendar_view"))

        return None

    @app.context_processor
    def inject_current_user():
        return {"current_user": current_user(), "is_admin": is_admin()}

    @app.context_processor
    def inject_vehicle_alerts():
        """Pastille rouge du menu Véhicules : nombre de véhicules dont le
        contrôle technique arrive à échéance (ou est dépassé). Réservée aux
        administrateurs — les seuls à voir cette entrée de menu — et muette
        si la base ne répond pas : une pastille ne doit pas casser une page."""
        if not is_admin():
            return {"vehicle_ct_alerts": 0}
        from app import repo
        from app.routes.vehicles import ct_alert_deadline
        try:
            return {"vehicle_ct_alerts": repo.count_vehicles_ct_due(ct_alert_deadline())}
        except Exception as e:
            app.logger.warning("Comptage des contrôles techniques impossible : %s", e)
            return {"vehicle_ct_alerts": 0}
