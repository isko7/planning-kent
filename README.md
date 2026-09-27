# Planning KENT — Transports KENT

Application interne pour créer, stocker, modifier et envoyer les **Ordres
de Mission (OM)** et **Billets Collectifs (BC)** de Transports KENT, à
partir des chauffeurs, véhicules et clients enregistrés.

Déployée sur **Vercel** (fonctions serverless), base **MySQL**.

## Ce que ça fait

- **Connexion obligatoire, deux niveaux de droits** : toute l'application
  est protégée par un identifiant / mot de passe. Les seuls comptes sont des
  **fiches chauffeur** : dans *Personnel*, cocher « Autoriser ce chauffeur à
  se connecter » et lui donner un identifiant + un mot de passe. Un chauffeur
  sans accès n'a aucun compte — il reçoit seulement ses ordres de mission par
  email.
  - **chauffeur** : consulte, en **lecture seule**, son planning et ses
    propres ordres de mission (détail + PDF). Les écrans Personnel /
    Véhicules / Clients / Templates lui sont fermés et masqués du menu ;
    les OM des autres chauffeurs répondent 404.
  - **administrateur** (case « Administrateur » de la fiche) : accès
    complet, y compris la gestion des accès. Il peut **réinitialiser le mot
    de passe** d'un chauffeur d'un bouton : le mot de passe redevient
    l'identifiant, et un nouveau est exigé à la connexion suivante.
