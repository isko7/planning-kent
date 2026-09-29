"""
Envoi de l'Ordre de Mission par email, en pièce jointe PDF.

Deux modes, choisis par SMTP_AUTH_METHOD dans .env :

- "basic" (par défaut) : SMTP classique avec identifiant/mot de passe,
  fonctionne avec Gmail (mot de passe d'application), OVH, Infomaniak,
  la plupart des hébergeurs. C'est le mode testé dans cet environnement
  (le code ci-dessous est du smtplib standard).

- "oauth2_o365" : Microsoft 365 / Exchange Online, via l'API **Microsoft
  Graph** (`POST /users/{expéditeur}/sendMail`), en OAuth2 client-
  credentials (msal). On n'utilise volontairement PAS le protocole SMTP
  AUTH classique : il est traité comme une « authentification legacy »
  par Microsoft et bloqué dès que les Security Defaults / Conditional
  Access sont actifs sur le tenant (cas de la plupart des tenants créés
  récemment) — désactiver cette protection tenant-wide juste pour
  l'email est un compromis de sécurité qu'on préfère éviter. Graph est
  une API REST « moderne », non concernée par ce blocage.
  Voir README > Configuration email pour le setup Azure AD complet
  (App registration, permissions Graph `Mail.Send` et `Mail.ReadWrite`,
  admin consent).

Un timeout explicite est posé sur les appels réseau : sans lui, un
serveur injoignable (mauvais host, pare-feu, etc.) peut faire attendre
la requête indéfiniment plutôt que d'échouer proprement.

Regroupement en fil de discussion (bouton « Envoyer l'itinéraire ») :

- en "basic", on pose nous-mêmes le Message-ID de chaque envoi et on le
  journalise ; l'email suivant porte In-Reply-To / References et les
  clients le rattachent au premier de façon certaine ;
- en "oauth2_o365", `sendMail` ne renvoie rien et n'accepte pas d'en-tête
  In-Reply-To. On passe donc par un brouillon : POST /messages donne un
  `internetMessageId` avant l'envoi, puis /send expédie, et la copie est
  déplacée de « Éléments envoyés » vers le dossier SENT_FOLDER_NAME. C'est
  l'identifiant de ce message rangé qu'on journalise, et sur lequel
  createReply construit plus tard une vraie réponse.

Deux effets recherchés par ce rangement : les envois de l'application ne
se mélangent pas aux envois faits à la main depuis Outlook, et une
stratégie de rétention peut vider ce seul dossier.

Tant que la permission Graph `Mail.ReadWrite` n'est pas accordée, la
préparation du brouillon échoue : on se rabat alors sur l'ancien
`sendMail` (sans copie ni fil). Les emails partent donc dans tous les cas,
et le fil s'améliore de lui-même une fois la permission en place.
"""
import base64
import html
import json
import re
import smtplib
import logging
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import make_msgid

from app import repo
from app.config import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM_NAME, SMTP_FROM_EMAIL,
    SMTP_AUTH_METHOD, O365_TENANT_ID, O365_CLIENT_ID, O365_CLIENT_SECRET, O365_SENDER_EMAIL,
)

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# Limite documentée de sendMail avec pièces jointes en base64 inline ; au-delà
# il faudrait un upload session (non implémenté ici, cas rare pour des Ordres de Mission).
GRAPH_MAX_MESSAGE_BYTES = 4 * 1024 * 1024

# Ligne "Itinéraire : <url>" ajoutée par _driver_email_defaults() (missions.py)
# quand le chauffeur a coché "Envoyer l'itinéraire" : dans la version HTML de
# l'email, on l'affiche comme un lien cliquable ("Itinéraire") plutôt que
# l'URL brute — la version texte brut (fallback) garde elle l'URL en clair.
_ITINERARY_LINE_RE = re.compile(r"^Itin[ée]raire\s*:\s*(\S+)$")

# Lignes « <lieu> : <lien Waze> » ajoutées par l'envoi d'itinéraire
# (missions.py:send_itinerary). En HTML, c'est l'adresse elle-même qui
# devient le lien : la liste se lit comme une suite d'arrêts, et l'URL
# brute — longue et encodée — n'encombre pas. La version texte, elle, la
# garde en clair, faute de pouvoir rendre quoi que ce soit cliquable.
_WAZE_LINE_RE = re.compile(r"^(.+?)\s*:\s*(https://\S*waze\.com/\S+)$")


