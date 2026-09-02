from __future__ import annotations

import re
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .crypto import AdminSession, AdminSessionSigner
from .database import ALL_AGENT_SCOPES, AdminRecord
from .services import get_client_registry, get_repository
from .wechat import WeChatAPIError, WeChatClient, WeChatCredentials


router = APIRouter(prefix="/admin", include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SESSION_COOKIE = "wcb_admin_session"
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
ADMIN_VIEWS = {"accounts", "audit", "security"}
ADMIN_VIEW_META = {
    "accounts": ("公众号管理", "配置公众号连接，并管理每个公众号的智能体 API Key。"),
    "audit": ("操作审计", "查看管理员和智能体的近期操作记录。"),
    "security": ("账号安全", "更新管理员密码和登录凭据。"),
}
_login_failures: dict[str, list[float]] = {}


@lru_cache
def session_signer() -> AdminSessionSigner:
    settings = get_settings()
    if not settings.admin_session_secret:
        raise RuntimeError("WECHAT_BRIDGE_ADMIN_SESSION_SECRET is required")
    return AdminSessionSigner(settings.admin_session_secret, settings.admin_session_ttl_seconds)


def base_path() -> str:
    return get_settings().normalized_root_path


def login_url() -> str:
    return f"{base_path()}/admin/login"


def admin_url() -> str:
    return f"{base_path()}/admin"


def agent_api_base_url(request: Request) -> str:
    configured = get_settings().bridge_base_url
    if configured:
        return configured.rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",", 1)[0].strip()
    forwarded_host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc)).split(",", 1)[0].strip()
    return f"{forwarded_proto}://{forwarded_host}{base_path()}".rstrip("/")


def remote_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def _session(request: Request) -> tuple[Optional[AdminSession], Optional[AdminRecord]]:
    session = session_signer().verify(request.cookies.get(SESSION_COOKIE))
    if not session:
        return None, None
    admin = get_repository().get_admin(session.admin_id)
    if not admin or admin.session_version != session.session_version:
        return None, None
    return session, admin


def require_admin(request: Request) -> tuple[AdminSession, AdminRecord]:
    session, admin = _session(request)
    if not session or not admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Administrator login required")
    return session, admin


def require_csrf(request: Request, session: AdminSession, submitted: Any) -> None:
    if not isinstance(submitted, str) or submitted != session.csrf_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def set_session_cookie(response: RedirectResponse, token: str, request: Request) -> None:
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=get_settings().admin_session_ttl_seconds,
        httponly=True,
        secure=forwarded_proto == "https",
        samesite="lax",
        path=(base_path() or "") + "/",
    )


def _friendly_wechat_error(exc: Exception) -> str:
    if isinstance(exc, WeChatAPIError):
        errcode = exc.payload.get("errcode", "unknown")
        errmsg = exc.payload.get("errmsg", str(exc))
        return f"微信连接失败（{errcode}）：{errmsg}"
    return f"微信连接失败：{str(exc)[:300]}"


