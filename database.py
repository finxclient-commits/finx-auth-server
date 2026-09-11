import secrets
import datetime
import os

# If DATABASE_URL is set (Railway Postgres), use psycopg2; otherwise fall back to SQLite
DATABASE_URL = os.getenv("DATABASE_URL", "")

if DATABASE_URL:
    try:
        import psycopg2
        import psycopg2.extras
        _USE_POSTGRES = True
    except ImportError:
        _USE_POSTGRES = False
else:
    _USE_POSTGRES = False

if not _USE_POSTGRES:
    import sqlite3


def generate_key(prefix="FINX"):
    parts = [secrets.token_hex(2).upper() for _ in range(3)]
    return f"{prefix}-{'-'.join(parts)}"


class LicenseDB:
    def __init__(self, db_path="licenses.db"):
        self.db_path = db_path
        self._init_db()

    # -----------------------------------------------------------------------
    # Internal connection helpers
    # -----------------------------------------------------------------------
    def _get_conn(self):
        if _USE_POSTGRES:
            conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
            return conn
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

    def _placeholder(self):
        """Return the right SQL placeholder: %s for Postgres, ? for SQLite."""
        return "%s" if _USE_POSTGRES else "?"

    def _init_db(self):
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            if _USE_POSTGRES:
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
            else:
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
                # SQLite-only migrations for existing databases
                try:
                    cur.execute("ALTER TABLE licenses ADD COLUMN expires_at TEXT")
                except Exception:
                    pass
                try:
                    cur.execute("ALTER TABLE licenses ADD COLUMN duration_type TEXT DEFAULT 'lifetime'")
                except Exception:
                    pass
            conn.commit()
        finally:
            conn.close()

    def _row_to_dict(self, row):
        if row is None:
            return None
        if isinstance(row, dict):
            return dict(row)
        return dict(row)  # sqlite3.Row also supports dict()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------
    def get_by_discord_id(self, discord_id):
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT * FROM licenses WHERE discord_id = {ph} AND active = 1 ORDER BY created_at DESC LIMIT 1",
                (str(discord_id),)
            )
            row = cur.fetchone()
            return self._row_to_dict(row)
        finally:
            conn.close()

    def get_by_key(self, key):
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM licenses WHERE key = {ph}", (key.strip(),))
            row = cur.fetchone()
            return self._row_to_dict(row)
        finally:
            conn.close()

    def assign_key(self, discord_id, discord_tag="", days=0, notes=""):
        """
        Assigns or updates a key for a user.
        days <= 0 or None => Lifetime
        days > 0 => Expiring in X days
        """
        ph = self._placeholder()
        now = datetime.datetime.now(datetime.timezone.utc)
        created_at = now.isoformat()

        if days and int(days) > 0:
            expires_at = (now + datetime.timedelta(days=int(days))).isoformat()
            duration_type = f"{int(days)}d"
        else:
            expires_at = None
            duration_type = "lifetime"

        conn = self._get_conn()
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
        finally:
            conn.close()

    def reset_hwid_by_discord_id(self, discord_id):
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE licenses SET hwid = NULL WHERE discord_id = {ph} AND active = 1",
                (str(discord_id),)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def reset_hwid_by_key(self, key):
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE licenses SET hwid = NULL WHERE key = {ph} AND active = 1",
                (key.strip(),)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def revoke_key(self, key):
        ph = self._placeholder()
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE licenses SET active = 0 WHERE key = {ph}",
                (key.strip(),)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def list_all(self):
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM licenses ORDER BY created_at DESC")
            return [self._row_to_dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def verify(self, key, hwid):
        ph = self._placeholder()
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
            try:
                cur = conn.cursor()
                cur.execute(
                    f"UPDATE licenses SET hwid = {ph} WHERE key = {ph}",
                    (hwid.strip(), key.strip())
                )
                conn.commit()
                return True, "HWID registered successfully. Welcome to FinxClient!"
            finally:
                conn.close()

        if stored_hwid == hwid.strip():
            return True, "Authenticated successfully."

        return False, "HWID mismatch! If you changed PC or components, use /resethwid in Discord."