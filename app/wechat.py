from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import Settings
from .metrics import (
    build_article_total_detail_result,
    each_day,
    iter_metric_items,
    normalize_metric_item,
    validate_date_range,
)


TOKEN_REFRESH_SKEW_SECONDS = 300
TOKEN_ERROR_CODES = {40001, 40014, 42001}


class WeChatAPIError(RuntimeError):
    def __init__(self, message: str, payload: dict[str, Any], status_code: int = 502):
        super().__init__(message)
        self.payload = payload
        self.status_code = status_code


@dataclass
class TokenCache:
    access_token: Optional[str] = None
    expires_at: float = 0

    def valid(self) -> bool:
        return bool(self.access_token) and time.time() < self.expires_at

    def expires_in_seconds(self) -> Optional[int]:
        if not self.access_token:
            return None
        return max(0, int(self.expires_at - time.time()))


@dataclass(frozen=True)
class WeChatCredentials:
    app_id: str
    app_secret: str
    api_base: str = "https://api.weixin.qq.com"


class WeChatClient:
    def __init__(self, credentials: WeChatCredentials | Settings):
        if isinstance(credentials, Settings):
            if not credentials.wechat_app_id or not credentials.wechat_app_secret:
                raise ValueError("WeChat credentials are not configured")
            self.credentials = WeChatCredentials(
                app_id=credentials.wechat_app_id,
                app_secret=credentials.wechat_app_secret,
                api_base=credentials.wechat_api_base,
            )
        else:
            self.credentials = credentials
        self._token = TokenCache()
        self._token_lock = asyncio.Lock()

    def token_status(self) -> TokenCache:
        return self._token

    async def get_access_token(self, force_refresh: bool = False) -> str:
        async with self._token_lock:
            if not force_refresh and self._token.valid():
                return self._token.access_token or ""

            url = f"{self.credentials.api_base}/cgi-bin/stable_token"
            payload = {
                "grant_type": "client_credential",
                "appid": self.credentials.app_id,
                "secret": self.credentials.app_secret,
                "force_refresh": force_refresh,
            }
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, json=payload)
            data = response.json()
            if response.status_code >= 400 or data.get("errcode"):
                raise WeChatAPIError("Failed to get WeChat access_token", data)

            expires_in = int(data.get("expires_in") or 7200)
            self._token = TokenCache(
                access_token=data["access_token"],
                expires_at=time.time() + max(60, expires_in - TOKEN_REFRESH_SKEW_SECONDS),
            )
            return self._token.access_token

    async def post_json_raw(
        self,
        endpoint: str,
        payload: dict[str, Any],
        retry_on_token_error: bool = True,
    ) -> dict[str, Any]:
        token = await self.get_access_token()
        url = f"{self.credentials.api_base}{endpoint}"
        params = {"access_token": token}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, params=params, json=payload)
        data = response.json()

        if retry_on_token_error and data.get("errcode") in TOKEN_ERROR_CODES:
            token = await self.get_access_token(force_refresh=True)
            params = {"access_token": token}
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, params=params, json=payload)
            data = response.json()

        if response.status_code >= 400:
            raise WeChatAPIError("WeChat API returned an HTTP error", data)
        return data

    async def post_json(self, endpoint: str, payload: dict[str, Any], retry_on_token_error: bool = True) -> dict[str, Any]:
        data = await self.post_json_raw(endpoint, payload, retry_on_token_error=retry_on_token_error)
        if data.get("errcode", 0) not in (0, None):
            raise WeChatAPIError("WeChat API returned an error", data)
        return data

    async def post_file(
        self,
        endpoint: str,
        file_path: Path,
        filename: str,
        content_type: str,
        params: Optional[dict[str, str]] = None,
        retry_on_token_error: bool = True,
    ) -> dict[str, Any]:
        token = await self.get_access_token()
        query = {"access_token": token, **(params or {})}
        url = f"{self.credentials.api_base}{endpoint}"
        data = await self._post_file_once(url, query, file_path, filename, content_type)

        if retry_on_token_error and data.get("errcode") in TOKEN_ERROR_CODES:
            token = await self.get_access_token(force_refresh=True)
            query = {"access_token": token, **(params or {})}
            data = await self._post_file_once(url, query, file_path, filename, content_type)

        if data.get("errcode", 0) not in (0, None):
            raise WeChatAPIError("WeChat file upload returned an error", data)
        return data

    async def _post_file_once(
        self,
        url: str,
        params: dict[str, str],
        file_path: Path,
        filename: str,
        content_type: str,
    ) -> dict[str, Any]:
        with file_path.open("rb") as file_obj:
            files = {"media": (filename, file_obj, content_type)}
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(url, params=params, files=files)
        data = response.json()
        if response.status_code >= 400:
            raise WeChatAPIError("WeChat file upload HTTP error", data)
        return data

    async def upload_article_image(self, file_path: Path, filename: str, content_type: str) -> dict[str, Any]:
        return await self.post_file("/cgi-bin/media/uploadimg", file_path, filename, content_type)

    async def upload_permanent_material(
        self,
        file_path: Path,
        filename: str,
        content_type: str,
        material_type: str,
    ) -> dict[str, Any]:
        return await self.post_file(
            "/cgi-bin/material/add_material",
            file_path,
            filename,
            content_type,
            params={"type": material_type},
        )

    async def create_draft(self, articles: list[dict[str, Any]]) -> dict[str, Any]:
        return await self.post_json("/cgi-bin/draft/add", {"articles": articles})

    async def get_draft(self, media_id: str) -> dict[str, Any]:
        return await self.post_json("/cgi-bin/draft/get", {"media_id": media_id})

    async def batch_get_drafts(self, offset: int = 0, count: int = 20, no_content: int = 1) -> dict[str, Any]:
        return await self.post_json(
            "/cgi-bin/draft/batchget",
            {"offset": offset, "count": count, "no_content": no_content},
        )

    async def batch_get_freepublish(self, offset: int = 0, count: int = 20, no_content: int = 1) -> dict[str, Any]:
        return await self.post_json(
            "/cgi-bin/freepublish/batchget",
            {"offset": offset, "count": count, "no_content": no_content},
        )

    async def get_freepublish_article(self, article_id: str) -> dict[str, Any]:
        return await self.post_json("/cgi-bin/freepublish/getarticle", {"article_id": article_id})

    async def fetch_article_total_detail(self, begin_date: str, end_date: str) -> dict[str, Any]:
        begin, end = validate_date_range(begin_date, end_date)
        articles: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        raw_daily: list[dict[str, Any]] = []

        for day in each_day(begin, end):
            day_text = day.isoformat()
            raw = await self.post_json_raw(
                "/datacube/getarticletotaldetail",
                {"begin_date": day_text, "end_date": day_text},
            )
            errcode = raw.get("errcode", 0)
            ok = errcode in (0, None)
            items = [normalize_metric_item(item) for item in iter_metric_items(raw)] if ok else []
            if ok:
                articles.extend(items)
            else:
                failures.append({"date": day_text, "errcode": errcode, "errmsg": raw.get("errmsg", "")})
            raw_daily.append(
                {
                    "date": day_text,
                    "ok": ok,
                    "errcode": errcode,
                    "errmsg": raw.get("errmsg", ""),
                    "article_count": len(items),
                    "raw": raw,
                }
            )

        return build_article_total_detail_result(begin_date, end_date, articles, failures, raw_daily)

    async def publish_draft(self, media_id: str) -> dict[str, Any]:
        return await self.post_json("/cgi-bin/freepublish/submit", {"media_id": media_id})

    async def get_publish_status(self, publish_id: str) -> dict[str, Any]:
        return await self.post_json("/cgi-bin/freepublish/get", {"publish_id": publish_id})
