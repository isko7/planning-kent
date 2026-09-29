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
- **Prix masquable** : sur la fiche d'un OM, un bouton réservé aux
  administrateurs, à côté du prix, bascule entre « Affiché » et
  « Masqué ». Masqué, le prix disparaît du **PDF** (pour tout le monde —
  c'est le document qui part au chauffeur et à l'agence) et des écrans
  pour les **non-administrateurs**. Un administrateur continue de le voir
  dans l'application, barré, pour repérer d'un coup d'oeil qu'il ne part
  pas. Le gabarit du billet collectif dispose de la variable
  `show_price` ; un gabarit personnalisé qui l'ignore reçoit de toute
  façon un `price` vide. Le même bouton figure dans le formulaire de
  création / modification, à côté du champ Prix, et le réglage s'y
  enregistre avec la mission. La duplication d'un OM le conserve.
- **Créer le retour** : les arrêts sont repris dans l'ordre inverse et
  replanifiés à partir de la date et de l'heure données, et les heures des
  trajets sont **recalculées depuis ces arrêts** — comme le ferait le
  bouton « Générer les trajets depuis les arrêts ». Un décalage uniforme
  ne marcherait pas : les trajets du retour sont ceux de l'aller pris à
  l'envers, leurs heures d'origine n'ont plus de sens dans ce sens-là.
- **Envoyer l'itinéraire** : sur la fiche d'un OM déjà envoyé à son
  chauffeur, un bouton envoie le lien Google Maps de la mission **en
  réponse à cet email-là** (même objet précédé de « Re: »), pour que le
  chauffeur retrouve les deux au même endroit dans sa boîte. Le bouton ne
  dépend pas de la case « Envoyer l'itinéraire » de la fiche du chauffeur,
  qui ne règle que la ligne ajoutée d'office à l'ordre de mission.
  L'email porte le trajet complet dans un bouton « Itinéraire complet —
  Google Maps », puis les arrêts **numérotés, chacun étant une pastille
  cliquable qui ouvre Waze** : Waze ne sait pas enchaîner plusieurs
  destinations dans une URL, un lien unique ne couvrirait qu'une partie du
  trajet. **L'email d'ordre de mission porte le même bloc** quand la case
  « Envoyer l'itinéraire » est cochée sur la fiche du chauffeur. La mise
  en forme HTML des emails repose sur des blocs à marges fixes
  (`email_service._body_to_html`) et non sur des `<br>` empilés, qui
  donnaient des écarts irréguliers d'un client de messagerie à l'autre.
- **Trois onglets sur la liste des OM** : *À venir*, *Missions passées* et
  *Missions archivées*. Les envois par email ne sont proposés que sur les
  missions à venir ; l'onglet des missions passées offre à la place un
  bouton **Archiver** (contour orange) qui range la sélection dans l'onglet
  *Missions archivées*, d'où **Désarchiver** la ramène. Rien n'est supprimé
  (colonne `missions.archived_at`), et le planning continue d'afficher les
  missions archivées.
- **Menus déroulants** : *Ressources* réunit **Personnel** et
  **Véhicules**, *Tiers* réunit **Clients** et **Partenaires**. Ce ne sont
  que des regroupements de la barre de navigation — les quatre écrans
  restent distincts. La pastille d'alerte des contrôles techniques
  remonte sur « Ressources » tant que le menu est replié.
