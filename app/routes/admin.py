"""
Route d'administration protégée par un secret (env SEED_SECRET).

Sert à initialiser la base après le premier déploiement Vercel, sans avoir
besoin d'ouvrir un accès MySQL depuis votre poste :

    GET /admin/init?key=<SEED_SECRET>          -> schéma + templates
    GET /admin/init?key=<SEED_SECRET>&demo=1   -> + données de démo

Si SEED_SECRET n'est pas défini, la route répond 404.
"""
import time
import traceback

import secrets

from flask import Blueprint, request, jsonify, abort

from app.config import env
from app.db import init_db, check_connection
from app.seeding import (seed_templates, seed_demo_data, refresh_default_templates,
                          ensure_login_access)

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _require_secret():
    secret = env("SEED_SECRET")
    if not secret:
        abort(404)
    # Comparaison à temps constant : un « != » sur une chaîne s'arrête au
    # premier caractère différent, et le temps de réponse renseigne alors sur
    # le secret. C'est théorique derrière le réseau, mais c'est la manière
    # d'écrire une comparaison de secret.
    if not secrets.compare_digest(request.args.get("key") or "", secret):
        abort(403)


@bp.route("/dbcheck")
def dbcheck():
    """Diagnostic connexion MySQL : /admin/dbcheck?key=<SEED_SECRET>"""
    _require_secret()
    result = check_connection()
    return jsonify(result), (200 if result.get("ok") else 500)


@bp.route("/pdftest")
def pdftest():
    """Diagnostic du rendu PDF : /admin/pdftest?key=<SEED_SECRET>
    Teste render_html_to_pdf sur un HTML minimal et rapporte l'erreur."""
    _require_secret()
    import os
    from app import pdf_service
    from app.config import PDF_ENGINE, PDF_RENDER_URL, PDF_RENDER_SECRET
    info = {
        "PDF_ENGINE": PDF_ENGINE,
        "render_url": pdf_service._pdf_render_base_url() + "/api/render_pdf",
        "VERCEL_URL": os.environ.get("VERCEL_URL"),
        "VERCEL_PROJECT_PRODUCTION_URL": os.environ.get("VERCEL_PROJECT_PRODUCTION_URL"),
        "bypass_secret_set": bool(os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET")),
        "render_secret_set": bool(PDF_RENDER_SECRET),
    }
    html = "<!doctype html><html><head><style>@page{size:A4;margin:1cm}</style></head><body><h1>Test PDF</h1><p>OK</p></body></html>"
    t0 = time.monotonic()
    try:
        pdf = pdf_service.render_html_to_pdf(html)
        info["ok"] = True
        info["pdf_bytes"] = len(pdf)
        info["is_pdf"] = pdf[:5] == b"%PDF-"
    except Exception as e:
        info["ok"] = False
        info["error"] = f"{type(e).__name__}: {e}"
        info["trace"] = traceback.format_exc().splitlines()[-8:]
    info["ms"] = round((time.monotonic() - t0) * 1000)
    return jsonify(info), (200 if info.get("ok") else 500)


@bp.route("/init")
def init():
    _require_secret()

    steps = {}
    try:
        t0 = time.monotonic()
        steps["schema"] = init_db(force=True, report=True)
        steps["schema_ms"] = round((time.monotonic() - t0) * 1000)

        t0 = time.monotonic()
        if request.args.get("refresh_templates") in ("1", "true", "yes"):
            steps["templates"] = refresh_default_templates()
        else:
            steps["templates"] = seed_templates()
        steps["templates_ms"] = round((time.monotonic() - t0) * 1000)

        t0 = time.monotonic()
        steps["login_access"] = ensure_login_access() or ["déjà configuré"]
        steps["login_access_ms"] = round((time.monotonic() - t0) * 1000)

        if request.args.get("demo") in ("1", "true", "yes"):
            t0 = time.monotonic()
            steps["demo"] = seed_demo_data() or ["déjà présent"]
            steps["demo_ms"] = round((time.monotonic() - t0) * 1000)
    except Exception as e:
        steps["ok"] = False
        steps["error"] = f"{type(e).__name__}: {e}"
        steps["trace"] = traceback.format_exc().splitlines()[-6:]
        return jsonify(steps), 500

    steps["ok"] = True
    return jsonify(steps)