class EmailError(Exception):
    pass


# Styles en ligne : les clients de messagerie ignorent les feuilles de
# style (Gmail retire <style>), et l'espacement doit venir des marges de
# vrais blocs — empiler des <br> donne des écarts irréguliers d'un client
# à l'autre, c'est ce qui rendait la liste des arrêts bancale.
_HTML_WRAPPER = ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
                 'line-height:1.5;color:#1b1c21;">')
_STYLE_PARAGRAPHE = "margin:0 0 14px;"
_STYLE_BOUTON = ("display:inline-block;padding:11px 18px;background:#d6293a;color:#ffffff;"
                 "text-decoration:none;border-radius:6px;font-weight:bold;")
_STYLE_TITRE = "margin:26px 0 10px;font-weight:bold;"
_STYLE_ARRET = ("display:block;padding:12px 14px;background:#f5f6f8;border:1px solid #dcdfe4;"
                "border-radius:6px;color:#1d63d8;text-decoration:none;font-weight:bold;")


def _paragraphe(lignes, apres_bloc=False):
    """Un bloc de lignes consécutives -> un paragraphe, lignes séparées par
    des <br>. Les lignes vides ne servent qu'à délimiter les paragraphes :
    elles ne produisent pas d'espace supplémentaire, d'où un interligne
    identique partout. `apres_bloc` détache le paragraphe de la pastille
    ou du bouton qui le précède."""
    contenu = "<br>".join(html.escape(l) for l in lignes)
    marge = "margin:20px 0 14px;" if apres_bloc else _STYLE_PARAGRAPHE
    return f'<p style="{marge}">{contenu}</p>'


def _body_to_html(body):
    """Version HTML du corps. Trois lignes reçoivent une mise en forme
    propre : l'itinéraire Google Maps devient un bouton, le titre de la
    liste Waze un intertitre, et chaque arrêt une pastille cliquable
    pleine largeur — assez haute pour être visée du doigt."""
    lignes = body.split("\n")
    blocs, tampon = [], []
    # Vrai quand le dernier élément posé est un bouton, un intertitre ou
    # une pastille d'arrêt : le paragraphe suivant s'en écarte un peu.
    apres_bloc = [False]

    def vider():
        """Le texte en attente devient un paragraphe par groupe de lignes
        consécutives. Les lignes vides ne font que séparer les groupes :
        elles ne produisent aucun espace propre, sans quoi deux lignes
        vides d'affilée creuseraient un écart plus grand qu'ailleurs — la
        source des trous irréguliers."""
        groupe = []
        for ligne in tampon:
            if ligne.strip():
                groupe.append(ligne)
                continue
            if groupe:
                blocs.append(_paragraphe(groupe, apres_bloc[0]))
                apres_bloc[0] = False
                groupe = []
        if groupe:
            blocs.append(_paragraphe(groupe, apres_bloc[0]))
            apres_bloc[0] = False
        tampon.clear()

    for i, ligne in enumerate(lignes):
        nue = ligne.strip()

        m = _ITINERARY_LINE_RE.match(nue)
        if m:
            vider()
            url = html.escape(m.group(1), quote=True)
            blocs.append(f'<div style="margin:0 0 6px;"><a href="{url}" '
                         f'style="{_STYLE_BOUTON}">Itinéraire complet — Google Maps</a></div>')
            apres_bloc[0] = True
            continue

        m = _WAZE_LINE_RE.match(nue)
        if m:
            vider()
            place = html.escape(m.group(1))
            url = html.escape(m.group(2), quote=True)
            blocs.append(f'<div style="margin:0 0 8px;"><a href="{url}" '
                         f'style="{_STYLE_ARRET}">{place}</a></div>')
            apres_bloc[0] = True
            continue

        # Ligne d'introduction de la liste des arrêts : reconnue à ce qui la
        # suit, pour ne pas figer sa formulation ici.
        suivante = lignes[i + 1].strip() if i + 1 < len(lignes) else ""
        if nue.endswith(":") and _WAZE_LINE_RE.match(suivante):
            vider()
            blocs.append(f'<div style="{_STYLE_TITRE}">{html.escape(nue.rstrip(":").strip())}</div>')
            apres_bloc[0] = True
            continue

        tampon.append(ligne)

    vider()
    return "<html><body>" + _HTML_WRAPPER + "".join(blocs) + "</div></body></html>"