- **Chauffeurs / Véhicules / Clients** : liste + fiche + CRUD complet. Ils
  alimentent les menus déroulants partout où ils sont utilisés. L'adresse
  d'un client se cherche comme les arrêts d'un BC (Google Maps ou Base
  Adresse Nationale, selon le réglage « Recherche d'adresse ») : le choix
  remplit aussi le code postal et la ville — sur la fiche client comme dans
  la création rapide du formulaire d'ordre de mission.
- **Ordres de mission** : un formulaire unique avec les **arrêts du Billet
  Collectif** (prises en charge / déposes, adresses, horaires, voyageurs) et
  les **trajets de l'Ordre de Mission** (début / fin / véhicule / trajet),
  avec un bouton « Générer les trajets depuis les arrêts ».
- **Missions liées** : sur la fiche d'un OM et dans son formulaire, une
  section liste les missions liées (le lien vaut dans les deux sens). Le
  bouton « Lier des missions » ouvre une sélection multiple, les missions au
  même code en tête de nom d'abord (`26MONTENEG A` → `26MONTENEG R`). Le nom
  d'une mission liée mène à sa fiche ; l'œil à côté l'ouvre dans un panneau
  flottant, à côté de la page — les deux restent visibles. Le panneau montre
  la fiche **entière** (trajets, arrêts, pièces jointes, missions liées,
  envois, récap de facturation, boutons d'action) : ce qui s'y modifie y
  revient, ce qui en sort (modifier, dupliquer, envoyer, supprimer) s'ouvre
  dans un nouvel onglet. « Créer le retour » lie d'office l'aller et son
  retour : une fenêtre demande la date et l'heure du **premier arrêt** du
  retour, et les arrêts suivants sont replanifiés aux mêmes écarts qu'à
  l'aller (les trajets, eux, restent à revoir — « Générer les trajets depuis
  les arrêts » les refait). Le formulaire propose la même chose d'un coup
  avec « Enregistrer et créer le retour ».
- **Génération PDF** : un seul PDF = **OM + pièces jointes + BC**, fusionnés.
  Pièces jointes (PDF/PNG/JPG) insérables avant l'OM, entre l'OM et le BC
  (page 2, par défaut), ou après le BC — depuis la fiche, ou dans le
  formulaire (jointes à l'enregistrement). « Agrandir » (fichier choisi) et
  « Aperçu PDF » (OM généré) les affichent en grand dans un panneau
  flottant, pour comparer avec la page.
- **Lecture des arrêts** (formulaire OM) : sur la page choisie d'un plan de
  ramassage / dépose joint, « Lire les arrêts de la page » remplit les
  arrêts du BC (heure, ville, adresse, voyageurs, sens aller / retour). Le
  texte du PDF est lu directement s'il en contient ; sinon (scan, PDF
  « imprimé ») la page passe par un OCR dans le navigateur (Tesseract.js,
  rien n'est envoyé ailleurs). Chaque adresse est ensuite vérifiée auprès
  de Google ou de la Base Adresse Nationale (réglage « Recherche
  d'adresse ») et remplacée par l'adresse officielle si elle correspond.
- **Envoi par email** : au chauffeur + destinataires en copie, objet et corps
  personnalisables, PDF en pièce jointe, historique des envois.
- **Templates OM / BC modifiables** : le HTML/Jinja2 qui génère les PDF est
  stocké en base, éditable depuis *Templates* (aperçu sur données de démo,
  duplication pour tester une variante).
- **Planning** : deux vues des missions de la semaine — la grille horaire
  et l'agenda (liste par jour) — avec un bouton pour passer de l'une à
  l'autre, sur ordinateur comme sur téléphone ; par défaut la grille sur
  ordinateur, l'agenda sur téléphone. Coloré par chauffeur (couleur
  personnalisable sur la fiche chauffeur), clic sur une mission → son ordre
  de mission, et un œil sur chaque élément en ouvre le PDF dans un panneau
  flottant, sans quitter le planning. Les filtres sont repliés tant qu'aucun
  n'est actif. Flux **iCalendar** partageable (tout le monde, ou un
  chauffeur seul) : « Partager le calendrier » donne un bouton par agenda —
  Google Agenda (le chemin à suivre aussi pour Samsung Calendar et les autres
  agendas Android, via le compte Google du téléphone), Calendrier iPhone, et
  le fichier .ics à ouvrir tel quel. Voir `CALENDAR_FEED_TOKEN`.
- **Plan de Ramassage** (écran indépendant, réservé aux administrateurs) :
  une liste d'adresses saisies dans n'importe quel ordre — avec la même
  recherche d'adresse que les arrêts du BC — qu'un bouton remet dans l'ordre
  du trajet le plus court, carte à l'appui. Rien n'est imposé : le calcul
  choisit aussi par où commencer et par où finir, le seul critère étant la
  distance totale. Un second bouton, « Calculer l'itinéraire », trace au
  contraire l'ordre affiché sans y toucher. L'ordre reste modifiable à la
  main, par glisser-déposer (ou les flèches ▲▼) : la carte et les distances
  suivent sans relancer le calcul. Un bouton ouvre l'itinéraire dans Google
  Maps pour la navigation, un autre copie la liste. La liste est conservée
  par le navigateur, le temps de revenir dessus.
  **Heures de passage** : on en saisit une seule — départ, arrivée, ou
  n'importe quel arrêt — et chaque calcul remplit les autres à partir des
  durées de trajet, en avant comme en arrière ; la dernière heure saisie à
  la main fait référence. Saisie libre, affichage sur 24 heures à la mode de
  l'application : « 6 », « 630 », « 6:30 » ou « 6h30 » donnent tous « 06h30 ».
- **Mode sombre** : bouton lune / soleil dans la barre du haut. Par défaut,
  l'application suit le réglage clair / sombre de l'appareil ; le choix fait
  avec le bouton est mémorisé par le navigateur (donc par appareil).

## Stack technique

L'application est écrite en **Python / Flask**. Principe : **gabarits HTML
→ PDF**, avec la base de données comme source unique des chauffeurs,
véhicules, clients et missions.

| Besoin | Choix | Détail |
|---|---|---|
| Hébergement | **Vercel** | `api/index.py` sert l'app Flask (WSGI) ; `vercel.json` réécrit toutes les routes vers cette fonction. |
| Serveur web | **Flask** | Pas de build front : pages en Jinja2 + JS vanilla. |
| Base de données | **MySQL** (`PyMySQL`, pur Python) | Connexion via `DATABASE_URL`. Schéma dans `app/db.py`, accès aux données isolé dans `app/repo.py`. |
| Templates OM/BC | **HTML + Jinja2**, stockés en base (table `templates`) | Éditables depuis l'interface. |
| HTML → PDF | **Chrome headless** (`api/render_pdf.js`, `@sparticuz/chromium`) | 2ᵉ fonction serverless Node, appelée en HTTP interne par Flask. Les règles CSS `@page` des templates sont respectées (`preferCSSPageSize`). En local : `wkhtmltopdf`. |
| Fusion OM + PJ + BC | **pypdf** | Les pièces jointes sont stockées en base (`LONGBLOB`). |
| Email | **smtplib** (stdlib) + option **MSAL/OAuth2** pour Microsoft 365 | `SMTP_AUTH_METHOD=basic` ou `oauth2_o365`. |
| Authentification | **JWT** (`PyJWT`, HS256) dans un cookie HttpOnly + hachage `pbkdf2:sha256` (Werkzeug) | `app/auth.py`. Les comptes sont les fiches chauffeur autorisées ; droits et accès relus en base à chaque requête (révocation immédiate). |
| Droits | liste blanche `app/auth.py:DRIVER_ENDPOINTS` | Un chauffeur non administrateur n'atteint que ces points d'entrée, tous en lecture. Tout écran ajouté plus tard est donc réservé aux administrateurs par défaut. |

## Schéma de données

```
crew           personnel : chauffeurs et autres (+ accès appli :
               can_login, is_admin, must_change_password, username,
               password_hash). Anciennement « drivers » : le renommage est
               joué automatiquement au démarrage (db.TABLE_RENAMES)
vehicles       véhicules (+ suivi : contrôle technique, entretien, km)
clients        donneurs d'ordre, réutilisables
templates      gabarits OM/BC (type, html, version, actif)
missions       un ordre de mission (chauffeur, date, motif, client, statut...)
mission_legs   lignes du tableau « Mission » de l'OM
mission_stops  lignes du tableau du BC
attachments    fichiers joints (contenu binaire + position d'insertion)
email_log      historique des envois
mission_links  missions liées (chaque lien écrit dans les deux sens)
```

Détail complet dans `app/db.py` (`SCHEMA_STATEMENTS`).

---

## Déploiement sur Vercel

### 1. Base de données MySQL

Provisionner un MySQL accessible depuis Internet (offres compatibles :
PlanetScale, Aiven, Railway, un MySQL managé OVH/Scaleway, ou la marketplace
Vercel). Récupérer une URL de connexion de la forme :

```
mysql://utilisateur:motdepasse@hote:3306/nom_de_base
```

### 2. Variables d'environnement Vercel

Dans *Project → Settings → Environment Variables* :

| Variable | Valeur |
|---|---|
| `DATABASE_URL` | l'URL MySQL ci-dessus (ou les variables `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` / `MYSQL_SSL` séparées — à préférer si le mot de passe contient `@` ou des espaces) |
| `PDF_ENGINE` | `http` |
| `PDF_RENDER_SECRET` | une chaîne aléatoire (partagée entre les 2 fonctions, définie une seule fois ici) |
| `SECRET_KEY` | une chaîne aléatoire |
| `SEED_SECRET` | une chaîne aléatoire (pour la route d'initialisation, voir §3) |
| `CALENDAR_FEED_TOKEN` | une chaîne aléatoire (active le flux iCalendar partageable de l'écran Planning) |
| `JWT_SECRET` | une chaîne aléatoire (signature des sessions ; à défaut `SECRET_KEY` est réutilisée). La changer déconnecte tout le monde |
| `JWT_TTL_HOURS` | durée d'une session en heures (défaut `168`, soit 7 jours) |
| `BOOTSTRAP_PASSWORD` | mot de passe du compte créé automatiquement au premier démarrage (voir §3) |
| `SMTP_AUTH_METHOD` | `basic` (ou `oauth2_o365`) |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | identifiants SMTP |
| `SMTP_FROM_NAME` / `SMTP_FROM_EMAIL` | expéditeur affiché |
| `COMPANY_*`, `OM_LEGAL_REF`, `BC_LEGAL_REF` | si différent des valeurs par défaut (voir `.env.example`) |

### Microsoft 365 / Exchange Online (`SMTP_AUTH_METHOD=oauth2_o365`)

L'envoi passe par l'**API Microsoft Graph** (`POST /users/{expéditeur}/
sendMail`), pas par le protocole SMTP AUTH classique : Microsoft traite
SMTP AUTH comme une « authentification legacy » et le bloque dès que les
Security Defaults / Conditional Access sont actifs sur le tenant (quasi
systématique sur un tenant récent) — désactiver cette protection tenant-
wide juste pour l'email est un compromis de sécurité qu'on préfère
éviter. Graph est une API REST « moderne », non concernée par ce blocage,
donc rien à changer à la posture de sécurité du tenant.

1. **portal.azure.com → Azure Active Directory → App registrations → New
   registration** (ou réutiliser une app existante).
2. Notez l'**Application (client) ID** et le **Directory (tenant) ID**
   (page *Overview*).
3. **Certificates & secrets → New client secret** → copiez la **Value**
   tout de suite (affichée une seule fois).
4. **API permissions → Add a permission → Microsoft Graph** →
   **Application permissions** → cherchez et cochez **`Mail.Send`** →
   Add → puis **Grant admin consent** (nécessite un rôle Admin global).
   *(Ne pas confondre avec l'API « Office 365 Exchange Online » /
   `SMTP.SendAsApp` — ce n'est pas celle-là qu'il faut.)*
5. *(Recommandé)* Restreindre l'app à une seule boîte mail plutôt que
   tout le tenant, via une Application Access Policy (Exchange Online
   PowerShell — s'applique aussi à Graph `sendMail`) :
   ```powershell
   New-DistributionGroup -Name "KentGraphSenders" -Members expediteur@votredomaine.com
   New-ApplicationAccessPolicy -AppId <client-id> -PolicyScopeGroupId KentGraphSenders@votredomaine.com -AccessRight RestrictAccess -Description "Limite l'appli à l'expéditeur"
   ```
6. Variables (Vercel ou `.env`) :
   ```
   SMTP_AUTH_METHOD=oauth2_o365
   O365_TENANT_ID=<Directory (tenant) ID>
   O365_CLIENT_ID=<Application (client) ID>
   O365_CLIENT_SECRET=<Value du secret, pas l'ID>
   O365_SENDER_EMAIL=expediteur@votredomaine.com
   ```
   (`SMTP_HOST`/`SMTP_PORT`/`SMTP_FROM_EMAIL` ne sont pas utilisés dans ce
   mode.) `msal` est déjà dans `requirements.txt`.

Limite : pièces jointes inline via `sendMail` plafonnées à ~4 Mo au total
par message (au-delà, erreur claire plutôt qu'un envoi tronqué) — largement
suffisant pour un ordre de mission, à surveiller si beaucoup de pièces jointes lourdes.

> **Protection de déploiement** : si l'authentification Vercel (Deployment
> Protection) est active, l'appel interne Flask → `/api/render_pdf` est
> bloqué. Soit la désactiver, soit garder `PDF_RENDER_SECRET` **et** ajouter
> l'en-tête de contournement via `VERCEL_AUTOMATION_BYPASS_SECRET`.

### 3. Amorcer la base

Le schéma se crée tout seul au premier démarrage (`CREATE TABLE IF NOT
EXISTS`), mais il faut installer les **templates OM/BC par défaut** une fois.

**Option simple (Vercel) — aucun accès MySQL requis depuis votre poste :**
après le 1ᵉʳ déploiement, ouvrir une fois dans le navigateur :

```
https://<projet>.vercel.app/admin/init?key=<SEED_SECRET>          # schéma + templates
https://<projet>.vercel.app/admin/init?key=<SEED_SECRET>&demo=1   # + mission de démo
```

La réponse est un JSON listant les actions. La route est idempotente et
répond 404 si `SEED_SECRET` n'est pas défini.

**Option ligne de commande** (si votre poste peut joindre la base) :

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
$env:DATABASE_URL="mysql://...:3306/db"; python seed.py --demo
```

### 4. Première connexion

L'application est fermée : il faut un compte pour y entrer. Au démarrage,
si **aucun** chauffeur ne peut se connecter, l'application en crée un
automatiquement (`ensure_login_access()` dans `app/seeding.py`) :

| | Valeur par défaut | Variable |
|---|---|---|
| Chauffeur | Ismail KILINC (fiche créée si absente) | `BOOTSTRAP_LAST_NAME` / `BOOTSTRAP_FIRST_NAME` |
| Identifiant | `ikilinc` | `BOOTSTRAP_USERNAME` |
| Mot de passe | `kent2026` | `BOOTSTRAP_PASSWORD` |

⚠️ **Changez ce mot de passe dès la première connexion** (*Chauffeurs* → sa
fiche → *Accès à l'application*), ou définissez `BOOTSTRAP_PASSWORD` avant
le premier démarrage.

Ensuite, pour ouvrir l'accès à un autre chauffeur : *Personnel* → sa fiche
→ **Accès à l'application** → cocher « Autoriser ce chauffeur à se
connecter », saisir un identifiant et un mot de passe (8 caractères
minimum). Cocher en plus **Administrateur** lui donne les pleins droits ;
sans cette case, il est en lecture seule sur son seul périmètre.

Décocher « Autoriser… » retire l'accès et efface identifiant, mot de passe
et droits d'administration ; ses sessions en cours sont coupées
immédiatement, comme lors d'un changement de mot de passe.

**Mot de passe oublié** : sur la fiche du chauffeur, bouton *Réinitialiser
le mot de passe*. Le mot de passe redevient son identifiant (à lui
communiquer), et l'application le bloque sur l'écran « choisissez un nouveau
mot de passe » tant qu'il n'en a pas saisi un autre. Chacun peut aussi
changer le sien à tout moment en cliquant son nom dans la barre du haut.

Quatre garde-fous évitent de se retrouver dehors : on ne peut retirer ni son
propre accès, ni celui du dernier compte capable de se connecter, ni ses
propres droits d'administration, ni ceux du dernier administrateur.

Les routes qui portent déjà leur propre secret restent hors connexion :
`/admin/*` (`SEED_SECRET`) et le flux `/planning/calendrier.ics`
(`CALENDAR_FEED_TOKEN`, appelé par l'application Calendrier du téléphone).

### 5. Déployer

`git push` sur la branche suivie par Vercel, ou `vercel --prod`.

---

## Développement local

**Prérequis :** Python 3.10+, un MySQL local (ou distant), et le binaire
**wkhtmltopdf** (rendu PDF local) :

- Windows : `winget install wkhtmltopdf.wkhtmltox`
- macOS : `brew install --cask wkhtmltopdf` · Debian/Ubuntu : `apt install wkhtmltopdf`

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # éditer DATABASE_URL, SMTP_*, etc.
python seed.py --demo
python run.py               # http://localhost:8000
```

En local, `PDF_ENGINE=wkhtmltopdf` (défaut) utilise le binaire système. Pour
tester le rendu Chrome serverless en local, utiliser `vercel dev` avec
`PDF_ENGINE=http`.

## Arborescence

```
api/
  index.py             point d'entrée Vercel (WSGI Flask)
  render_pdf.js        fonction Node : HTML -> PDF (Chrome headless)
app/
  config.py            configuration (.env / variables Vercel)
  db.py               connexion MySQL + schéma
  repo.py             accès aux données (SQL brut)
  auth.py             authentification : JWT, mots de passe, garde-fou global
  pdf_service.py       rendu Jinja2 -> HTML -> PDF -> fusion pypdf
  email_service.py     envoi SMTP (basic ou OAuth2 O365)
  ical_service.py       génération du flux iCalendar (planning)
  routing.py           géocodage (BAN / TomTom), estimation de durée,
                       ordre de passage le plus court (Plan de Ramassage)
  utils.py             formats de date/heure en français
  routes/              blueprints Flask
  templates/           pages Jinja2 (interface web)
  templates_data/      sources HTML/Jinja2 par défaut de l'OM et du BC
  static/              CSS, JS, logo
seed.py                 amorçage (schéma + templates + compte d'accès + option --demo)
run.py                  serveur de dev Flask
vercel.json             config des fonctions + réécritures
requirements.txt        dépendances Python
package.json            dépendances Node (fonction de rendu PDF)
.env.example
```
