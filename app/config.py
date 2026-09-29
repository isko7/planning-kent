"""
Configuration de l'application.

Toutes les valeurs peuvent être surchargées par des variables
d'environnement (ou un fichier .env à la racine du projet — voir
.env.example). Aucune dépendance externe requise : le petit loader
ci-dessous lit .env s'il existe, sans écraser des variables déjà
définies dans l'environnement réel (donc les variables Vercel gagnent).
"""
import os
import re
from pathlib import Path
from urllib.parse import unquote

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def env(key, default=None):
    return os.environ.get(key, default)


# --- Général -----------------------------------------------------------
SECRET_KEY = env("SECRET_KEY", "dev-secret-change-me")
PORT = int(env("PORT", "8000"))
DEBUG = env("FLASK_DEBUG", "0") == "1"

# --- Authentification (JWT) -------------------------------------------
# Les seuls comptes sont des fiches chauffeur : un chauffeur peut se
# connecter si on lui a coché « Accès à l'application » et donné un
# identifiant + mot de passe (écran Chauffeurs). Il n'y a pas d'autre type
# d'utilisateur : un chauffeur connecté a accès à toute l'application.
#
# Le jeton JWT (HS256) est signé avec JWT_SECRET, ou à défaut SECRET_KEY —
# l'application fonctionne donc sans variable supplémentaire. Changer ce
# secret déconnecte tout le monde.
JWT_SECRET = env("JWT_SECRET") or SECRET_KEY
# Durée de validité du jeton, en heures (7 jours par défaut : un chauffeur
# qui consulte son planning depuis son téléphone n'a pas à se reconnecter
# chaque jour).
JWT_TTL_HOURS = int(env("JWT_TTL_HOURS", "168"))
# Cookie porteur du jeton. Secure est activé hors debug (HTTPS sur Vercel).
AUTH_COOKIE_NAME = env("AUTH_COOKIE_NAME", "kent_auth")

# Chauffeur amorcé avec un accès à l'application si AUCUN chauffeur n'en a
# (première mise en service, ou accès révoqué à tout le monde par erreur) :
# sans cela, personne ne pourrait plus se connecter. Voir
# app/seeding.py:ensure_login_access().
# `or` plutôt qu'une valeur par défaut d'env() : .env.example livre ces
# clés vides, et une valeur vide doit retomber sur le défaut (un mot de
# passe vide donnerait un compte inutilisable, donc un verrouillage).
BOOTSTRAP_LAST_NAME = env("BOOTSTRAP_LAST_NAME") or "KILINC"
BOOTSTRAP_FIRST_NAME = env("BOOTSTRAP_FIRST_NAME") or "Ismail"
BOOTSTRAP_EMAIL = env("BOOTSTRAP_EMAIL") or "ikilinc07@gmail.com"
BOOTSTRAP_USERNAME = env("BOOTSTRAP_USERNAME") or "ikilinc"
BOOTSTRAP_PASSWORD = env("BOOTSTRAP_PASSWORD") or "kent2026"


# Taille max d'une pièce jointe uploadée. Attention : au-delà de la valeur
# de `max_allowed_packet` de votre serveur MySQL (souvent 4 à 16 Mo sur les
# offres managées), l'insertion du LONGBLOB échouera.
MAX_UPLOAD_MB = int(env("MAX_UPLOAD_MB", "8"))


# --- Base de données (MySQL) -----------------------------------------
# Fournir soit DATABASE_URL (mysql://user:pass@host:port/dbname), soit les
# variables MYSQL_* individuelles. Les MYSQL_* évitent tout souci
# d'encodage : à privilégier si le mot de passe contient @ ou des espaces.
# SSL : MYSQL_SSL explicite (0/1) sinon activé sauf si l'hôte est local.
def _ssl_enabled(host):
    flag = env("MYSQL_SSL")
    if flag is None:
        return host not in ("localhost", "127.0.0.1", "::1", "")
    return flag not in ("0", "false", "no", "")


def _parse_db_url(url):
    """Parseur tolérant : accepte les caractères spéciaux non encodés
    (; ? : / +) dans le mot de passe, contrairement à urllib.parse qui
    lève sur `:` ou `?`. Un « @ » dans le mot de passe reste ambigu ->
    utiliser les variables MYSQL_* dans ce cas."""
    rest = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", "", url.strip())
    creds, sep, hostpart = rest.rpartition("@")
    if not sep:
        creds, hostpart = "", rest
    user, _, password = creds.partition(":")
    hostport, _, dbname = hostpart.partition("/")
    dbname = dbname.split("?", 1)[0]
    host, _, port = hostport.partition(":")
    return {
        "host": unquote(host) or "localhost",
        "port": int(port) if port.isdigit() else 3306,
        "user": unquote(user) or "root",
        "password": unquote(password) if re.search(r"%[0-9A-Fa-f]{2}", password) else password,
        "database": unquote(dbname) or "kent",
    }


def _db_config():
    url = env("DATABASE_URL") or env("MYSQL_URL")
    if url:
        cfg = _parse_db_url(url)
    else:
        cfg = {
            "host": env("MYSQL_HOST", "localhost"),
            "port": int(env("MYSQL_PORT", "3306")),
            "user": env("MYSQL_USER", "root"),
            "password": env("MYSQL_PASSWORD", ""),
            "database": env("MYSQL_DATABASE", "kent"),
        }
    cfg["ssl"] = _ssl_enabled(cfg["host"])
    return cfg


DB_CONFIG = _db_config()