# --------------------------------------------------------- mode "basic"
def _send_via_smtp(msg: EmailMessage):
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT_SECONDS) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)


def _build_message(to_addresses, cc_addresses, subject, body, attachments, in_reply_to=None):
    """attachments : liste de (pdf_bytes, filename). `in_reply_to` : le
    Message-ID de l'email auquel celui-ci répond — les clients de messagerie
    regroupent alors les deux dans le même fil."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    msg["To"] = ", ".join(to_addresses)
    # Message-ID posé par nous plutôt que par le serveur : c'est lui qu'on
    # journalise, pour pouvoir répondre dans le fil plus tard.
    msg["Message-ID"] = make_msgid(domain=(SMTP_FROM_EMAIL.split("@")[-1] or None))
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    if cc_addresses:
        msg["Cc"] = ", ".join(cc_addresses)
    msg.set_content(body)
    msg.add_alternative(_body_to_html(body), subtype="html")
    for pdf_bytes, pdf_filename in attachments:
        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=pdf_filename)
    return msg


# ----------------------------------------------------- mode "oauth2_o365"
# Dossier de la boîte expéditrice où l'application range tout ce qu'elle
# envoie. Il la tient hors de « Éléments envoyés » (réservé aux envois faits
# à la main depuis Outlook) et permet de poser dessus, et sur lui seul, une
# stratégie de rétention qui le vide régulièrement.
# Renommer ce dossier ici suppose de le renommer aussi côté Outlook.
SENT_FOLDER_NAME = "Planning KENT"

# Id du dossier, retenu le temps du process : une invocation serverless
# « chaude » ne le recherche qu'une fois.
_folder_id_cache = {}


class GraphError(EmailError):
    """Erreur d'un appel Graph. `status` = code HTTP, ou 0 si l'API n'a même
    pas répondu — ce qui distingue « permission refusée » d'un incident
    réseau."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _get_graph_access_token():
    try:
        import msal
    except ImportError as e:
        raise EmailError(
            "SMTP_AUTH_METHOD=oauth2_o365 nécessite le paquet 'msal' "
            "(déjà dans requirements.txt — redéployez si l'erreur persiste)"
        ) from e
    app = msal.ConfidentialClientApplication(
        O365_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{O365_TENANT_ID}",
        client_credential=O365_CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise EmailError(f"Échec d'obtention du jeton Graph : {result.get('error_description')}")
    return result["access_token"]


def _graph(token, method, path, payload=None):
    """Un appel Graph. Renvoie le JSON de la réponse, ou None si le corps
    est vide (202/204)."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        GRAPH_BASE + path, data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:500]
        raise GraphError(e.code, f"Microsoft Graph a refusé {method} {path} ({e.code}) : {detail}") from e
    except Exception as e:
        raise GraphError(0, f"Microsoft Graph injoignable : {e}") from e
    return json.loads(raw.decode("utf-8")) if raw else None


def _mailbox():
    return f"/users/{O365_SENDER_EMAIL}"


def _graph_message(to_addresses, cc_addresses, subject, body, attachments):
    """Le message au format Graph, pièces jointes en base64 dans la requête."""
    html_body = _body_to_html(body)
    graph_attachments = []
    total_bytes = len(html_body.encode("utf-8"))
    for pdf_bytes, filename in attachments:
        total_bytes += len(pdf_bytes)
        graph_attachments.append({
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": filename,
            "contentType": "application/pdf",
            "contentBytes": base64.b64encode(pdf_bytes).decode("ascii"),
        })
    if total_bytes > GRAPH_MAX_MESSAGE_BYTES:
        raise EmailError(
            f"Message trop volumineux pour l'API Graph ({total_bytes // 1024} Ko, limite ~4 Mo) "
            "— réduisez le nombre ou la taille des pièces jointes."
        )
    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [{"emailAddress": {"address": a}} for a in to_addresses],
        "attachments": graph_attachments,
    }
    if cc_addresses:
        message["ccRecipients"] = [{"emailAddress": {"address": a}} for a in cc_addresses]
    return message


def _sent_folder_id(token):
    """Id du dossier de rangement, créé s'il n'existe pas encore."""
    cached = _folder_id_cache.get(SENT_FOLDER_NAME)
    if cached:
        return cached
    # Filtrage côté client : un $filter sur displayName est fragile dès que
    # le nom porte une apostrophe ou un accent, et la boîte n'a pas des
    # centaines de dossiers à la racine.
    listing = _graph(token, "GET", f"{_mailbox()}/mailFolders?$top=100&$select=id,displayName")
    for folder in (listing or {}).get("value", []):
        if folder.get("displayName") == SENT_FOLDER_NAME:
            _folder_id_cache[SENT_FOLDER_NAME] = folder["id"]
            return folder["id"]
    created = _graph(token, "POST", f"{_mailbox()}/mailFolders",
                     {"displayName": SENT_FOLDER_NAME})
    _folder_id_cache[SENT_FOLDER_NAME] = created["id"]
    return created["id"]


def _file_sent_message(token, internet_message_id):
    """Sort de « Éléments envoyés » la copie du message qui vient de partir
    et la range dans SENT_FOLDER_NAME. Renvoie son nouvel identifiant Graph
    (il change à chaque déplacement), ou None si elle est introuvable.

    On la retrouve par son internetMessageId : l'identifiant du brouillon ne
    vaut plus rien une fois le message envoyé."""
    query = urllib.parse.urlencode({
        "$filter": f"internetMessageId eq '{internet_message_id}'",
        "$select": "id", "$top": 1,
    })
    found = _graph(token, "GET", f"{_mailbox()}/mailFolders/sentitems/messages?{query}")
    items = (found or {}).get("value") or []
    if not items:
        return None
    moved = _graph(token, "POST", f"{_mailbox()}/messages/{items[0]['id']}/move",
                   {"destinationId": _sent_folder_id(token)})
    return (moved or {}).get("id")


def _graph_sendmail(token, message):
    """Repli : l'envoi direct d'autrefois, sans brouillon ni rangement. Sert
    tant que la permission Mail.ReadWrite n'est pas accordée — mieux vaut un
    email parti sans classement qu'un email pas parti du tout."""
    _graph(token, "POST", f"{_mailbox()}/sendMail",
           {"message": message, "saveToSentItems": False})


def _send_via_graph(to_addresses, cc_addresses, subject, body, attachments,
                    reply_to_graph_id=None):
    """Envoie par Graph puis range la copie. Renvoie
    (internetMessageId, id du message rangé) — (None, None) si l'envoi est
    passé par le repli sendMail, qui ne laisse rien derrière lui.

    `reply_to_graph_id` : identifiant Graph d'un message déjà rangé. L'envoi
    devient alors une vraie réponse (createReply pose In-Reply-To,
    References et l'objet « RE: »), ce qui regroupe les deux messages dans
    la boîte du destinataire. createReply répond à l'expéditeur du message
    d'origine — c'est-à-dire nous — d'où la réécriture des destinataires."""
    token = _get_graph_access_token()
    message = _graph_message(to_addresses, cc_addresses, subject, body, attachments)

    # Rien n'est encore parti à ce stade : si la préparation échoue
    # (permission manquante, API indisponible), le repli ne risque pas
    # d'envoyer deux fois.
    try:
        if reply_to_graph_id:
            draft = _graph(token, "POST",
                           f"{_mailbox()}/messages/{reply_to_graph_id}/createReply", {})
            _graph(token, "PATCH", f"{_mailbox()}/messages/{draft['id']}", {
                "toRecipients": message["toRecipients"],
                "ccRecipients": message.get("ccRecipients", []),
                "body": message["body"],
            })
            # L'objet « RE: ... » posé par createReply est celui du fil : on
            # ne le remplace pas par le nôtre.
            draft = _graph(token, "GET",
                           f"{_mailbox()}/messages/{draft['id']}?$select=id,internetMessageId")
        else:
            draft = _graph(token, "POST", f"{_mailbox()}/messages", message)
    except GraphError:
        _graph_sendmail(token, message)
        return None, None

    draft_id = draft["id"]
    internet_message_id = draft.get("internetMessageId")
    try:
        _graph(token, "POST", f"{_mailbox()}/messages/{draft_id}/send")
    except GraphError:
        # L'envoi a échoué : on retire le brouillon pour ne pas le laisser
        # traîner, puis on remonte l'erreur — surtout pas de second envoi.
        try:
            _graph(token, "DELETE", f"{_mailbox()}/messages/{draft_id}")
        except GraphError:
            pass
        raise

    # À partir d'ici l'email est parti : un échec de rangement ne doit pas
    # se faire passer pour un échec d'envoi.
    try:
        return internet_message_id, _file_sent_message(token, internet_message_id)
    except GraphError as e:
        log.warning("Email envoyé mais non rangé dans « %s » : %s", SENT_FOLDER_NAME, e)
        return internet_message_id, None


# --------------------------------------------------------------- dispatch
def _send(to_addresses, cc_addresses, subject, body, attachments,
          in_reply_to=None, reply_to_graph_id=None):
    """Renvoie (message_id, provider_message_id) :

    - `message_id` : l'en-tête Message-ID / internetMessageId du message
      parti, journalisé pour pouvoir répondre dans le fil plus tard ;
    - `provider_message_id` : l'identifiant du message rangé côté Graph, le
      seul qui permette un vrai createReply. Toujours None en SMTP, où le
      fil se tient par In-Reply-To.

    `in_reply_to` (SMTP) et `reply_to_graph_id` (Graph) désignent la même
    chose — l'email auquel celui-ci répond — dans les termes de chaque
    mode."""
    if SMTP_AUTH_METHOD == "oauth2_o365":
        return _send_via_graph(to_addresses, cc_addresses, subject, body, attachments,
                               reply_to_graph_id=reply_to_graph_id)
    msg = _build_message(to_addresses, cc_addresses, subject, body, attachments, in_reply_to)
    _send_via_smtp(msg)
    return msg["Message-ID"], None


def send_mission_email(mission_id, to_addresses, cc_addresses, subject, body, pdf_bytes, pdf_filename):
    """to_addresses / cc_addresses : listes de chaînes email. Une seule
    mission -> un seul PDF joint, journalisé sur cette mission."""
    try:
        message_id, provider_id = _send(to_addresses, cc_addresses, subject, body,
                                        [(pdf_bytes, pdf_filename)])
    except Exception as e:
        repo.log_email(
            mission_id, ", ".join(to_addresses), ", ".join(cc_addresses or []),
            subject, body, status="failed", error_message=str(e),
        )
        raise EmailError(str(e)) from e

    repo.log_email(
        mission_id, ", ".join(to_addresses), ", ".join(cc_addresses or []),
        subject, body, status="sent", message_id=message_id,
        provider_message_id=provider_id,
    )


def send_followup_email(mission_id, to_addresses, subject, body,
                        in_reply_to=None, reply_to_graph_id=None):
    """Email sans pièce jointe qui prolonge un envoi précédent (bouton
    « Envoyer l'itinéraire » de la fiche mission) : les deux se retrouvent
    dans le même fil chez le destinataire."""
    try:
        message_id, provider_id = _send(to_addresses, [], subject, body, [],
                                        in_reply_to=in_reply_to,
                                        reply_to_graph_id=reply_to_graph_id)
    except Exception as e:
        repo.log_email(mission_id, ", ".join(to_addresses), "", subject, body,
                       status="failed", error_message=str(e))
        raise EmailError(str(e)) from e

    repo.log_email(mission_id, ", ".join(to_addresses), "", subject, body,
                   status="sent", message_id=message_id, provider_message_id=provider_id)


def send_bulk_email(mission_ids, to_addresses, cc_addresses, subject, body, attachments):
    """Envoi groupé : plusieurs missions dans un seul email, un PDF par
    mission en pièce jointe. Journalise l'envoi sur chacune des missions
    (visible dans leur historique respectif)."""
    to_str, cc_str = ", ".join(to_addresses), ", ".join(cc_addresses or [])
    try:
        message_id, provider_id = _send(to_addresses, cc_addresses, subject, body, attachments)
    except Exception as e:
        for mission_id in mission_ids:
            repo.log_email(mission_id, to_str, cc_str, subject, body, status="failed", error_message=str(e))
        raise EmailError(str(e)) from e

    for mission_id in mission_ids:
        repo.log_email(mission_id, to_str, cc_str, subject, body, status="sent",
                       message_id=message_id, provider_message_id=provider_id)