def render_dashboard(
    request: Request,
    session: AdminSession,
    admin: AdminRecord,
    *,
    notice: Optional[str] = None,
    error: Optional[str] = None,
    new_key: Optional[str] = None,
    new_key_name: Optional[str] = None,
    initial_view: str = "accounts",
    open_modal: Optional[str] = None,
    selected_account_id: Optional[int] = None,
    account_keys_id: Optional[int] = None,
) -> HTMLResponse:
    repository = get_repository()
    if initial_view in {"overview", "keys"}:
        initial_view = "accounts"
    active_view = initial_view if initial_view in ADMIN_VIEWS else "accounts"
    accounts = repository.list_accounts()
    agent_keys = repository.list_agent_keys()
    keys_by_account = {account["id"]: [] for account in accounts}
    for agent_key in agent_keys:
        keys_by_account.setdefault(agent_key["account_id"], []).append(agent_key)
    account_keys_account = next(
        (account for account in accounts if account["id"] == account_keys_id),
        None,
    )
    valid_modals = {"account", "key"}
    valid_modals.update(f"edit-account-{account['id']}" for account in accounts)
    api_base_url = agent_api_base_url(request)
    response = templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "base_path": base_path(),
            "agent_api_base_url": api_base_url,
            "agent_api_doc_url": f"{api_base_url}/agent-guide",
            "session": session,
            "admin": admin,
            "accounts": accounts,
            "agent_keys": agent_keys,
            "keys_by_account": keys_by_account,
            "account_keys_account": account_keys_account,
            "audit_logs": repository.list_audit_logs(),
            "stats": repository.dashboard_stats(),
            "all_scopes": ALL_AGENT_SCOPES,
            "scope_labels": {
                "content:read": "读取草稿与文章",
                "content:write": "上传素材与写入草稿",
                "metrics:read": "读取运营数据",
                "publish": "提交发布",
            },
            "notice": notice,
            "error": error,
            "new_key": new_key,
            "new_key_name": new_key_name,
            "initial_view": active_view,
            "page_title": (
                f"{account_keys_account['display_name']} · API Key"
                if account_keys_account
                else ADMIN_VIEW_META[active_view][0]
            ),
            "page_description": ADMIN_VIEW_META[active_view][1],
            "open_modal": open_modal if open_modal in valid_modals else None,
            "selected_account_id": selected_account_id,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    session, admin = _session(request)
    if session and admin:
        return RedirectResponse(admin_url(), status_code=303)
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"base_path": base_path(), "error": request.query_params.get("error")},
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/login")
async def login(request: Request) -> Response:
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    ip = remote_ip(request)
    now = time.monotonic()
    attempts = [item for item in _login_failures.get(ip, []) if now - item < 900]
    _login_failures[ip] = attempts
    if len(attempts) >= 8:
        response = templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"base_path": base_path(), "error": "登录尝试过多，请 15 分钟后再试。"},
            status_code=429,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    repository = get_repository()
    admin = repository.authenticate_admin(username, password)
    if not admin:
        attempts.append(now)
        repository.record_audit(
            "admin", "admin.login", "denied", actor_id=username or None, remote_ip=ip
        )
        response = templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"base_path": base_path(), "error": "账号或密码不正确。"},
            status_code=401,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    _login_failures.pop(ip, None)
    token, _new_session = session_signer().create(admin.id, admin.username, admin.session_version)
    response = RedirectResponse(admin_url(), status_code=303)
    set_session_cookie(response, token, request)
    repository.record_audit(
        "admin", "admin.login", "success", actor_id=str(admin.id), remote_ip=ip
    )
    return response


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request) -> Response:
    session, admin = _session(request)
    if not session or not admin:
        return RedirectResponse(login_url(), status_code=303)
    return render_dashboard(
        request,
        session,
        admin,
        notice=request.query_params.get("notice"),
        error=request.query_params.get("error"),
        initial_view=request.query_params.get("view", "accounts"),
    )