# --- PDF -------------------------------------------------------------
# PDF_ENGINE = "wkhtmltopdf" -> binaire système local (dev)
# PDF_ENGINE = "http"        -> appelle la fonction serverless Node
#                               /api/render_pdf (Chrome headless) — Vercel
PDF_ENGINE = env("PDF_ENGINE", "wkhtmltopdf")
WKHTMLTOPDF_BIN = env("WKHTMLTOPDF_BIN", "wkhtmltopdf")
# Base URL du service de rendu HTTP. Si vide, on utilise VERCEL_URL (auto)
# puis http://localhost:3000 en dernier recours.
PDF_RENDER_URL = env("PDF_RENDER_URL", "")
PDF_RENDER_SECRET = env("PDF_RENDER_SECRET", "")


# --- Coordonnées de l'entreprise (affichées sur l'OM et le BC) ---------
COMPANY = {
    "name": env("COMPANY_NAME", "Transports KENT"),
    "address": env("COMPANY_ADDRESS", "14 Rue du Fer à Cheval"),
    "postal_code": env("COMPANY_POSTAL_CODE", "28200"),
    "city": env("COMPANY_CITY", "ST-DENIS-LANNERAY"),
    "phone": env("COMPANY_PHONE", "07 44 90 60 38"),
    "siret": env("COMPANY_SIRET", "93073109600010"),
}
OM_LEGAL_REF = env("OM_LEGAL_REF", "28/11/2011 Art1-1er-2")
BC_LEGAL_REF = env("BC_LEGAL_REF", "28/12/2011")


# --- Email -------------------------------------------------------------
# SMTP_AUTH_METHOD = "basic"       -> SMTP classique (host/port/user/pass)
# SMTP_AUTH_METHOD = "oauth2_o365" -> Microsoft 365 / Exchange Online (MSAL)
SMTP_AUTH_METHOD = env("SMTP_AUTH_METHOD", "basic")

SMTP_HOST = env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USER = env("SMTP_USER", "")
SMTP_PASS = env("SMTP_PASS", "")
SMTP_FROM_NAME = env("SMTP_FROM_NAME", COMPANY["name"])
SMTP_FROM_EMAIL = env("SMTP_FROM_EMAIL", SMTP_USER)

# Pour SMTP_AUTH_METHOD=oauth2_o365 :
O365_TENANT_ID = env("O365_TENANT_ID", "")
O365_CLIENT_ID = env("O365_CLIENT_ID", "")
O365_CLIENT_SECRET = env("O365_CLIENT_SECRET", "")
O365_SENDER_EMAIL = env("O365_SENDER_EMAIL", SMTP_USER)

# Formulations proposées pour le libellé d'un trajet « (pause) » dans le
# formulaire de mission. Séparateur « | » plutôt que la virgule : un
# libellé peut en contenir une (« Pause déjeuner, 45 min »). Le champ
# reste libre — la liste ne fait qu'éviter de retaper les plus courantes.
PAUSE_LABELS = [p.strip() for p in env(
    "PAUSE_LABELS", "Pause + Attente Clients|Pause 15 min|Pause 30 min"
).split("|") if p.strip()]

# Client épinglé en tête du menu déroulant "Client" du formulaire de
# mission (le reste de la liste est alphabétique).
PINNED_CLIENT_NAME = env("PINNED_CLIENT_NAME", "Simplon Voyages")

# --- Planning / partage calendrier -------------------------------------
# Secret pour le flux iCalendar public GET /planning/calendrier.ics?token=...
# (abonnement iPhone/Android). Laisser vide désactive le flux (404), comme
# SEED_SECRET pour /admin/init.
CALENDAR_FEED_TOKEN = env("CALENDAR_FEED_TOKEN", "")

# --- Estimation des durées de trajet ----------------------------------
# Clé TomTom (developer.tomtom.com) pour le calcul d'itinéraire avec
# trafic à l'heure de départ. Sans clé, le bouton « Estimer TomTom » renvoie
# un message explicite et le reste de l'application fonctionne normalement.
# Le géocodage passe lui par la Base Adresse Nationale (gratuite, sans clé).
TOMTOM_API_KEY = env("TOMTOM_API_KEY", "")

# Clé Maps JavaScript API (console.cloud.google.com), avec les API
# « Places API », « Distance Matrix API » et « Directions API » activées
# (cette dernière pour l'écran Plan de Ramassage). Contrairement à
# TOMTOM_API_KEY, cette clé est utilisée UNIQUEMENT côté navigateur (c'est
# le fonctionnement normal d'une clé « Maps JavaScript API » chez Google :
# on la restreint par « référents HTTP » dans la console Google Cloud,
# plutôt que de la garder secrète côté serveur). Alimente à la fois
# l'autocomplétion d'adresse (si le réglage « Recherche d'adresse », écran
# Réglages, est sur Google) et le bouton « Estimer avec Maps » des lignes de
# trajet (toujours proposé, indépendamment de ce réglage), ainsi que l'écran
# Plan de Ramassage : carte, ordre de passage le plus court et distances par
# la route (sans clé, l'ordre est calculé côté serveur et la carte n'est pas
# affichée — voir app/routes/tours.py). Accepte aussi
# NEXT_PUBLIC_GOOGLE_MAPS_API_KEY comme alias, pour reprendre une clé déjà
# nommée ainsi ailleurs.
GOOGLE_MAPS_API_KEY = env("GOOGLE_MAPS_API_KEY") or env("NEXT_PUBLIC_GOOGLE_MAPS_API_KEY", "")
