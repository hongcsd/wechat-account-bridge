from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.responses import PlainTextResponse

from .config import Settings, get_settings
from .admin import router as admin_router
from .schemas import (
    ArticleTotalDetailRequest,
    ArticleTotalDetailResponse,
    DraftCreateRequest,
    DraftCreateResponse,
    DraftBatchGetRequest,
    DraftBatchGetResponse,
    DraftGetRequest,
    DraftGetResponse,
    FreePublishBatchGetRequest,
    FreePublishBatchGetResponse,
    FreePublishGetArticleRequest,
    FreePublishGetArticleResponse,
    MaterialType,
    PublishRequest,
    PublishResponse,
    PublishStatusRequest,
    PublishStatusResponse,
    TokenRefreshRequest,
    TokenRefreshResponse,
    TokenStatusResponse,
    UploadResponse,
)
from .security import require_bridge_token, require_scope
from .services import get_client_registry, get_repository
from .tempfiles import cleanup_expired, store_upload
from .wechat import WeChatAPIError, WeChatClient

logger = logging.getLogger("wechat_account_bridge")

WECHAT_ARTICLE_IMAGE_MAX_BYTES = 1 * 1024 * 1024
WECHAT_PERMANENT_IMAGE_MAX_BYTES = 10 * 1024 * 1024
WECHAT_THUMB_MAX_BYTES = 64 * 1024
AGENT_GUIDE_PATH = Path(__file__).with_name("agent-guide.md")


app = FastAPI(
    title="wechat-account-bridge",
    version="1.1.5",
    dependencies=[Depends(require_bridge_token)],
    docs_url="/docs" if os.getenv("WECHAT_BRIDGE_ENABLE_DOCS", "false").lower() == "true" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if os.getenv("WECHAT_BRIDGE_ENABLE_DOCS", "false").lower() == "true" else None,
    root_path=os.getenv("WECHAT_BRIDGE_ROOT_PATH", "").rstrip("/"),
)
app.include_router(admin_router)


