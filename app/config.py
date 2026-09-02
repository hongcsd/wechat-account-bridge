from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Legacy single-account values are retained for the one-time migration of
    # an existing installation into the multi-tenant database.
    bridge_api_token: Optional[str] = Field(default=None, min_length=16)
    wechat_app_id: Optional[str] = Field(default=None, min_length=1)
    wechat_app_secret: Optional[str] = Field(default=None, min_length=1)
    wechat_callback_token: Optional[str] = None

    bridge_base_url: Optional[str] = Field(default=None, alias="WECHAT_BRIDGE_BASE_URL")
    root_path: str = Field(default="", alias="WECHAT_BRIDGE_ROOT_PATH")
    wechat_api_base: str = "https://api.weixin.qq.com"
    temp_dir: Path = Field(default=Path("/tmp/wechat-account-bridge"), alias="WECHAT_BRIDGE_TEMP_DIR")
    database_path: Path = Field(
        default=Path("/data/wechat-account-bridge.sqlite3"),
        alias="WECHAT_BRIDGE_DATABASE_PATH",
    )
    credential_encryption_key: Optional[str] = Field(
        default=None,
        alias="WECHAT_BRIDGE_CREDENTIAL_ENCRYPTION_KEY",
    )
    api_key_pepper: Optional[str] = Field(default=None, alias="WECHAT_BRIDGE_API_KEY_PEPPER")
    admin_session_secret: Optional[str] = Field(
        default=None,
        alias="WECHAT_BRIDGE_ADMIN_SESSION_SECRET",
    )
    admin_initial_username: str = Field(
        default="admin",
        min_length=3,
        alias="WECHAT_BRIDGE_ADMIN_INITIAL_USERNAME",
    )
    admin_initial_password: Optional[str] = Field(
        default=None,
        min_length=12,
        alias="WECHAT_BRIDGE_ADMIN_INITIAL_PASSWORD",
    )
    admin_session_ttl_seconds: int = Field(
        default=28800,
        ge=900,
        le=604800,
        alias="WECHAT_BRIDGE_ADMIN_SESSION_TTL_SECONDS",
    )
    audit_log_retention_count: int = Field(
        default=10000,
        ge=100,
        le=1000000,
        alias="WECHAT_BRIDGE_AUDIT_LOG_RETENTION_COUNT",
    )
    upload_ttl_seconds: int = Field(default=1800, ge=60, alias="WECHAT_BRIDGE_UPLOAD_TTL_SECONDS")
    keep_failed_uploads: bool = Field(default=False, alias="WECHAT_BRIDGE_KEEP_FAILED_UPLOADS")
    log_level: str = Field(default="INFO", alias="WECHAT_BRIDGE_LOG_LEVEL")

    @property
    def normalized_root_path(self) -> str:
        value = self.root_path.strip()
        if not value or value == "/":
            return ""
        return "/" + value.strip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