@router.get("/accounts/{account_id}/keys", response_class=HTMLResponse)
async def account_keys_page(account_id: int, request: Request) -> Response:
    session, admin = _session(request)
    if not session or not admin:
        return RedirectResponse(login_url(), status_code=303)
    if not get_repository().get_account(account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    return render_dashboard(
        request,
        session,
        admin,
        notice=request.query_params.get("notice"),
        error=request.query_params.get("error"),
        initial_view="accounts",
        account_keys_id=account_id,
    )


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    session, admin = require_admin(request)
    form = await request.form()
    require_csrf(request, session, form.get("_csrf"))
    response = RedirectResponse(login_url(), status_code=303)
    response.delete_cookie(SESSION_COOKIE, path=(base_path() or "") + "/")
    get_repository().record_audit(
        "admin", "admin.logout", "success", actor_id=str(admin.id), remote_ip=remote_ip(request)
    )
    return response


@router.post("/accounts")
async def create_account(request: Request) -> Response:
    session, admin = require_admin(request)
    form = await request.form()
    require_csrf(request, session, form.get("_csrf"))
    slug = str(form.get("slug", "")).strip().lower()
    display_name = str(form.get("display_name", "")).strip()
    app_id = str(form.get("app_id", "")).strip()
    app_secret = str(form.get("app_secret", "")).strip()
    callback_token = str(form.get("callback_token", "")).strip() or None
    if not SLUG_PATTERN.fullmatch(slug):
        return render_dashboard(request, session, admin, error="账号标识只能包含 2–32 位小写字母、数字或连字符。", initial_view="accounts", open_modal="account")
    if not display_name or not app_id or not app_secret:
        return render_dashboard(request, session, admin, error="公众号名称、AppID 和 AppSecret 均为必填项。", initial_view="accounts", open_modal="account")

    try:
        client = WeChatClient(WeChatCredentials(app_id, app_secret, get_settings().wechat_api_base))
        await client.get_access_token(force_refresh=False)
    except Exception as exc:
        return render_dashboard(request, session, admin, error=_friendly_wechat_error(exc), initial_view="accounts", open_modal="account")

    repository = get_repository()
    try:
        account_id = repository.create_account(slug, display_name, app_id, app_secret, callback_token)
    except sqlite3.IntegrityError:
        return render_dashboard(request, session, admin, error="账号标识或 AppID 已存在。", initial_view="accounts", open_modal="account")
    repository.record_audit(
        "admin",
        "account.create",
        "success",
        actor_id=str(admin.id),
        account_id=account_id,
        target=slug,
        remote_ip=remote_ip(request),
    )
    return RedirectResponse(f"{admin_url()}?view=accounts&notice=公众号已添加并通过微信连接验证", status_code=303)


@router.post("/accounts/{account_id}/update")
async def update_account(account_id: int, request: Request) -> Response:
    session, admin = require_admin(request)
    form = await request.form()
    require_csrf(request, session, form.get("_csrf"))
    repository = get_repository()
    account = repository.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    display_name = str(form.get("display_name", "")).strip()
    app_id = str(form.get("app_id", "")).strip()
    app_secret = str(form.get("app_secret", "")).strip() or None
    replace_callback = form.get("replace_callback") == "1"
    callback_token = str(form.get("callback_token", "")).strip() or None
    if not display_name or not app_id:
        return render_dashboard(
            request,
            session,
            admin,
            error="公众号名称和 AppID 不能为空。",
            initial_view="accounts",
            open_modal=f"edit-account-{account_id}",
        )
    _old_app_id, old_secret, _old_callback = repository.decrypt_account_credentials(account)
    candidate_secret = app_secret or old_secret
    try:
        client = WeChatClient(WeChatCredentials(app_id, candidate_secret, get_settings().wechat_api_base))
        await client.get_access_token(force_refresh=True)
        repository.update_account(
            account_id,
            display_name,
            app_id,
            app_secret,
            callback_token,
            replace_callback,
        )
        get_client_registry().invalidate(account_id)
    except sqlite3.IntegrityError:
        return render_dashboard(
            request,
            session,
            admin,
            error="该 AppID 已绑定其他公众号。",
            initial_view="accounts",
            open_modal=f"edit-account-{account_id}",
        )
    except Exception as exc:
        return render_dashboard(
            request,
            session,
            admin,
            error=_friendly_wechat_error(exc),
            initial_view="accounts",
            open_modal=f"edit-account-{account_id}",
        )
    repository.record_audit(
        "admin", "account.update", "success", actor_id=str(admin.id), account_id=account_id,
        target=account.slug, remote_ip=remote_ip(request)
    )
    return RedirectResponse(f"{admin_url()}?view=accounts&notice=公众号配置已更新并验证", status_code=303)


@router.post("/accounts/{account_id}/test")
async def test_account(account_id: int, request: Request) -> RedirectResponse:
    session, admin = require_admin(request)
    form = await request.form()
    require_csrf(request, session, form.get("_csrf"))
    repository = get_repository()
    account = repository.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        client = get_client_registry().get(account)
        await client.get_access_token(force_refresh=True)
    except Exception as exc:
        message = _friendly_wechat_error(exc)
        repository.mark_account_test(account_id, message)
        repository.record_audit(
            "admin", "account.test", "failed", actor_id=str(admin.id), account_id=account_id,
            target=account.slug, remote_ip=remote_ip(request)
        )
        return RedirectResponse(f"{admin_url()}?view=accounts&error={message}", status_code=303)
    repository.mark_account_test(account_id, None)
    repository.record_audit(
        "admin", "account.test", "success", actor_id=str(admin.id), account_id=account_id,
        target=account.slug, remote_ip=remote_ip(request)
    )
    return RedirectResponse(f"{admin_url()}?view=accounts&notice=微信连接测试通过", status_code=303)


@router.post("/accounts/{account_id}/toggle")
async def toggle_account(account_id: int, request: Request) -> RedirectResponse:
    session, admin = require_admin(request)
    form = await request.form()
    require_csrf(request, session, form.get("_csrf"))
    repository = get_repository()
    account = repository.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    enabled = form.get("enabled") == "1"
    repository.set_account_enabled(account_id, enabled)
    get_client_registry().invalidate(account_id)
    repository.record_audit(
        "admin", "account.enable" if enabled else "account.disable", "success",
        actor_id=str(admin.id), account_id=account_id, target=account.slug,
        remote_ip=remote_ip(request)
    )
    return RedirectResponse(f"{admin_url()}?view=accounts&notice=公众号状态已更新", status_code=303)


@router.post("/keys")
@router.post("/accounts/{account_id}/keys")
async def create_key(request: Request, account_id: Optional[int] = None) -> HTMLResponse:
    session, admin = require_admin(request)
    form = await request.form()
    require_csrf(request, session, form.get("_csrf"))
    if account_id is None:
        try:
            account_id = int(str(form.get("account_id", "")))
        except ValueError:
            return render_dashboard(
                request,
                session,
                admin,
                error="请选择要绑定的公众号。",
                initial_view="accounts",
                open_modal="key",
            )
    name = str(form.get("name", "")).strip()
    scopes = [str(value) for value in form.getlist("scopes")]
    if not name:
        return render_dashboard(
            request,
            session,
            admin,
            error="请填写智能体或 Key 名称。",
            initial_view="accounts",
            open_modal="key",
            selected_account_id=account_id,
            account_keys_id=account_id,
        )
    repository = get_repository()
    account = repository.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        token, prefix = repository.create_agent_key(account_id, name, scopes)
    except ValueError as exc:
        return render_dashboard(
            request,
            session,
            admin,
            error=str(exc),
            initial_view="accounts",
            open_modal="key",
            selected_account_id=account_id,
            account_keys_id=account_id,
        )
    repository.record_audit(
        "admin", "agent_key.create", "success", actor_id=str(admin.id), account_id=account_id,
        target=prefix, remote_ip=remote_ip(request)
    )
    return render_dashboard(
        request,
        session,
        admin,
        notice="智能体 API Key 已生成",
        new_key=token,
        new_key_name=f"{account.display_name} / {name}",
        initial_view="accounts",
        account_keys_id=account_id,
    )


@router.post("/keys/{key_id}/revoke")
async def revoke_key(key_id: int, request: Request) -> RedirectResponse:
    session, admin = require_admin(request)
    form = await request.form()
    require_csrf(request, session, form.get("_csrf"))
    try:
        account_id = int(str(form.get("account_id", "")))
    except ValueError:
        account_id = 0
    repository = get_repository()
    repository.revoke_agent_key(key_id)
    repository.record_audit(
        "admin", "agent_key.revoke", "success", actor_id=str(admin.id),
        target=str(key_id), remote_ip=remote_ip(request)
    )
    if account_id and repository.get_account(account_id):
        return RedirectResponse(
            f"{admin_url()}/accounts/{account_id}/keys?notice=API Key 已撤销",
            status_code=303,
        )
    return RedirectResponse(f"{admin_url()}?view=accounts&notice=API Key 已撤销", status_code=303)


@router.post("/keys/{key_id}/reveal")
async def reveal_key(key_id: int, request: Request) -> JSONResponse:
    session, admin = require_admin(request)
    form = await request.form()
    require_csrf(request, session, form.get("_csrf"))
    repository = get_repository()
    revealed = repository.reveal_agent_key(key_id)
    if not revealed:
        response = JSONResponse(
            status_code=409,
            content={"detail": "这个 Key 创建于支持再次复制之前，请先重新生成。"},
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    token, account_id, key_prefix = revealed
    repository.record_audit(
        "admin",
        "agent_key.copy",
        "success",
        actor_id=str(admin.id),
        account_id=account_id,
        target=key_prefix,
        remote_ip=remote_ip(request),
    )
    response = JSONResponse(content={"key": token})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/keys/{key_id}/rotate")
async def rotate_key(key_id: int, request: Request) -> Response:
    session, admin = require_admin(request)
    form = await request.form()
    require_csrf(request, session, form.get("_csrf"))
    repository = get_repository()
    try:
        token, prefix, account_id, name = repository.rotate_agent_key(key_id)
    except ValueError as exc:
        try:
            account_id = int(str(form.get("account_id", "")))
        except ValueError:
            account_id = 0
        if account_id and repository.get_account(account_id):
            return RedirectResponse(
                f"{admin_url()}/accounts/{account_id}/keys?error={str(exc)}",
                status_code=303,
            )
        return render_dashboard(
            request,
            session,
            admin,
            error=str(exc),
            initial_view="accounts",
        )
    account = repository.get_account(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    repository.record_audit(
        "admin",
        "agent_key.rotate",
        "success",
        actor_id=str(admin.id),
        account_id=account_id,
        target=prefix,
        remote_ip=remote_ip(request),
    )
    return render_dashboard(
        request,
        session,
        admin,
        notice="API Key 已重新生成，旧 Key 已立即失效",
        new_key=token,
        new_key_name=f"{account.display_name} / {name}",
        initial_view="accounts",
        account_keys_id=account_id,
    )


@router.post("/password")
async def change_password(request: Request) -> Response:
    session, admin = require_admin(request)
    form = await request.form()
    require_csrf(request, session, form.get("_csrf"))
    current_password = str(form.get("current_password", ""))
    new_password = str(form.get("new_password", ""))
    confirm_password = str(form.get("confirm_password", ""))
    if new_password != confirm_password:
        return render_dashboard(request, session, admin, error="两次输入的新密码不一致。", initial_view="security")
    if len(new_password) < 12:
        return render_dashboard(request, session, admin, error="新密码至少需要 12 个字符。", initial_view="security")
    repository = get_repository()
    try:
        updated = repository.change_admin_password(admin.id, current_password, new_password)
    except ValueError as exc:
        return render_dashboard(request, session, admin, error=str(exc), initial_view="security")
    token, _new_session = session_signer().create(updated.id, updated.username, updated.session_version)
    response = RedirectResponse(f"{admin_url()}?view=security&notice=管理员密码已更新", status_code=303)
    set_session_cookie(response, token, request)
    repository.record_audit(
        "admin", "admin.password.change", "success", actor_id=str(admin.id), remote_ip=remote_ip(request)
    )
    return response
