"""
Génération du PDF final d'une mission : OM (page 1) + pièces jointes
positionnées + BC (dernières pages), à partir des templates HTML/Jinja2
stockés en base.

Pipeline :
  1. Charger le template actif (ou celui choisi sur la mission) pour OM et BC
  2. Construire le contexte de données (driver, legs/stops, société...)
  3. Rendre le HTML avec Jinja2
  4. Convertir chaque HTML en PDF avec wkhtmltopdf (sous-processus, stdin/stdout)
  5. Fusionner OM + pièces jointes (triées par position d'insertion) + BC avec pypdf

Remplacer le moteur HTML -> PDF (WeasyPrint, Playwright/Chromium...) ne
touche que render_html_to_pdf().
"""
import base64
import json
import os
import subprocess
import urllib.request
from io import BytesIO

from jinja2 import Template
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from PIL import Image

from app import repo
from app.config import (
    COMPANY, OM_LEGAL_REF, BC_LEGAL_REF, BASE_DIR,
    PDF_ENGINE, WKHTMLTOPDF_BIN, PDF_RENDER_URL, PDF_RENDER_SECRET,
)
from app.utils import fmt_time, fmt_date_short, fmt_date_long, day_label, shuttle_number, is_depot

LOGO_PATH = BASE_DIR / "app" / "static" / "img" / "logo.png"
_logo_b64_cache = None

# Valeurs de "insert_after_page" utilisées par le formulaire de pièces
# jointes (voir routes/missions.py) pour les 3 zones d'insertion possibles.
POSITION_BEFORE_OM = 0
POSITION_AFTER_OM = 1      # juste après l'OM = page 2, comme les documents d'origine
POSITION_AFTER_BC = 9999   # après tout, à la fin du document


class PdfGenerationError(Exception):
    pass


def _parse_page_spec(spec: str, n_pages: int) -> list:
    """« 1,3,5-7 » (1-indexé) -> [0, 2, 4, 5, 6] (indices 0-based, triés,
    dédupliqués, hors-limites ignorés)."""
    indexes = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a, b = int(a), int(b)
        else:
            a = b = int(part)
        for p in range(min(a, b), max(a, b) + 1):
            if 1 <= p <= n_pages:
                indexes.add(p - 1)
    if not indexes:
        raise ValueError(f"aucune page valide dans « {spec} » (document de {n_pages} page(s))")
    return sorted(indexes)


def extract_pdf_pages(pdf_bytes: bytes, pages_spec: str) -> bytes:
    """Extrait les pages indiquées (ex. "1,3,5-7") d'un PDF — utilisé par
    l'upload de pièce jointe quand l'utilisateur ne veut garder que
    certaines pages d'un PDF plus long (choisies via les miniatures
    PDF.js côté navigateur)."""
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        n_pages = len(reader.pages)
    except Exception as e:
        raise PdfGenerationError(f"PDF illisible : {e}") from e
    try:
        indexes = _parse_page_spec(pages_spec, n_pages)
    except ValueError as e:
        raise PdfGenerationError(str(e)) from e
    writer = PdfWriter()
    for i in indexes:
        writer.add_page(reader.pages[i])
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def get_logo_base64():
    global _logo_b64_cache
    if _logo_b64_cache is None:
        _logo_b64_cache = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    return _logo_b64_cache


def render_html_to_pdf(html: str) -> bytes:
    """Convertit une chaîne HTML en octets PDF.

    PDF_ENGINE=wkhtmltopdf : binaire système local (développement).
    PDF_ENGINE=http        : appelle la fonction serverless Node
                             /api/render_pdf (Chrome headless) — Vercel.
    """
    if PDF_ENGINE == "http":
        return _render_via_http(html)
    return _render_via_wkhtmltopdf(html)