async def get_wechat_client(request: Request) -> WeChatClient:
    principal = getattr(request.state, "agent_principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="Missing agent identity")
    return get_client_registry().get(principal.account)


@app.on_event("startup")
async def startup() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    get_repository().initialize()
    removed = cleanup_expired(settings.temp_dir, settings.upload_ttl_seconds)
    if removed:
        logger.info("Cleaned %s expired temp uploads", removed)


@app.middleware("http")
async def security_and_audit_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "img-src data:; connect-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    if request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    principal = getattr(request.state, "agent_principal", None)
    if principal is not None and request.url.path.startswith("/wechat/"):
        try:
            get_repository().record_audit(
                actor_type="agent",
                actor_id=str(principal.key_id),
                account_id=principal.account.id,
                action=f"{request.method} {request.url.path}",
                target=principal.key_prefix,
                result="success" if response.status_code < 400 else "failed",
                remote_ip=request.headers.get("x-forwarded-for", "").split(",", 1)[0]
                or (request.client.host if request.client else None),
                request_id=request_id,
            )
        except Exception:
            logger.exception("Failed to record API audit log")
    return response


@app.exception_handler(WeChatAPIError)
async def handle_wechat_error(_request, exc: WeChatAPIError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"message": str(exc), "wechat": exc.payload}},
    )


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/readyz")
async def readyz(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    get_repository().connect().close()
    return {"ok": True}


@app.get("/agent-guide", response_class=PlainTextResponse)
async def agent_guide() -> PlainTextResponse:
    response = PlainTextResponse(
        AGENT_GUIDE_PATH.read_text(encoding="utf-8"),
        media_type="text/markdown",
    )
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@app.post("/maintenance/cleanup-temp")
async def cleanup_temp(request: Request, settings: Settings = Depends(get_settings)) -> dict[str, object]:
    require_scope(request, "content:write")
    return {"ok": True, "removed": cleanup_expired(settings.temp_dir, settings.upload_ttl_seconds)}


@app.get("/wechat/token/status", response_model=TokenStatusResponse)
async def token_status(request: Request, client: WeChatClient = Depends(get_wechat_client)) -> TokenStatusResponse:
    require_scope(request, "content:read")
    cache = client.token_status()
    return TokenStatusResponse(cached=cache.valid(), expires_in_seconds=cache.expires_in_seconds())


@app.post("/wechat/token/refresh", response_model=TokenRefreshResponse)
async def token_refresh(
    body: TokenRefreshRequest,
    request: Request,
    client: WeChatClient = Depends(get_wechat_client),
) -> TokenRefreshResponse:
    require_scope(request, "content:write")
    await client.get_access_token(force_refresh=body.force_refresh)
    cache = client.token_status()
    return TokenRefreshResponse(cached=cache.valid(), expires_in_seconds=cache.expires_in_seconds())


@app.post("/wechat/media/article-image", response_model=UploadResponse)
async def upload_article_image(
    request: Request,
    media: Annotated[UploadFile, File()],
    settings: Settings = Depends(get_settings),
    client: WeChatClient = Depends(get_wechat_client),
) -> UploadResponse:
    require_scope(request, "content:write")
    stored = await store_upload(media, settings.temp_dir, max_bytes=WECHAT_ARTICLE_IMAGE_MAX_BYTES)
    try:
        wechat = await client.upload_article_image(stored.path, stored.filename, stored.content_type)
    except Exception:
        if not settings.keep_failed_uploads:
            stored.path.unlink(missing_ok=True)
        raise
    else:
        stored.path.unlink(missing_ok=True)
    return UploadResponse(
        sha256=stored.sha256,
        filename=stored.filename,
        size=stored.size,
        wechat=wechat,
    )


@app.post("/wechat/material", response_model=UploadResponse)
async def upload_material(
    request: Request,
    media: Annotated[UploadFile, File()],
    material_type: Annotated[MaterialType, Form()] = "thumb",
    settings: Settings = Depends(get_settings),
    client: WeChatClient = Depends(get_wechat_client),
) -> UploadResponse:
    require_scope(request, "content:write")
    max_bytes = (
        WECHAT_THUMB_MAX_BYTES
        if material_type == "thumb"
        else WECHAT_PERMANENT_IMAGE_MAX_BYTES
    )
    stored = await store_upload(media, settings.temp_dir, max_bytes=max_bytes)
    try:
        wechat = await client.upload_permanent_material(
            stored.path,
            stored.filename,
            stored.content_type,
            material_type,
        )
    except Exception:
        if not settings.keep_failed_uploads:
            stored.path.unlink(missing_ok=True)
        raise
    else:
        stored.path.unlink(missing_ok=True)
    return UploadResponse(
        sha256=stored.sha256,
        filename=stored.filename,
        size=stored.size,
        wechat=wechat,
    )


@app.post("/wechat/drafts", response_model=DraftCreateResponse)
async def create_draft(
    body: DraftCreateRequest,
    request: Request,
    client: WeChatClient = Depends(get_wechat_client),
) -> DraftCreateResponse:
    require_scope(request, "content:write")
    wechat = await client.create_draft(body.articles)
    media_id = wechat.get("media_id")
    draft = None
    verified = False
    if body.verify_after_create and media_id:
        draft = await client.get_draft(media_id)
        verified = "news_item" in draft
    return DraftCreateResponse(media_id=media_id, wechat=wechat, verified=verified, draft=draft)


@app.post("/wechat/drafts/get", response_model=DraftGetResponse)
async def get_draft(
    body: DraftGetRequest,
    request: Request,
    client: WeChatClient = Depends(get_wechat_client),
) -> DraftGetResponse:
    require_scope(request, "content:read")
    wechat = await client.get_draft(body.media_id)
    return DraftGetResponse(media_id=body.media_id, wechat=wechat)


@app.post("/wechat/drafts/batchget", response_model=DraftBatchGetResponse)
async def batch_get_drafts(
    body: DraftBatchGetRequest,
    request: Request,
    client: WeChatClient = Depends(get_wechat_client),
) -> DraftBatchGetResponse:
    require_scope(request, "content:read")
    wechat = await client.batch_get_drafts(body.offset, body.count, body.no_content)
    return DraftBatchGetResponse(wechat=wechat)


@app.post("/wechat/freepublish/batchget", response_model=FreePublishBatchGetResponse)
async def batch_get_freepublish(
    body: FreePublishBatchGetRequest,
    request: Request,
    client: WeChatClient = Depends(get_wechat_client),
) -> FreePublishBatchGetResponse:
    require_scope(request, "content:read")
    wechat = await client.batch_get_freepublish(body.offset, body.count, body.no_content)
    return FreePublishBatchGetResponse(wechat=wechat)


@app.post("/wechat/freepublish/getarticle", response_model=FreePublishGetArticleResponse)
async def get_freepublish_article(
    body: FreePublishGetArticleRequest,
    request: Request,
    client: WeChatClient = Depends(get_wechat_client),
) -> FreePublishGetArticleResponse:
    require_scope(request, "content:read")
    wechat = await client.get_freepublish_article(body.article_id)
    return FreePublishGetArticleResponse(article_id=body.article_id, wechat=wechat)


@app.post("/wechat/metrics/article-total-detail", response_model=ArticleTotalDetailResponse)
async def article_total_detail(
    body: ArticleTotalDetailRequest,
    request: Request,
    client: WeChatClient = Depends(get_wechat_client),
) -> ArticleTotalDetailResponse:
    require_scope(request, "metrics:read")
    try:
        metrics = await client.fetch_article_total_detail(body.begin_date, body.end_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ArticleTotalDetailResponse(metrics=metrics)


@app.post("/wechat/publish", response_model=PublishResponse)
async def publish_draft(
    body: PublishRequest,
    request: Request,
    client: WeChatClient = Depends(get_wechat_client),
) -> PublishResponse:
    require_scope(request, "publish")
    wechat = await client.publish_draft(body.media_id)
    return PublishResponse(
        publish_id=wechat.get("publish_id"),
        msg_data_id=wechat.get("msg_data_id"),
        wechat=wechat,
    )


@app.post("/wechat/publish/status", response_model=PublishStatusResponse)
async def publish_status(
    body: PublishStatusRequest,
    request: Request,
    client: WeChatClient = Depends(get_wechat_client),
) -> PublishStatusResponse:
    require_scope(request, "publish")
    wechat = await client.get_publish_status(body.publish_id)
    return PublishStatusResponse(
        publish_id=wechat.get("publish_id"),
        publish_status=wechat.get("publish_status"),
        article_id=wechat.get("article_id"),
        wechat=wechat,
    )


@app.get("/wechat/callback", response_class=PlainTextResponse)
@app.get("/wechat/callback/{account_slug}", response_class=PlainTextResponse)
async def verify_callback(
    account_slug: str = "xiaobao",
    signature: Optional[str] = None,
    timestamp: Optional[str] = None,
    nonce: Optional[str] = None,
    echostr: Optional[str] = None,
) -> str:
    if not all([signature, timestamp, nonce, echostr]):
        raise HTTPException(status_code=400, detail="Missing callback verification parameters")
    account = get_repository().get_account_by_slug(account_slug)
    if not account or not account.enabled:
        raise HTTPException(status_code=404, detail="WeChat account not found")
    _app_id, _app_secret, callback_token = get_repository().decrypt_account_credentials(account)
    if not callback_token:
        raise HTTPException(status_code=503, detail="WeChat callback token is not configured")

    expected = hashlib.sha1(
        "".join(sorted([callback_token, timestamp or "", nonce or ""])).encode("utf-8")
    ).hexdigest()
    if expected != signature:
        raise HTTPException(status_code=403, detail="Invalid callback signature")
    return echostr or ""
