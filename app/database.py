from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import Settings
from .crypto import CredentialCipher, hash_password, keyed_digest, verify_password


ALL_AGENT_SCOPES = ("content:read", "content:write", "metrics:read", "publish")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class AccountRecord:
    id: int
    slug: str
    display_name: str
    app_id: str
    app_secret_encrypted: str
    callback_token_encrypted: Optional[str]
    enabled: bool
    created_at: str
    updated_at: str
    last_verified_at: Optional[str]
    last_error: Optional[str]


@dataclass(frozen=True)
class AgentPrincipal:
    key_id: int
    key_name: str
    key_prefix: str
    scopes: frozenset[str]
    account: AccountRecord


@dataclass(frozen=True)
class AdminRecord:
    id: int
    username: str
    password_hash: str
    session_version: int
    must_change_password: bool
    created_at: str
    last_login_at: Optional[str]


class Repository:
    def __init__(self, settings: Settings):
        self.settings = settings
        if not settings.credential_encryption_key:
            raise RuntimeError("WECHAT_BRIDGE_CREDENTIAL_ENCRYPTION_KEY is required")
        if not settings.api_key_pepper:
            raise RuntimeError("WECHAT_BRIDGE_API_KEY_PEPPER is required")
        self.cipher = CredentialCipher(settings.credential_encryption_key)
        self.api_key_pepper = settings.api_key_pepper
        self.path = settings.database_path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 15000")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    session_version INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    app_id TEXT NOT NULL UNIQUE,
                    app_secret_encrypted TEXT NOT NULL,
                    callback_token_encrypted TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_verified_at TEXT,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS agent_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    key_prefix TEXT NOT NULL UNIQUE,
                    key_hash TEXT NOT NULL UNIQUE,
                    token_encrypted TEXT,
                    scopes_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    revoked_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_agent_keys_hash ON agent_keys(key_hash);
                CREATE INDEX IF NOT EXISTS idx_agent_keys_account ON agent_keys(account_id);

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT,
                    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                    action TEXT NOT NULL,
                    target TEXT,
                    result TEXT NOT NULL,
                    remote_ip TEXT,
                    request_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_account ON audit_logs(account_id);
                """
            )
            agent_key_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(agent_keys)").fetchall()
            }
            if "token_encrypted" not in agent_key_columns:
                conn.execute("ALTER TABLE agent_keys ADD COLUMN token_encrypted TEXT")
            self._prune_audit_logs(conn)
        self._bootstrap_admin()
        self._bootstrap_legacy_account()

    def _bootstrap_admin(self) -> None:
        with self.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
            if count:
                return
            if not self.settings.admin_initial_password:
                raise RuntimeError(
                    "WECHAT_BRIDGE_ADMIN_INITIAL_PASSWORD is required to create the first administrator"
                )
            conn.execute(
                """INSERT INTO admins
                   (username, password_hash, session_version, must_change_password, created_at)
                   VALUES (?, ?, 1, 1, ?)""",
                (
                    self.settings.admin_initial_username,
                    hash_password(self.settings.admin_initial_password),
                    utc_now(),
                ),
            )

    def _bootstrap_legacy_account(self) -> None:
        if not self.settings.wechat_app_id or not self.settings.wechat_app_secret:
            return
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM accounts WHERE slug = 'xiaobao'").fetchone()
            if row:
                account_id = int(row["id"])
            else:
                cursor = conn.execute(
                    """INSERT INTO accounts
                       (slug, display_name, app_id, app_secret_encrypted, callback_token_encrypted,
                        enabled, created_at, updated_at)
                       VALUES ('xiaobao', '心小宝', ?, ?, ?, 1, ?, ?)""",
                    (
                        self.settings.wechat_app_id,
                        self.cipher.encrypt(self.settings.wechat_app_secret),
                        self.cipher.encrypt(self.settings.wechat_callback_token),
                        now,
                        now,
                    ),
                )
                account_id = int(cursor.lastrowid)

            if self.settings.bridge_api_token:
                digest = keyed_digest(self.settings.bridge_api_token, self.api_key_pepper)
                existing = conn.execute(
                    "SELECT id FROM agent_keys WHERE key_hash = ?", (digest,)
                ).fetchone()
                encrypted_token = self.cipher.encrypt(self.settings.bridge_api_token)
                if existing:
                    conn.execute(
                        """UPDATE agent_keys SET token_encrypted = COALESCE(token_encrypted, ?)
                           WHERE id = ?""",
                        (encrypted_token, existing["id"]),
                    )
                else:
                    conn.execute(
                        """INSERT INTO agent_keys
                           (account_id, name, key_prefix, key_hash, token_encrypted,
                            scopes_json, enabled, created_at)
                           VALUES (?, '心小宝现有运营智能体', ?, ?, ?, ?, 1, ?)""",
                        (
                            account_id,
                            "legacy_" + digest[:12],
                            digest,
                            encrypted_token,
                            json.dumps(ALL_AGENT_SCOPES),
                            now,
                        ),
                    )

    @staticmethod
    def _account(row: sqlite3.Row) -> AccountRecord:
        return AccountRecord(
            id=int(row["id"]),
            slug=str(row["slug"]),
            display_name=str(row["display_name"]),
            app_id=str(row["app_id"]),
            app_secret_encrypted=str(row["app_secret_encrypted"]),
            callback_token_encrypted=row["callback_token_encrypted"],
            enabled=bool(row["enabled"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            last_verified_at=row["last_verified_at"],
            last_error=row["last_error"],
        )

    @staticmethod
    def _admin(row: sqlite3.Row) -> AdminRecord:
        return AdminRecord(
            id=int(row["id"]),
            username=str(row["username"]),
            password_hash=str(row["password_hash"]),
            session_version=int(row["session_version"]),
            must_change_password=bool(row["must_change_password"]),
            created_at=str(row["created_at"]),
            last_login_at=row["last_login_at"],
        )

    def authenticate_agent(self, token: str) -> Optional[AgentPrincipal]:
        digest = keyed_digest(token, self.api_key_pepper)
        with self.connect() as conn:
            row = conn.execute(
                """SELECT k.id AS key_id, k.name AS key_name, k.key_prefix,
                          k.scopes_json, a.*
                   FROM agent_keys k JOIN accounts a ON a.id = k.account_id
                   WHERE k.key_hash = ? AND k.enabled = 1 AND k.revoked_at IS NULL
                     AND a.enabled = 1""",
                (digest,),
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE agent_keys SET last_used_at = ? WHERE id = ?", (utc_now(), row["key_id"]))
        return AgentPrincipal(
            key_id=int(row["key_id"]),
            key_name=str(row["key_name"]),
            key_prefix=str(row["key_prefix"]),
            scopes=frozenset(json.loads(row["scopes_json"])),
            account=self._account(row),
        )

    def get_account(self, account_id: int) -> Optional[AccountRecord]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return self._account(row) if row else None

    def get_account_by_slug(self, slug: str) -> Optional[AccountRecord]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE slug = ?", (slug,)).fetchone()
        return self._account(row) if row else None

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT a.*,
                          COUNT(k.id) AS key_count,
                          SUM(CASE WHEN k.enabled = 1 AND k.revoked_at IS NULL THEN 1 ELSE 0 END) AS active_key_count
                   FROM accounts a LEFT JOIN agent_keys k ON k.account_id = a.id
                   GROUP BY a.id ORDER BY a.created_at"""
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            app_id = str(item["app_id"])
            item["masked_app_id"] = app_id[:6] + "…" + app_id[-4:] if len(app_id) > 12 else app_id
            item["enabled"] = bool(item["enabled"])
            item["active_key_count"] = int(item["active_key_count"] or 0)
            item["key_count"] = int(item["key_count"] or 0)
            result.append(item)
        return result

    def create_account(
        self,
        slug: str,
        display_name: str,
        app_id: str,
        app_secret: str,
        callback_token: Optional[str],
    ) -> int:
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO accounts
                   (slug, display_name, app_id, app_secret_encrypted, callback_token_encrypted,
                    enabled, created_at, updated_at, last_verified_at, last_error)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, NULL)""",
                (
                    slug,
                    display_name,
                    app_id,
                    self.cipher.encrypt(app_secret),
                    self.cipher.encrypt(callback_token),
                    now,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def update_account(
        self,
        account_id: int,
        display_name: str,
        app_id: str,
        app_secret: Optional[str],
        callback_token: Optional[str],
        replace_callback: bool,
    ) -> None:
        account = self.get_account(account_id)
        if not account:
            raise ValueError("account not found")
        secret_encrypted = (
            self.cipher.encrypt(app_secret) if app_secret else account.app_secret_encrypted
        )
        callback_encrypted = (
            self.cipher.encrypt(callback_token) if replace_callback else account.callback_token_encrypted
        )
        with self.connect() as conn:
            conn.execute(
                """UPDATE accounts SET display_name = ?, app_id = ?, app_secret_encrypted = ?,
                          callback_token_encrypted = ?, updated_at = ?, last_verified_at = ?, last_error = NULL
                   WHERE id = ?""",
                (
                    display_name,
                    app_id,
                    secret_encrypted,
                    callback_encrypted,
                    utc_now(),
                    utc_now(),
                    account_id,
                ),
            )

    def set_account_enabled(self, account_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE accounts SET enabled = ?, updated_at = ? WHERE id = ?",
                (1 if enabled else 0, utc_now(), account_id),
            )

    def mark_account_test(self, account_id: int, error: Optional[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE accounts SET last_verified_at = ?, last_error = ? WHERE id = ?",
                (utc_now(), error[:500] if error else None, account_id),
            )

    def decrypt_account_credentials(self, account: AccountRecord) -> tuple[str, str, Optional[str]]:
        secret = self.cipher.decrypt(account.app_secret_encrypted)
        if not secret:
            raise ValueError("account AppSecret is missing")
        return account.app_id, secret, self.cipher.decrypt(account.callback_token_encrypted)

    def list_agent_keys(self, account_id: Optional[int] = None) -> list[dict[str, Any]]:
        query = (
            """SELECT k.*, a.display_name AS account_name, a.slug AS account_slug
               FROM agent_keys k JOIN accounts a ON a.id = k.account_id"""
        )
        params: tuple[Any, ...] = ()
        if account_id is not None:
            query += " WHERE k.account_id = ?"
            params = (account_id,)
        query += " ORDER BY k.created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("key_hash", None)
            item["can_copy"] = bool(item.pop("token_encrypted", None))
            item["scopes"] = json.loads(item.pop("scopes_json"))
            item["enabled"] = bool(item["enabled"])
            result.append(item)
        return result

    def create_agent_key(self, account_id: int, name: str, scopes: Iterable[str]) -> tuple[str, str]:
        allowed = [scope for scope in ALL_AGENT_SCOPES if scope in set(scopes)]
        if not allowed:
            raise ValueError("at least one scope is required")
        public_prefix = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
        token = f"wcb_live_{public_prefix}_{secrets.token_urlsafe(32)}"
        digest = keyed_digest(token, self.api_key_pepper)
        key_prefix = f"wcb_live_{public_prefix}"
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO agent_keys
                   (account_id, name, key_prefix, key_hash, token_encrypted,
                    scopes_json, enabled, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    account_id,
                    name,
                    key_prefix,
                    digest,
                    self.cipher.encrypt(token),
                    json.dumps(allowed),
                    utc_now(),
                ),
            )
        return token, key_prefix

    def reveal_agent_key(self, key_id: int) -> Optional[tuple[str, int, str]]:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT account_id, key_prefix, key_hash, token_encrypted
                   FROM agent_keys
                   WHERE id = ? AND enabled = 1 AND revoked_at IS NULL""",
                (key_id,),
            ).fetchone()
        if not row or not row["token_encrypted"]:
            return None
        token = self.cipher.decrypt(str(row["token_encrypted"]))
        if not token or keyed_digest(token, self.api_key_pepper) != row["key_hash"]:
            raise ValueError("stored API key failed integrity verification")
        return token, int(row["account_id"]), str(row["key_prefix"])

    def rotate_agent_key(self, key_id: int) -> tuple[str, str, int, str]:
        public_prefix = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
        token = f"wcb_live_{public_prefix}_{secrets.token_urlsafe(32)}"
        digest = keyed_digest(token, self.api_key_pepper)
        key_prefix = f"wcb_live_{public_prefix}"
        with self.connect() as conn:
            row = conn.execute(
                """SELECT account_id, name FROM agent_keys
                   WHERE id = ? AND enabled = 1 AND revoked_at IS NULL""",
                (key_id,),
            ).fetchone()
            if not row:
                raise ValueError("API Key 不存在或已撤销")
            conn.execute(
                """UPDATE agent_keys
                   SET key_prefix = ?, key_hash = ?, token_encrypted = ?,
                       created_at = ?, last_used_at = NULL
                   WHERE id = ?""",
                (
                    key_prefix,
                    digest,
                    self.cipher.encrypt(token),
                    utc_now(),
                    key_id,
                ),
            )
        return token, key_prefix, int(row["account_id"]), str(row["name"])

    def revoke_agent_key(self, key_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE agent_keys SET enabled = 0, revoked_at = ? WHERE id = ?",
                (utc_now(), key_id),
            )

    def get_admin(self, admin_id: int) -> Optional[AdminRecord]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM admins WHERE id = ?", (admin_id,)).fetchone()
        return self._admin(row) if row else None

    def authenticate_admin(self, username: str, password: str) -> Optional[AdminRecord]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
            if not row or not verify_password(password, row["password_hash"]):
                return None
            conn.execute("UPDATE admins SET last_login_at = ? WHERE id = ?", (utc_now(), row["id"]))
        return self._admin(row)

    def change_admin_password(self, admin_id: int, current_password: str, new_password: str) -> AdminRecord:
        admin = self.get_admin(admin_id)
        if not admin or not verify_password(current_password, admin.password_hash):
            raise ValueError("当前密码不正确")
        encoded = hash_password(new_password)
        with self.connect() as conn:
            conn.execute(
                """UPDATE admins SET password_hash = ?, session_version = session_version + 1,
                          must_change_password = 0 WHERE id = ?""",
                (encoded, admin_id),
            )
        updated = self.get_admin(admin_id)
        if not updated:
            raise RuntimeError("administrator disappeared during password update")
        return updated

    def _prune_audit_logs(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """DELETE FROM audit_logs
               WHERE id <= COALESCE(
                   (SELECT id FROM audit_logs ORDER BY id DESC LIMIT 1 OFFSET ?),
                   0
               )""",
            (self.settings.audit_log_retention_count,),
        )

    def record_audit(
        self,
        actor_type: str,
        action: str,
        result: str,
        actor_id: Optional[str] = None,
        account_id: Optional[int] = None,
        target: Optional[str] = None,
        remote_ip: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO audit_logs
                   (actor_type, actor_id, account_id, action, target, result, remote_ip, request_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    actor_type,
                    actor_id,
                    account_id,
                    action,
                    target,
                    result,
                    remote_ip,
                    request_id,
                    utc_now(),
                ),
            )
            self._prune_audit_logs(conn)

    def list_audit_logs(self, limit: int = 80) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT l.*, a.display_name AS account_name
                   FROM audit_logs l LEFT JOIN accounts a ON a.id = l.account_id
                   ORDER BY l.id DESC LIMIT ?""",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def dashboard_stats(self) -> dict[str, int]:
        with self.connect() as conn:
            accounts = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            active_accounts = conn.execute("SELECT COUNT(*) FROM accounts WHERE enabled = 1").fetchone()[0]
            keys = conn.execute(
                "SELECT COUNT(*) FROM agent_keys WHERE enabled = 1 AND revoked_at IS NULL"
            ).fetchone()[0]
            calls_today = conn.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE actor_type = 'agent' AND created_at >= date('now')"
            ).fetchone()[0]
        return {
            "accounts": int(accounts),
            "active_accounts": int(active_accounts),
            "active_keys": int(keys),
            "calls_today": int(calls_today),
        }