def _render_via_wkhtmltopdf(html: str) -> bytes:
    proc = subprocess.run(
        [WKHTMLTOPDF_BIN, "--quiet", "--enable-local-file-access", "-", "-"],
        input=html.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise PdfGenerationError(
            f"wkhtmltopdf a échoué (code {proc.returncode}): {proc.stderr.decode('utf-8', 'ignore')[:500]}"
        )
    return proc.stdout


def _pdf_render_base_url() -> str:
    if PDF_RENDER_URL:
        return PDF_RENDER_URL.rstrip("/")
    # Le domaine de production (planning.kent-transports.com) n'est pas
    # derrière la « Deployment Protection », contrairement à VERCEL_URL (URL
    # de déploiement, protégée par SSO). On le préfère donc pour l'appel interne.
    prod = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
    if prod:
        return f"https://{prod}"
    vercel = os.environ.get("VERCEL_URL")
    if vercel:
        return f"https://{vercel}"
    return "http://localhost:3000"


def _render_via_http(html: str) -> bytes:
    url = _pdf_render_base_url() + "/api/render_pdf"
    payload = json.dumps({"html": html}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if PDF_RENDER_SECRET:
        headers["X-Render-Secret"] = PDF_RENDER_SECRET
    # Si la « Protection Bypass for Automation » est activée, Vercel injecte
    # ce secret : il ouvre aussi les URLs de déploiement protégées.
    bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET")
    if bypass:
        headers["x-vercel-protection-bypass"] = bypass
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = resp.read()
    except Exception as e:
        detail = ""
        body = getattr(e, "read", None)
        if callable(body):
            try:
                detail = " — " + body().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
        raise PdfGenerationError(f"Service de rendu PDF injoignable ({url}): {e}{detail}")
    if not out.startswith(b"%PDF"):
        raise PdfGenerationError(f"Rendu PDF invalide reçu: {out[:200]!r}")
    return out


def _format_leg_label(label):
    """Formate un libellé de trajet pour l'OM.

    - « VILLE1, adr1 → VILLE2, adr2 » (le format produit par « Générer les
      trajets depuis les arrêts ») -> ("split", de_html, à_html), chaque
      côté sur 2 lignes (ville en gras, adresse dessous) pour un rendu en
      2 colonnes + flèche, comme le BC.
    - tout le reste (pause, point de contrôle, relais, texte libre, ou une
      seule adresse sans virgule) -> ("single", html) sur une cellule
      fusionnée ; la ville est quand même mise en gras si le motif
      "VILLE, adresse" est présent.
    """
    if not label:
        return ("single", label)

    def side_html(side, stacked):
        if is_depot(side):
            return f"<strong>{side.strip()}</strong>"
        if ", " in side:
            city, rest = side.split(", ", 1)
            return f"<strong>{city}</strong>{'<br>' if stacked else ', '}{rest}"
        return side

    parts = label.split(" → ")
    if len(parts) == 2:
        return ("split", side_html(parts[0], True), side_html(parts[1], True))
    return ("single", side_html(label, False))


def _default_passenger_count(stops):
    pec = sum(s.get("passenger_count") or 1 for s in stops if s["stop_type"] == "prise_en_charge")
    dep = sum(s.get("passenger_count") or 1 for s in stops if s["stop_type"] == "depose")
    return max(pec, dep)


def build_om_context(mission):
    driver = mission["driver"]
    legs = []
    for leg in mission["legs"]:
        formatted = _format_leg_label(leg["label"])
        leg_ctx = {
            "start_time": fmt_time(leg["start_time"]),
            "end_time": fmt_time(leg["end_time"]),
            "vehicle": leg.get("vehicle_plate"),
            "is_checkpoint": bool(leg["is_checkpoint"]),
            "is_relay": bool(leg.get("is_relay")),
            "label_kind": formatted[0],
        }
        if formatted[0] == "split":
            leg_ctx["from_html"], leg_ctx["to_html"] = formatted[1], formatted[2]
        else:
            leg_ctx["label"] = formatted[1]
        legs.append(leg_ctx)
    return {
        "logo_base64": get_logo_base64(),
        "company": COMPANY,
        "om_legal_ref": OM_LEGAL_REF,
        "mission_name": mission.get("mission_name") or "",
        "shuttle_number": shuttle_number(mission.get("shuttle_label")),
        "driver_name": f"{driver['last_name']} {driver['first_name']}",
        "mission_day_label": day_label(mission["mission_date"]),
        "mission_date_label": fmt_date_long(mission["mission_date"]),
        "legs": legs,
        "remarks": mission.get("remarks") or "",
    }


def _bc_client(mission):
    """Bloc « Client / donneur d'ordre » du Billet Collectif.

    Ce sont les coordonnées saisies sur la mission (bc_client_*), recopiées
    depuis la fiche client au moment de la sélection puis éventuellement
    retouchées pour ce BC-là. La fiche client ne sert que de repli, pour les
    missions antérieures à ce champ : un BC déjà émis ne doit pas changer
    parce qu'on corrige la fiche du client des mois plus tard.
    """
    client = mission.get("client") or {}
    fields = {
        "name": mission.get("bc_client_name") or client.get("name") or "",
        "address": mission.get("bc_client_address") or client.get("address") or "",
        "postal_code": mission.get("bc_client_postal_code") or client.get("postal_code") or "",
        "city": mission.get("bc_client_city") or client.get("city") or "",
        "phone": (mission.get("bc_client_phone")
                  or client.get("phone") or client.get("email") or ""),
    }
    return fields if any(fields.values()) else None


def build_bc_context(mission):
    driver = mission["driver"]
    stops = mission["stops"]
    stop_ctx = []
    for s in stops:
        stop_ctx.append({
            "stop_type": s["stop_type"],
            "date_label": fmt_date_short(s["stop_date"]),
            "time": fmt_time(s["stop_time"]),
            "address": s["address"],
            "city": s.get("city") or "",
        })
    return {
        "logo_base64": get_logo_base64(),
        "company": COMPANY,
        "bc_legal_ref": BC_LEGAL_REF,
        "motif": mission.get("motif") or "Transport Occasionnel",
        "driver_name": f"{driver['last_name']} {driver['first_name']}",
        "stops": stop_ctx,
        "passenger_count": _default_passenger_count(stops),
        # Prix masqué : vidé de la variable, et `show_price` permet au
        # gabarit de retirer la ligne entière (le gabarit livré le fait).
        # Les deux, pour qu'un gabarit personnalisé plus ancien, qui ne
        # connaît pas `show_price`, n'affiche au moins aucun montant.
        "price": "" if mission.get("price_hidden") else (mission.get("price") or ""),
        "show_price": not mission.get("price_hidden"),
        "client": _bc_client(mission),
        "emission_date_label": fmt_date_long(mission.get("emission_date") or mission["mission_date"]),
    }


def render_template_string(source_html: str, context: dict) -> bytes:
    html = Template(source_html).render(**context)
    return render_html_to_pdf(html)


def _attachment_to_pdf_bytes(attachment) -> bytes:
    content = attachment["content"]
    content_type = (attachment.get("content_type") or "").lower()
    if content_type == "application/pdf" or attachment["filename"].lower().endswith(".pdf"):
        return bytes(content)
    # Image (jpg/png/...) -> on l'enveloppe dans une page A4 pour l'insérer proprement.
    img = Image.open(BytesIO(content)).convert("RGB")
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    margin = 20
    max_w, max_h = page_w - 2 * margin, page_h - 2 * margin
    scale = min(max_w / img.width, max_h / img.height)
    draw_w, draw_h = img.width * scale, img.height * scale
    x = (page_w - draw_w) / 2
    y = (page_h - draw_h) / 2
    c.drawImage(ImageReader(img), x, y, width=draw_w, height=draw_h)
    c.showPage()
    c.save()
    return buf.getvalue()


def _mission_filename(mission, prefix=""):
    driver = mission["driver"]
    date_compact = (mission["mission_date"] or "").replace("-", "")
    return f"{prefix}{driver['last_name'].upper()}_{date_compact}.pdf"


def generate_bc_pdf(mission_id: int):
    """Billet Collectif seul (bouton « Télécharger BC » de la fiche) :
    ni l'Ordre de Mission, ni les pièces jointes."""
    mission = repo.get_mission(mission_id)
    if not mission:
        raise PdfGenerationError("Mission introuvable")
    bc_template = (
        repo.get_template(mission["bc_template_id"]) if mission.get("bc_template_id")
        else repo.get_active_template("BC")
    )
    if not bc_template:
        raise PdfGenerationError("Aucun template actif pour le BC (voir Réglages > Templates)")
    pdf_bytes = render_template_string(bc_template["definition_html"], build_bc_context(mission))
    return pdf_bytes, _mission_filename(mission, prefix="BC_")


def generate_mission_pdf(mission_id: int):
    """Retourne (pdf_bytes, filename) pour la mission donnée."""
    mission = repo.get_mission(mission_id)
    if not mission:
        raise PdfGenerationError("Mission introuvable")

    om_template = (
        repo.get_template(mission["om_template_id"]) if mission.get("om_template_id")
        else repo.get_active_template("OM")
    )
    bc_template = (
        repo.get_template(mission["bc_template_id"]) if mission.get("bc_template_id")
        else repo.get_active_template("BC")
    )
    if not om_template or not bc_template:
        raise PdfGenerationError("Aucun template actif pour l'OM ou le BC (voir Réglages > Templates)")

    om_pdf = render_template_string(om_template["definition_html"], build_om_context(mission))
    bc_pdf = render_template_string(bc_template["definition_html"], build_bc_context(mission))

    writer = PdfWriter()
    om_pages = list(PdfReader(BytesIO(om_pdf)).pages)
    bc_pages = list(PdfReader(BytesIO(bc_pdf)).pages)

    # Trois zones d'insertion pour les pièces jointes (voir POSITION_* dans
    # ce module, utilisées par le formulaire) : avant l'OM, entre l'OM et le
    # BC (par défaut, ex. la feuille de référence qui devient la page 2
    # comme dans les documents d'origine), après le BC. À l'intérieur d'une
    # même zone, l'ordre choisi par l'utilisateur (position) est respecté.
    attachments = sorted(repo.list_attachment_contents(mission_id), key=lambda a: a["position"])
    before_om, between, after_bc = [], [], []
    for att in attachments:
        pages = list(PdfReader(BytesIO(_attachment_to_pdf_bytes(att))).pages)
        if att["insert_after_page"] <= POSITION_BEFORE_OM:
            before_om.extend(pages)
        elif att["insert_after_page"] >= POSITION_AFTER_BC:
            after_bc.extend(pages)
        else:
            between.extend(pages)

    for p in before_om:
        writer.add_page(p)
    for p in om_pages:
        writer.add_page(p)
    for p in between:
        writer.add_page(p)
    for p in bc_pages:
        writer.add_page(p)
    for p in after_bc:
        writer.add_page(p)

    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue(), _mission_filename(mission)