- **Partenaires** : les agences d'intérim (nom, téléphone, email, modèle
  d'email). Une fiche *Personnel* se rattache à l'une d'elles par son champ
  « Intérim ». C'est cette agence qui fournit le destinataire et le texte
  pré-remplis du bouton « Envoyer à l'intérim » de la liste des ordres de
  mission — il n'y a plus d'adresse figée dans le `.env`. Le modèle accepte
  trois marqueurs : `{noms}` (les chauffeurs concernés), `{missions}` (le
  récapitulatif dates / horaires) et `{societe}`. Objet et message restent
  modifiables avant chaque envoi. Quand la sélection couvre **plusieurs
  agences**, les missions sont regroupées par agence : **un email par
  agence**, avec seulement ses propres ordres de mission en pièces jointes.
  Une mission dont le chauffeur n'a pas d'intérim n'a pas de destinataire :
  le bouton disparaît de sa fiche, et se désactive dans la liste dès qu'une
  telle mission est cochée (en nommant lesquelles).
  Chaque agence part indépendamment — l'échec de l'une n'empêche pas les
  autres, et seules les missions réellement envoyées sont marquées.
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
- **Plusieurs adresses par fiche** : le champ *Email* d'une fiche
  *Personnel* accepte autant d'adresses que voulu, **séparées par un
  point-virgule** (boîte perso + boîte de l'agence, par exemple). Toutes
  reçoivent l'ordre de mission — envoi à l'unité comme envoi groupé — et
  toutes sont pré-remplies dans le champ *Destinataires* de l'écran
  d'envoi, où elles restent modifiables. La virgule et le retour à la ligne
  sont acceptés à la saisie et ramenés au point-virgule à l'enregistrement
  (`utils.split_emails`).
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
  n'est actif. Dans la grille, la largeur d'un jour s'ajuste en tirant le
  bord de son en-tête (au doigt comme à la souris) ; les colonnes reprennent
  des largeurs égales au rechargement, au changement de semaine, ou d'un
  double-clic sur la poignée. Flux **iCalendar** partageable (tout le monde, ou un
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
  la main fait référence. Saisie libre ou choix dans le sélecteur, comme
  partout ailleurs (voir **Saisie des heures**), affichage à la mode de
  l'application : « 6 », « 630 », « 6:30 » ou « 6h30 » donnent tous « 06h30 ».
  **Navettes** (section du bas, indépendante de l'itinéraire ci-dessus) : des
  points de ramassage avec leur nombre de voyageurs, une destination commune
  et son heure d'arrivée, des navettes de N places (8 par défaut). Priorités,
  dans cet ordre : remplir les navettes au maximum, donc en avoir le moins
  possible, puis raccourcir les trajets. Chacune reçoit son ordre de passage
  le plus court jusqu'à la destination, les heures de ramassage déduites à
  rebours de l'heure d'arrivée, le tracé sur une carte (une couleur par navette) et un lien de
  navigation. La répartition se fait à vol d'oiseau (aucune clé requise) ;
  les distances, durées et heures affichées, elles, viennent de la route.
  Un sélecteur **« Itinéraires »** choisit qui les calcule, pour les deux
  sections de l'écran : **Google Maps** (dans le navigateur) ou **TomTom**
  (côté serveur, trafic du moment — sa clé ne sort pas du serveur). Le choix
  est gardé par l'appareil, et grisé s'il manque la clé correspondante.
- **Saisie des heures** : tous les champs d'heure de l'application (arrêts et
  trajets d'un OM, étapes et navettes du Plan de Ramassage, fenêtre « Créer le
  retour ») marchent de la même façon. On tape l'heure comme on veut — « 6 »,
  « 630 », « 6:30 », « 6h30 » — et elle est remise au propre en quittant le
  champ ; un clic dedans ouvre en plus un **sélecteur en deux colonnes,
  heures et minutes** (de 5 en 5), positionné sur l'heure déjà saisie : un
  clic dans une colonne ne change que cette moitié-là, et se voit aussitôt
  dans le champ. Les flèches ↑ / ↓ avancent ou reculent de 5 minutes. Ce sont
  des champs texte et non des `input[type=time]`, qui s'affichent en AM/PM dès
  que l'appareil n'est pas en français.
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
               password_hash ; email : une ou plusieurs adresses séparées
               par « ; »). Anciennement « drivers » : le renommage est
               joué automatiquement au démarrage (db.TABLE_RENAMES)
vehicles       véhicules (+ suivi : contrôle technique, entretien, km)
clients        donneurs d'ordre, réutilisables
templates      gabarits OM/BC (type, html, version, actif)
missions       un ordre de mission (chauffeur, date, motif, client, statut,
               archived_at : rangée dans l'onglet « Missions archivées »)
mission_legs   lignes du tableau « Mission » de l'OM
mission_stops  lignes du tableau du BC
attachments    fichiers joints (contenu binaire + position d'insertion)
email_log      historique des envois (+ message_id : l'en-tête posé à
               l'envoi, pour répondre dans le fil — voir email_service.py)
mission_links  missions liées (chaque lien écrit dans les deux sens)
partners       agences d'intérim : coordonnées + modèle d'email de l'envoi
               groupé. Rattachées au personnel par crew.partner_id
```

Détail complet dans `app/db.py` (`SCHEMA_STATEMENTS`). Les bases déjà en
place sont mises à niveau au démarrage : `MIGRATIONS` ajoute les colonnes
manquantes, `COLUMN_WIDENINGS` élargit celles devenues trop courtes (un
`MODIFY COLUMN` ne se signalant pas comme « déjà appliqué », la longueur
actuelle est lue dans `information_schema` avant de jouer la DDL).

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
| `PAUSE_LABELS` | libellés proposés pour un trajet « (pause) » du formulaire d'OM, séparés par des `\|` (et non des virgules : un libellé peut en contenir une). Vide = saisie libre. Défaut : `Pause + Attente Clients\|Pause 15 min\|Pause 30 min` |
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
   **Application permissions** → cochez **`Mail.Send`** *et*
   **`Mail.ReadWrite`** → Add → puis **Grant admin consent** (nécessite un
   rôle Admin global).
   *(Ne pas confondre avec l'API « Office 365 Exchange Online » /
   `SMTP.SendAsApp` — ce n'est pas celle-là qu'il faut.)*

   `Mail.ReadWrite` sert au rangement des envois : l'application prépare
   un brouillon, l'envoie, puis déplace la copie de « Éléments envoyés »
   vers le dossier **Planning KENT** (`email_service.SENT_FOLDER_NAME`),
   créé au premier envoi. Deux bénéfices : les envois automatiques ne se
   mélangent pas aux envois faits à la main depuis Outlook — une
   stratégie de rétention peut vider ce seul dossier — et le bouton
   « Envoyer l'itinéraire » peut répondre au message d'origine
   (`createReply`), ce qui regroupe les deux emails dans la boîte du
   chauffeur.

   Sans cette permission, l'application se rabat automatiquement sur
   l'ancien envoi direct (`sendMail`, sans copie ni fil) : les emails
   partent quand même.
5. *(Recommandé)* Restreindre l'app à une seule boîte mail plutôt que
   tout le tenant, via une Application Access Policy (Exchange Online
   PowerShell — s'applique aussi à Graph `sendMail`) :
   ```powershell
   New-DistributionGroup -Name "KentGraphSenders" -Members expediteur@votredomaine.com
   New-ApplicationAccessPolicy -AppId <client-id> -PolicyScopeGroupId KentGraphSenders@votredomaine.com -AccessRight RestrictAccess -Description "Limite l'appli à l'expéditeur"
   ```
   Cette restriction prend d'autant plus d'importance avec
   `Mail.ReadWrite`, qui donne sinon accès en lecture/écriture à **toutes**
   les boîtes du tenant.
7. *(Facultatif)* Purger automatiquement le dossier **Planning KENT** :
   dans Outlook, clic droit sur le dossier → **Affecter une stratégie**,
   et choisir une balise de rétention « Supprimer après N jours ». Les
   balises se créent dans le portail Purview (*Data Lifecycle Management →
   Exchange (hérité) → Balises de rétention MRM*). La balise ne porte que
   sur ce dossier : « Éléments envoyés » n'est pas touché.
8. Variables (Vercel ou `.env`) :
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

Les envois ne laissent **pas** de copie dans « Éléments envoyés » de la boîte
expéditrice (`saveToSentItems: false`) : ils sont automatiques et nombreux, et
l'historique est déjà tenu par l'application (onglet Historique de la mission).

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
