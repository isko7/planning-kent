"""
Connexion MySQL + schéma de la base.

On utilise PyMySQL (pilote pur Python : aucune compilation, donc
déployable tel quel sur Vercel). La connexion est réutilisée entre les
invocations « chaudes » de la fonction serverless (un `ping(reconnect=True)`
la ré-ouvre si elle est tombée).

`repo.py` est écrit avec des placeholders SQLite (`?`) ; le petit wrapper
`_Cursor` ci-dessous les traduit en `%s` pour PyMySQL, ce qui garde la
couche d'accès aux données lisible et portable. Aucune requête de `repo.py`
ne contient de `?` ou de `%` littéral, la traduction est donc sûre.
"""
from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor

from app.config import DB_CONFIG, env
from app.utils import DEFAULT_PARTNER_EMAIL_BODY, DEFAULT_PARTNER_EMAIL_SUBJECT

# Index déclarés en ligne dans les CREATE TABLE (MySQL ne connaît pas
# « CREATE INDEX IF NOT EXISTS »). Pas de contraintes FOREIGN KEY : la
# cohérence référentielle est gérée côté application (repo.py), et les DDL
# de FK sont lents / capricieux sur TiDB serverless. La suppression en
# cascade des lignes filles est faite explicitement dans repo.delete_*.
SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS crew (
        id INT AUTO_INCREMENT PRIMARY KEY,
        last_name VARCHAR(120) NOT NULL,
        first_name VARCHAR(120) NOT NULL,
        email VARCHAR(255) NOT NULL,
        phone VARCHAR(40),
        license_number VARCHAR(60),
        active TINYINT(1) NOT NULL DEFAULT 1,
        color VARCHAR(9),
        send_itinerary TINYINT(1) NOT NULL DEFAULT 0,
        personal_notes TEXT,
        can_login TINYINT(1) NOT NULL DEFAULT 0,
        is_admin TINYINT(1) NOT NULL DEFAULT 0,
        must_change_password TINYINT(1) NOT NULL DEFAULT 0,
        username VARCHAR(80) NULL,
        password_hash VARCHAR(255) NULL,
        notes TEXT,
        remarks TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        -- Nom d'index hérité de l'ancienne table « drivers » : RENAME TABLE
        -- le conserve tel quel, on garde donc le même nom ici pour que les
        -- deux chemins (base neuve / base renommée) aient le même schéma.
        UNIQUE KEY uq_drivers_username (username)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS vehicles (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(120),
        plate VARCHAR(32) NOT NULL UNIQUE,
        seats INT,
        active TINYINT(1) NOT NULL DEFAULT 1,
        notes TEXT,
        remarks TEXT,
        -- Dates au format 'YYYY-MM-DD' (VARCHAR, comme missions.mission_date) :
        -- elles se comparent et se trient telles quelles.
        technical_control_date VARCHAR(10),
        maintenance_date VARCHAR(10),
        last_maintenance_km INT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS clients (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        address VARCHAR(255),
        postal_code VARCHAR(20),
        city VARCHAR(120),
        phone VARCHAR(40),
        email VARCHAR(255),
        notes TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS templates (
        id INT AUTO_INCREMENT PRIMARY KEY,
        type VARCHAR(4) NOT NULL,
        name VARCHAR(255) NOT NULL,
        is_active TINYINT(1) NOT NULL DEFAULT 0,
        definition_html MEDIUMTEXT NOT NULL,
        version INT NOT NULL DEFAULT 1,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_templates_type (type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS missions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        reference VARCHAR(64) UNIQUE,
        mission_name VARCHAR(255),
        shuttle_label VARCHAR(120),
        driver_id INT NOT NULL,
        mission_date VARCHAR(10) NOT NULL,
        motif VARCHAR(255) NOT NULL DEFAULT 'Transport Occasionnel',
        remarks TEXT,
        client_id INT NULL,
        bc_client_name VARCHAR(255),
        bc_client_address VARCHAR(255),
        bc_client_postal_code VARCHAR(20),
        bc_client_city VARCHAR(120),
        bc_client_phone VARCHAR(255),
        emission_date VARCHAR(10),
        price VARCHAR(60),
        -- Bascule « Masquer le prix » de la fiche mission (reservee aux
        -- administrateurs) : retire le prix du PDF, et des ecrans pour les
        -- non-administrateurs. 0 = prix visible.
        price_hidden TINYINT(1) NOT NULL DEFAULT 0,
        status VARCHAR(30) NOT NULL DEFAULT 'brouillon',
        om_template_id INT NULL,
        bc_template_id INT NULL,
        amplitude_minutes INT NULL,
        driving_minutes INT NULL,
        pause_minutes INT NULL,
        notes TEXT,
        -- Date d'envoi a l'agence d'interim. Nom historique (l'envoi
        -- groupe s'appelait « Envoyer a Randstad ») : renommer la colonne
        -- couterait une migration risquee pour un simple libelle.
        sent_randstad_at DATETIME NULL,
        sent_driver_at DATETIME NULL,
        -- Rempli par le bouton « Archiver » de l'onglet Missions passees :
        -- la mission sort de la liste courante et bascule dans l'onglet
        -- Missions archivees. NULL = mission active.
        archived_at DATETIME NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_missions_driver (driver_id),
        KEY idx_missions_date (mission_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS mission_legs (
        id INT AUTO_INCREMENT PRIMARY KEY,
        mission_id INT NOT NULL,
        position INT NOT NULL,
        start_time VARCHAR(10) NOT NULL,
        end_time VARCHAR(10) NOT NULL,
        vehicle_id INT NULL,
        label VARCHAR(255) NOT NULL,
        is_checkpoint TINYINT(1) NOT NULL DEFAULT 0,
        is_relay TINYINT(1) NOT NULL DEFAULT 0,
        relay_driver_id INT NULL,
        -- Distance du trajet en mètres, telle qu'estimée par le service
        -- d'itinéraire au moment de l'enregistrement (formulaire OM).
        -- NULL = non estimée (libellé libre, point de contrôle...).
        distance_m INT NULL,
        KEY idx_legs_mission (mission_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS mission_stops (
        id INT AUTO_INCREMENT PRIMARY KEY,
        mission_id INT NOT NULL,
        position INT NOT NULL,
        stop_type VARCHAR(20) NOT NULL,
        stop_date VARCHAR(10) NOT NULL,
        stop_time VARCHAR(10) NOT NULL,
        address VARCHAR(255) NOT NULL,
        city VARCHAR(120),
        passenger_count INT NOT NULL DEFAULT 1,
        passenger_name VARCHAR(160),
        passenger_phone VARCHAR(40),
        booking_ref VARCHAR(80),
        KEY idx_stops_mission (mission_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS attachments (
        id INT AUTO_INCREMENT PRIMARY KEY,
        mission_id INT NOT NULL,
        filename VARCHAR(255) NOT NULL,
        content_type VARCHAR(120),
        content LONGBLOB NOT NULL,
        insert_after_page INT NOT NULL DEFAULT 1,
        position INT NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_attachments_mission (mission_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS email_log (
        id INT AUTO_INCREMENT PRIMARY KEY,
        mission_id INT NOT NULL,
        to_addresses TEXT NOT NULL,
        cc_addresses TEXT,
        subject VARCHAR(500) NOT NULL,
        body MEDIUMTEXT,
        status VARCHAR(20) NOT NULL,
        error_message TEXT,
        -- En-tete Message-ID pose a l'envoi, quand le mode d'envoi nous
        -- laisse la main dessus (SMTP). Sert a rattacher l'email
        -- « itineraire » a l'ordre de mission deja envoye (In-Reply-To).
        message_id VARCHAR(255) NULL,
        -- Identifiant du message chez le fournisseur (Graph), une fois range
        -- dans le dossier de l'application : le seul qui permette d'y
        -- repondre par createReply. NULL en SMTP.
        provider_message_id VARCHAR(512) NULL,
        sent_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_email_mission (mission_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # Missions liées entre elles : chaque lien est écrit dans les deux sens
    # (voir repo._replace_links).
    """
    CREATE TABLE IF NOT EXISTS mission_links (
        mission_id INT NOT NULL,
        linked_mission_id INT NOT NULL,
        PRIMARY KEY (mission_id, linked_mission_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS partners (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(120) NOT NULL,
        phone VARCHAR(40),
        email VARCHAR(255),
        email_subject VARCHAR(255),
        email_body TEXT,
        -- Ordre d'affichage dans le menu deroulant « Interim » d'une fiche
        -- Personnel : impose Randstad, Artus, Adecco plutot que l'alphabetique.
        position INT NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        setting_key VARCHAR(100) PRIMARY KEY,
        setting_value VARCHAR(255),
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


# Migrations légères pour les bases déjà créées (les CREATE TABLE IF NOT
# EXISTS ci-dessus ne modifient pas une table existante). Chaque instruction
# est jouée en ignorant l'erreur « déjà appliquée » (voir
# MIGRATION_ALREADY_APPLIED).
MIGRATIONS = [
    "ALTER TABLE mission_legs ADD COLUMN is_relay TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE mission_legs ADD COLUMN relay_driver_id INT NULL",
    "ALTER TABLE missions ADD COLUMN mission_name VARCHAR(255)",
    "ALTER TABLE missions ADD COLUMN sent_randstad_at DATETIME NULL",
    "ALTER TABLE missions ADD COLUMN sent_driver_at DATETIME NULL",
    "ALTER TABLE missions ADD COLUMN shuttle_label VARCHAR(120)",
    "ALTER TABLE crew ADD COLUMN color VARCHAR(9)",
    "ALTER TABLE missions ADD COLUMN amplitude_minutes INT NULL",
    "ALTER TABLE missions ADD COLUMN driving_minutes INT NULL",
    "ALTER TABLE missions ADD COLUMN pause_minutes INT NULL",
    "ALTER TABLE crew ADD COLUMN send_itinerary TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE crew ADD COLUMN can_login TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE crew ADD COLUMN username VARCHAR(80) NULL",
    "ALTER TABLE crew ADD COLUMN password_hash VARCHAR(255) NULL",
    "ALTER TABLE crew ADD UNIQUE KEY uq_drivers_username (username)",
    "ALTER TABLE crew ADD COLUMN is_admin TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE crew ADD COLUMN must_change_password TINYINT(1) NOT NULL DEFAULT 0",
    "ALTER TABLE missions ADD COLUMN bc_client_name VARCHAR(255)",
    "ALTER TABLE missions ADD COLUMN bc_client_address VARCHAR(255)",
    "ALTER TABLE missions ADD COLUMN bc_client_postal_code VARCHAR(20)",
    "ALTER TABLE missions ADD COLUMN bc_client_city VARCHAR(120)",
    "ALTER TABLE missions ADD COLUMN bc_client_phone VARCHAR(255)",
    "ALTER TABLE missions ADD COLUMN notes TEXT",
    "ALTER TABLE crew ADD COLUMN personal_notes TEXT",
    "ALTER TABLE crew ADD COLUMN remarks TEXT",
    "ALTER TABLE vehicles ADD COLUMN remarks TEXT",
    "ALTER TABLE vehicles ADD COLUMN technical_control_date VARCHAR(10)",
    "ALTER TABLE vehicles ADD COLUMN maintenance_date VARCHAR(10)",
    "ALTER TABLE vehicles ADD COLUMN last_maintenance_km INT NULL",
    "ALTER TABLE mission_legs ADD COLUMN distance_m INT NULL",
    "ALTER TABLE crew ADD COLUMN partner_id INT NULL",
    "ALTER TABLE missions ADD COLUMN archived_at DATETIME NULL",
    "ALTER TABLE email_log ADD COLUMN message_id VARCHAR(255) NULL",
    "ALTER TABLE email_log ADD COLUMN provider_message_id VARCHAR(512) NULL",
    "ALTER TABLE missions ADD COLUMN price_hidden TINYINT(1) NOT NULL DEFAULT 0",
]

# Codes d'erreur MySQL qui signifient « migration déjà appliquée » :
# 1060 colonne déjà présente, 1061 index déjà présent.
MIGRATION_ALREADY_APPLIED = (1060, 1061)

# Renommages de tables, joués AVANT les CREATE TABLE : « CREATE TABLE IF
# NOT EXISTS crew » créerait sinon une table vide, et le renommage
# échouerait en laissant les données dans l'ancienne table.
TABLE_RENAMES = [("drivers", "crew")]


# Agences d'interim de depart, dans l'ordre voulu au menu deroulant
# « Interim » d'une fiche Personnel. Posees une seule fois, a la creation de
# la table : ensuite l'ecran Partenaires fait foi (une agence supprimee ne
# revient pas au demarrage suivant).
SEED_PARTNERS = ["Randstad", "Artus", "Adecco"]


def _seed_partners(cur):
    """Renvoie le nombre d'agences creees (0 si la table est deja peuplee)."""
    cur.execute("SELECT COUNT(*) AS n FROM partners")
    row = cur.fetchone()
    already = int(row["n"] if isinstance(row, dict) else row[0])
    if already:
        return 0
    for rank, name in enumerate(SEED_PARTNERS, start=1):
        cur.execute(
            "INSERT INTO partners (name, position, email_subject, email_body) "
            "VALUES (%s, %s, %s, %s)",
            (name, rank, DEFAULT_PARTNER_EMAIL_SUBJECT, DEFAULT_PARTNER_EMAIL_BODY),
        )
    return len(SEED_PARTNERS)


def _table_exists(cur, name):
    cur.execute("SHOW TABLES LIKE %s", (name,))
    return cur.fetchone() is not None


def _rename_legacy_tables(cur):
    """Applique TABLE_RENAMES. Ne fait rien si la nouvelle table existe
    déjà (renommage déjà joué) ou si l'ancienne n'existe pas (base neuve).
    Renvoie la liste des renommages effectués."""
    done = []
    for old_name, new_name in TABLE_RENAMES:
        if _table_exists(cur, new_name) or not _table_exists(cur, old_name):
            continue
        cur.execute(f"RENAME TABLE {old_name} TO {new_name}")
        done.append(f"{old_name} -> {new_name}")
    return done


def _connect():
    cfg = DB_CONFIG
    kwargs = dict(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        # Timeouts courts : sur Vercel une fonction a ~60 s. Mieux vaut une
        # erreur claire tout de suite qu'un FUNCTION_INVOCATION_FAILED.
        connect_timeout=int(env("MYSQL_CONNECT_TIMEOUT", "8")),
        read_timeout=20,
        write_timeout=20,
    )
    if cfg.get("ssl"):
        # ssl={} suffit pour activer TLS sans vérification stricte du CA,
        # ce que la plupart des MySQL managés acceptent.
        kwargs["ssl"] = {}
    return pymysql.connect(**kwargs)


def sanitized_config():
    """Config de connexion sans le mot de passe (pour /admin/dbcheck)."""
    cfg = DB_CONFIG
    return {
        "host": cfg["host"], "port": cfg["port"], "user": cfg["user"],
        "database": cfg["database"], "ssl": cfg["ssl"],
        "password_set": bool(cfg["password"]),
    }


def check_connection():
    """Tente une connexion + SELECT VERSION(). Renvoie un dict de diagnostic."""
    import traceback
    out = {"config": sanitized_config()}
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION() AS v")
            out["version"] = cur.fetchone()["v"]
        conn.close()
        out["ok"] = True
    except Exception as e:
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {e}"
        out["trace"] = traceback.format_exc().splitlines()[-5:]
    return out


_conn = None


def _get_conn():
    global _conn
    if _conn is not None:
        try:
            _conn.ping(reconnect=True)
            return _conn
        except Exception:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
    _conn = _connect()
    return _conn


class _Cursor:
    """Enveloppe le curseur PyMySQL pour accepter la syntaxe de repo.py :
    `db.execute(sql, params)` renvoie un objet avec .fetchone()/.fetchall()/
    .lastrowid/.rowcount, en traduisant les placeholders `?` en `%s`."""

    def __init__(self, cursor):
        self._c = cursor

    def execute(self, sql, params=None):
        self._c.execute(sql.replace("?", "%s"), tuple(params) if params else None)
        return self

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    @property
    def lastrowid(self):
        return self._c.lastrowid

    @property
    def rowcount(self):
        """Lignes reellement modifiees par le dernier UPDATE/DELETE : sert a
        compter ce qui a change (archivage d'une selection, par exemple)."""
        return self._c.rowcount


@contextmanager
def get_db():
    """`with get_db() as db:` puis db.execute(...). Commit auto en sortie,
    rollback si exception. La connexion reste ouverte (réutilisée à chaud)."""
    conn = _get_conn()
    cur = conn.cursor()
    try:
        yield _Cursor(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


_initialized = False


def init_db(force=False, report=False):
    """Crée le schéma s'il n'existe pas. Idempotent (CREATE TABLE IF NOT
    EXISTS). Exécuté une fois par process au démarrage de l'app.

    report=True -> renvoie une liste [{table, ms}] au lieu de rien, et
    n'avale pas les exceptions (utile pour /admin/init)."""
    global _initialized
    if _initialized and not force:
        return [] if report else None
    import time
    conn = _get_conn()
    timings = []
    with conn.cursor() as cur:
        for renamed in _rename_legacy_tables(cur):
            timings.append({"migration": f"RENAME TABLE {renamed}", "ms": 0})
        for stmt in SCHEMA_STATEMENTS:
            name = stmt.split("IF NOT EXISTS", 1)[-1].split("(", 1)[0].strip()
            t0 = time.monotonic()
            cur.execute(stmt)
            timings.append({"table": name, "ms": round((time.monotonic() - t0) * 1000)})
        for stmt in MIGRATIONS:
            try:
                cur.execute(stmt)
                timings.append({"migration": stmt[:60], "ms": 0})
            except Exception as e:
                if getattr(e, "args", [None])[0] not in MIGRATION_ALREADY_APPLIED:
                    raise
        created = _seed_partners(cur)
        if created:
            timings.append({"migration": f"{created} agences d'interim creees", "ms": 0})
    conn.commit()
    _initialized = True
    return timings if report else None
