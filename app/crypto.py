from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken


def _fernet_key(value: str) -> bytes:
    """Turn an environment secret of any reasonable format into a Fernet key."""
    return base64.urlsafe_b64encode(hashlib.sha256(value.encode("utf-8")).digest())


class CredentialCipher:
    def __init__(self, secret: str):
        if len(secret) < 32:
            raise ValueError("credential encryption key must contain at least 32 characters")
        self._fernet = Fernet(_fernet_key(secret))

    def encrypt(self, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return None
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("stored credential cannot be decrypted with the configured key") from exc


def keyed_digest(value: str, pepper: str) -> str:
    return hmac.new(pepper.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must contain at least 12 characters")
    salt = os.urandom(16)
    iterations = 600_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)
    return "pbkdf2_sha256$%s$%s$%s" % (
        iterations,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations), dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class AdminSession:
    admin_id: int
    username: str
    session_version: int
    csrf_token: str
    expires_at: int


class AdminSessionSigner:
    def __init__(self, secret: str, ttl_seconds: int):
        if len(secret) < 32:
            raise ValueError("admin session secret must contain at least 32 characters")
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    def create(self, admin_id: int, username: str, session_version: int) -> tuple[str, AdminSession]:
        session = AdminSession(
            admin_id=admin_id,
            username=username,
            session_version=session_version,
            csrf_token=secrets.token_urlsafe(24),
            expires_at=int(time.time()) + self.ttl_seconds,
        )
        payload = {
            "admin_id": session.admin_id,
            "username": session.username,
            "session_version": session.session_version,
            "csrf_token": session.csrf_token,
            "expires_at": session.expires_at,
        }
        encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        signature = _b64encode(hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest())
        return f"{encoded}.{signature}", session

    def verify(self, token: Optional[str]) -> Optional[AdminSession]:
        if not token or "." not in token:
            return None
        encoded, signature = token.rsplit(".", 1)
        expected = _b64encode(hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            payload: dict[str, Any] = json.loads(_b64decode(encoded))
            session = AdminSession(
                admin_id=int(payload["admin_id"]),
                username=str(payload["username"]),
                session_version=int(payload["session_version"]),
                csrf_token=str(payload["csrf_token"]),
                expires_at=int(payload["expires_at"]),
            )
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None
        if session.expires_at < int(time.time()):
            return None
        return session
