import secrets
import datetime
import os
import logging
import sqlite3

logger = logging.getLogger("FinxAuth.DB")

# Check for PostgreSQL support
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_USE_POSTGRES = False

if DATABASE_URL:
    try:
        import psycopg2
        import psycopg2.extras
        _USE_POSTGRES = True
        logger.info("PostgreSQL driver (psycopg2) loaded.")
    except ImportError:
        logger.warning("psycopg2 not installed; falling back to SQLite.")
        _USE_POSTGRES = False


def generate_key(prefix="FINX"):
    parts = [secrets.token_hex(2).upper() for _ in range(3)]
    return f"{prefix}-{'-'.join(parts)}"


class LicenseDB:
    def __init__(self, db_path="licenses.db"):
        self.db_path = db_path
        self._active_postgres = False
        self._init_db()

    # -----------------------------------------------------------------------
    # Internal connection helpers
    # -----------------------------------------------------------------------
    def _get_conn(self):
        global _USE_POSTGRES
        if _USE_POSTGRES and DATABASE_URL:
            try:
                url = DATABASE_URL
                if url.startswith("postgres://"):
                    url = url.replace("postgres://", "postgresql://", 1)
                conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor, connect_timeout=5)
                self._active_postgres = True
                return conn
            except Exception as e:
                logger.warning(f"PostgreSQL connection failed: {e}. Falling back to SQLite.")
                self._active_postgres = False

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        self._active_postgres = False
        return conn

    def _placeholder(self):
        return "%s" if self._active_postgres else "?"

    def _init_db(self):
        try:
            conn = self._get_conn()
            try:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS licenses (
                        key TEXT PRIMARY KEY,
                        discord_id TEXT NOT NULL,
                        discord_tag TEXT,
                        hwid TEXT,
                        created_at TEXT NOT NULL,
                        expires_at TEXT,
                        duration_type TEXT DEFAULT 'lifetime',
                        active INTEGER NOT NULL DEFAULT 1,
                        notes TEXT
                    )
                """)
                if not self._active_postgres:
                    try:
                        cur.execute("ALTER TABLE licenses ADD COLUMN expires_at TEXT")
                    except Exception:
                        pass
                    try:
                        cur.execute("ALTER TABLE licenses ADD COLUMN duration_type TEXT DEFAULT 'lifetime'")
                    except Exception:
                        pass
                conn.commit()
                logger.info(f"Database initialized (PostgreSQL: {self._active_postgres})")
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Database init warning: {e}")

    def _row_to_dict(self, row):
        if row is None:
            return None
        return dict(row)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------
    def get_by_discord_id(self, discord_id):
        conn = self._get_conn()
        ph = self._placeholder()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT * FROM licenses WHERE discord_id = {ph} AND active = 1 ORDER BY created_at DESC LIMIT 1",
                (str(discord_id),)
            )
            row = cur.fetchone()
            return self._row_to_dict(row)
        except Exception as e:
            logger.error(f"DB Error get_by_discord_id: {e}")
            return None
        finally:
            conn.close()

    def get_by_key(self, key):
        conn = self._get_conn()
        ph = self._placeholder()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM licenses WHERE key = {ph}", (key.strip(),))
            row = cur.fetchone()
            return self._row_to_dict(row)
        except Exception as e:
            logger.error(f"DB Error get_by_key: {e}")
            return None
        finally:
            conn.close()

    def assign_key(self, discord_id, discord_tag="", days=0, notes=""):
        now = datetime.datetime.now(datetime.timezone.utc)
        created_at = now.isoformat()

        if days and int(days) > 0:
            expires_at = (now + datetime.timedelta(days=int(days))).isoformat()
            duration_type = f"{int(days)}d"
        else:
            expires_at = None
            duration_type = "lifetime"

        conn = self._get_conn()
        ph = self._placeholder()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT * FROM licenses WHERE discord_id = {ph} AND active = 1",
                (str(discord_id),)
            )
            existing = cur.fetchone()

            if existing:
                existing = self._row_to_dict(existing)
                key = existing["key"]
                cur.execute(
                    f"UPDATE licenses SET expires_at = {ph}, duration_type = {ph}, discord_tag = {ph}, notes = {ph}, active = 1 WHERE key = {ph}",
                    (expires_at, duration_type, discord_tag, notes, key)
                )
                conn.commit()
                return key, False, expires_at, duration_type
            else:
                new_key = generate_key()
                cur.execute(
                    f"INSERT INTO licenses (key, discord_id, discord_tag, hwid, created_at, expires_at, duration_type, active, notes) VALUES ({ph}, {ph}, {ph}, NULL, {ph}, {ph}, {ph}, 1, {ph})",
                    (new_key, str(discord_id), discord_tag, created_at, expires_at, duration_type, notes)
                )
                conn.commit()
                return new_key, True, expires_at, duration_type
        except Exception as e:
            logger.error(f"DB Error assign_key: {e}")
            raise
        finally:
            conn.close()

    def reset_hwid_by_discord_id(self, discord_id):
        conn = self._get_conn()
        ph = self._placeholder()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE licenses SET hwid = NULL WHERE discord_id = {ph} AND active = 1",
                (str(discord_id),)
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            logger.error(f"DB Error reset_hwid_by_discord_id: {e}")
            return False
        finally:
            conn.close()

    def reset_hwid_by_key(self, key):
        conn = self._get_conn()
        ph = self._placeholder()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE licenses SET hwid = NULL WHERE key = {ph} AND active = 1",
                (key.strip(),)
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            logger.error(f"DB Error reset_hwid_by_key: {e}")
            return False
        finally:
            conn.close()

    def revoke_key(self, key):
        conn = self._get_conn()
        ph = self._placeholder()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE licenses SET active = 0 WHERE key = {ph}",
                (key.strip(),)
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            logger.error(f"DB Error revoke_key: {e}")
            return False
        finally:
            conn.close()

    def list_all(self):
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM licenses ORDER BY created_at DESC")
            return [self._row_to_dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error(f"DB Error list_all: {e}")
            return []
        finally:
            conn.close()

    def verify(self, key, hwid):
        if not key or not hwid:
            return False, "Missing key or HWID."

        lic = self.get_by_key(key)
        if not lic:
            return False, "Invalid license key. Please contact an admin."

        if not lic["active"]:
            return False, "This license key has been revoked or deactivated."

        expires_at = lic.get("expires_at")
        if expires_at:
            try:
                exp_dt = datetime.datetime.fromisoformat(expires_at)
                now = datetime.datetime.now(datetime.timezone.utc)
                if now > exp_dt:
                    return False, f"Your license key expired on {exp_dt.strftime('%Y-%m-%d')}! Contact an admin to renew."
            except Exception:
                pass

        stored_hwid = lic.get("hwid")
        if not stored_hwid:
            conn = self._get_conn()
            ph = self._placeholder()
            try:
                cur = conn.cursor()
                cur.execute(
                    f"UPDATE licenses SET hwid = {ph} WHERE key = {ph}",
                    (hwid.strip(), key.strip())
                )
                conn.commit()
                return True, "HWID registered successfully. Welcome to FinxClient!"
            except Exception as e:
                logger.error(f"DB Error verify hwid update: {e}")
                return False, "Database error recording HWID."
            finally:
                conn.close()

        if stored_hwid == hwid.strip():
            return True, "Authenticated successfully."

        return False, "HWID mismatch! If you changed PC or components, use /resethwid in Discord."