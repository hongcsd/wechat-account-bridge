import os
import re
import secrets
from pathlib import Path

os.environ.setdefault("BRIDGE_API_TOKEN", "test-token-1234567890")
os.environ.setdefault("WECHAT_APP_ID", "wx-test")
os.environ.setdefault("WECHAT_APP_SECRET", "secret-test")
os.environ["WECHAT_BRIDGE_BASE_URL"] = "https://bridge.example.test"
os.environ.setdefault("WECHAT_BRIDGE_TEMP_DIR", "/tmp/wechat-account-bridge-tests")
os.environ.setdefault(
    "WECHAT_BRIDGE_DATABASE_PATH",
    f"/tmp/wechat-account-bridge-tests-{os.getpid()}-{secrets.token_hex(4)}.sqlite3",
)
os.environ.setdefault("WECHAT_BRIDGE_CREDENTIAL_ENCRYPTION_KEY", "test-encryption-key-1234567890-abcdef")
os.environ.setdefault("WECHAT_BRIDGE_API_KEY_PEPPER", "test-api-key-pepper-1234567890-abcdef")
os.environ.setdefault("WECHAT_BRIDGE_ADMIN_SESSION_SECRET", "test-admin-session-1234567890-abcdef")
os.environ.setdefault("WECHAT_BRIDGE_ADMIN_INITIAL_USERNAME", "admin")
os.environ.setdefault("WECHAT_BRIDGE_ADMIN_INITIAL_PASSWORD", "AdminPassword!123")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import (  # noqa: E402
    WECHAT_ARTICLE_IMAGE_MAX_BYTES,
    WECHAT_THUMB_MAX_BYTES,
    app,
    get_wechat_client,
)
from app.services import get_repository  # noqa: E402


class FakeWeChatClient:
    async def get_access_token(self, force_refresh=False):
        return "ACCESS_TOKEN"

    def token_status(self):
        class Cache:
            def valid(self):
                return True

            def expires_in_seconds(self):
                return 3600

        return Cache()

    async def upload_article_image(self, file_path: Path, filename: str, content_type: str):
        assert file_path.exists()
        return {"errcode": 0, "errmsg": "ok", "url": "https://mmbiz.qpic.cn/test.jpg"}

    async def upload_permanent_material(self, file_path: Path, filename: str, content_type: str, material_type: str):
        assert file_path.exists()
        return {"errcode": 0, "errmsg": "ok", "media_id": "MEDIA_ID", "url": "https://mmbiz.qpic.cn/cover.jpg"}

    async def create_draft(self, articles):
        return {"errcode": 0, "errmsg": "ok", "media_id": "DRAFT_MEDIA_ID", "articles": articles}

    async def get_draft(self, media_id: str):
        return {"errcode": 0, "errmsg": "ok", "news_item": [{"title": "Hello"}]}

    async def batch_get_drafts(self, offset: int = 0, count: int = 20, no_content: int = 1):
        return {
            "errcode": 0,
            "errmsg": "ok",
            "total_count": 1,
            "item_count": 1,
            "item": [{"media_id": "DRAFT_MEDIA_ID", "content": {"news_item": [{"title": "Hello"}]}}],
        }

    async def batch_get_freepublish(self, offset: int = 0, count: int = 20, no_content: int = 1):
        return {
            "errcode": 0,
            "errmsg": "ok",
            "total_count": 1,
            "item_count": 1,
            "item": [{"article_id": "ARTICLE_ID", "content": {"news_item": [{"title": "Published"}]}}],
        }

    async def get_freepublish_article(self, article_id: str):
        return {"errcode": 0, "errmsg": "ok", "article_id": article_id, "news_item": [{"title": "Published"}]}

    async def fetch_article_total_detail(self, begin_date: str, end_date: str):
        return {
            "ok": True,
            "partial_ok": False,
            "begin_date": begin_date,
            "end_date": end_date,
            "article_count": 1,
            "articles": [{"title": "Published", "reads": 100, "shares": 3}],
            "failures": [],
            "raw_daily": [],
        }

    async def publish_draft(self, media_id: str):
        return {"errcode": 0, "errmsg": "ok", "publish_id": "PUBLISH_ID", "msg_data_id": "MSG_DATA_ID"}

    async def get_publish_status(self, publish_id: str):
        return {"errcode": 0, "errmsg": "ok", "publish_id": publish_id, "publish_status": 1}


def client():
    app.dependency_overrides[get_wechat_client] = lambda: FakeWeChatClient()
    return TestClient(app)


def auth_headers():
    return {"Authorization": "Bearer test-token-1234567890"}


def test_health_is_public():
    response = client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_auth_required():
    response = client().post("/wechat/drafts", json={"articles": [{"title": "x"}]})
    assert response.status_code == 401


def test_create_draft():
    response = client().post(
        "/wechat/drafts",
        headers=auth_headers(),
        json={"articles": [{"title": "Hello", "content": "<p>World</p>", "thumb_media_id": "MEDIA_ID"}]},
    )
    assert response.status_code == 200
    assert response.json()["media_id"] == "DRAFT_MEDIA_ID"
    assert response.json()["verified"] is True


def test_get_draft():
    response = client().post(
        "/wechat/drafts/get",
        headers=auth_headers(),
        json={"media_id": "DRAFT_MEDIA_ID"},
    )
    assert response.status_code == 200
    assert response.json()["wechat"]["news_item"][0]["title"] == "Hello"


def test_batch_get_drafts():
    response = client().post(
        "/wechat/drafts/batchget",
        headers=auth_headers(),
        json={"offset": 0, "count": 5, "no_content": 1},
    )
    assert response.status_code == 200
    assert response.json()["wechat"]["item"][0]["media_id"] == "DRAFT_MEDIA_ID"


def test_batch_get_freepublish():
    response = client().post(
        "/wechat/freepublish/batchget",
        headers=auth_headers(),
        json={"offset": 0, "count": 5, "no_content": 1},
    )
    assert response.status_code == 200
    assert response.json()["wechat"]["item"][0]["article_id"] == "ARTICLE_ID"


def test_get_freepublish_article():
    response = client().post(
        "/wechat/freepublish/getarticle",
        headers=auth_headers(),
        json={"article_id": "ARTICLE_ID"},
    )
    assert response.status_code == 200
    assert response.json()["article_id"] == "ARTICLE_ID"
    assert response.json()["wechat"]["news_item"][0]["title"] == "Published"


def test_article_total_detail_metrics():
    response = client().post(
        "/wechat/metrics/article-total-detail",
        headers=auth_headers(),
        json={"begin_date": "2026-06-01", "end_date": "2026-06-01"},
    )
    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert metrics["ok"] is True
    assert metrics["article_count"] == 1
    assert metrics["articles"][0]["reads"] == 100


def test_token_refresh_does_not_return_token():
    response = client().post(
        "/wechat/token/refresh",
        headers=auth_headers(),
        json={"force_refresh": False},
    )
    assert response.status_code == 200
    assert "access_token" not in response.text
    assert response.json()["cached"] is True


def test_upload_article_image_deletes_temp_file(tmp_path, monkeypatch):
    monkeypatch.setenv("WECHAT_BRIDGE_TEMP_DIR", str(tmp_path))
    response = client().post(
        "/wechat/media/article-image",
        headers=auth_headers(),
        files={"media": ("image.jpg", b"fake-image", "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.json()["wechat"]["url"].startswith("https://mmbiz.qpic.cn/")
    assert list(tmp_path.iterdir()) == []


def test_upload_article_image_uses_wechat_uploadimg_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("WECHAT_BRIDGE_TEMP_DIR", str(tmp_path))
    response = client().post(
        "/wechat/media/article-image",
        headers=auth_headers(),
        files={"media": ("too-large.jpg", b"x" * (WECHAT_ARTICLE_IMAGE_MAX_BYTES + 1), "image/jpeg")},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == f"Upload exceeds {WECHAT_ARTICLE_IMAGE_MAX_BYTES} bytes"
    assert list(tmp_path.iterdir()) == []


def test_upload_permanent_image_allows_larger_wechat_material_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("WECHAT_BRIDGE_TEMP_DIR", str(tmp_path))
    response = client().post(
        "/wechat/material",
        headers=auth_headers(),
        data={"material_type": "image"},
        files={"media": ("material.jpg", b"x" * (WECHAT_ARTICLE_IMAGE_MAX_BYTES + 1), "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.json()["wechat"]["media_id"] == "MEDIA_ID"
    assert list(tmp_path.iterdir()) == []


def test_upload_thumb_uses_wechat_thumb_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("WECHAT_BRIDGE_TEMP_DIR", str(tmp_path))
    response = client().post(
        "/wechat/material",
        headers=auth_headers(),
        data={"material_type": "thumb"},
        files={"media": ("thumb.jpg", b"x" * (WECHAT_THUMB_MAX_BYTES + 1), "image/jpeg")},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == f"Upload exceeds {WECHAT_THUMB_MAX_BYTES} bytes"
    assert list(tmp_path.iterdir()) == []


def test_legacy_account_is_migrated_and_secret_is_encrypted():
    repository = get_repository()
    account = repository.get_account_by_slug("xiaobao")
    assert account is not None
    assert account.display_name == "心小宝"
    assert account.app_id == "wx-test"
    assert "secret-test" not in account.app_secret_encrypted
    assert repository.decrypt_account_credentials(account)[1] == "secret-test"
    assert repository.authenticate_agent("test-token-1234567890").account.slug == "xiaobao"
    legacy_key = next(key for key in repository.list_agent_keys(account.id) if key["name"] == "心小宝现有运营智能体")
    assert legacy_key["can_copy"] is True
    assert "token_encrypted" not in legacy_key
    assert "key_hash" not in legacy_key


def test_admin_login_and_dashboard_show_migrated_account():
    web = client()
    login_page = web.get("/admin/login")
    assert login_page.status_code == 200
    assert "管理员登录" in login_page.text
    response = web.post(
        "/admin/login",
        data={"username": "admin", "password": "AdminPassword!123"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "公众号管理" in response.text
    assert "心小宝" in response.text
    assert "secret-test" not in response.text
    assert 'data-view="accounts"' in response.text
    assert "view=overview" not in response.text
    assert "view=keys" not in response.text


def test_admin_accounts_view_uses_clear_actions_and_edit_dialog():
    web = client()
    web.post(
        "/admin/login",
        data={"username": "admin", "password": "AdminPassword!123"},
    )
    response = web.get("/admin?view=accounts")
    assert response.status_code == 200
    assert 'data-view="accounts"' in response.text
    assert "AppSecret" in response.text
    assert "secret-test" not in response.text
    assert 'id="account-dialog"' in response.text
    assert 'id="edit-account-1"' in response.text
    assert 'data-open-dialog="account-dialog"' in response.text
    assert 'data-open-dialog="edit-account-1"' in response.text
    assert 'href="/admin/accounts/1/keys"' in response.text
    assert "管理 API Key" in response.text
    assert "测试连接" in response.text
    assert ">停用</button>" in response.text
    assert "<details" not in response.text
    assert 'id="key-dialog"' not in response.text
    assert 'href="/admin?view=keys"' not in response.text


def test_account_key_secondary_page_is_scoped_to_one_account():
    web = client()
    web.post(
        "/admin/login",
        data={"username": "admin", "password": "AdminPassword!123"},
    )
    account = get_repository().get_account_by_slug("xiaobao")
    response = web.get(f"/admin/accounts/{account.id}/keys")
    assert response.status_code == 200
    assert 'data-view="accounts"' in response.text
    assert "返回公众号管理" in response.text
    assert "心小宝 · API Key" in response.text
    assert 'id="key-dialog"' in response.text
    assert f'action="/admin/accounts/{account.id}/keys"' in response.text
    assert "生成 API Key" in response.text
    assert "https://bridge.example.test" in response.text
    assert "复制 API 地址" in response.text
    assert "https://bridge.example.test/agent-guide" in response.text
    assert "复制文档地址" in response.text
    assert 'id="account-dialog"' not in response.text


def test_agent_guide_is_public_and_agent_readable():
    response = client().get("/agent-guide")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "WECHAT_BRIDGE_BASE_URL" in response.text
    assert "Authorization: Bearer" in response.text
    assert "/wechat/drafts" in response.text
    assert "connect-src 'self'" in response.headers["content-security-policy"]


def test_legacy_overview_and_keys_views_render_accounts_module():
    web = client()
    web.post(
        "/admin/login",
        data={"username": "admin", "password": "AdminPassword!123"},
    )
    for legacy_view in ("overview", "keys"):
        response = web.get(f"/admin?view={legacy_view}")
        assert response.status_code == 200
        assert 'data-view="accounts"' in response.text


def test_admin_mutation_rejects_invalid_csrf():
    web = client()
    web.post(
        "/admin/login",
        data={"username": "admin", "password": "AdminPassword!123"},
    )
    response = web.post(
        "/admin/accounts/1/toggle",
        data={"_csrf": "wrong", "enabled": "0"},
    )
    assert response.status_code == 403


def test_admin_can_generate_and_recopy_encrypted_agent_key():
    web = client()
    response = web.post(
        "/admin/login",
        data={"username": "admin", "password": "AdminPassword!123"},
        follow_redirects=True,
    )
    csrf = re.search(r'name="_csrf" value="([^"]+)"', response.text).group(1)
    account = get_repository().get_account_by_slug("xiaobao")
    created = web.post(
        f"/admin/accounts/{account.id}/keys",
        data={
            "_csrf": csrf,
            "name": "read-only-test-agent",
            "scopes": ["content:read", "metrics:read"],
        },
    )
    assert created.status_code == 200
    assert 'data-view="accounts"' in created.text
    assert "智能体接入配置" in created.text
    assert 'id="new-api-base">https://bridge.example.test</code>' in created.text
    assert 'id="new-api-doc">https://bridge.example.test/agent-guide</code>' in created.text
    assert "复制 Key" in created.text
    assert "复制文档地址" in created.text
    assert "复制全部配置" in created.text
    assert "WECHAT_BRIDGE_API_DOC_URL" in created.text
    match = re.search(r"wcb_live_[A-Za-z0-9]+_[A-Za-z0-9_-]+", created.text)
    assert match is not None
    token = match.group(0)
    principal = get_repository().authenticate_agent(token)
    assert principal is not None
    assert principal.account.slug == "xiaobao"
    assert principal.scopes == {"content:read", "metrics:read"}
    assert token not in Path(os.environ["WECHAT_BRIDGE_DATABASE_PATH"]).read_bytes().decode(
        "utf-8", errors="ignore"
    )
    stored_key = next(
        key for key in get_repository().list_agent_keys(account.id)
        if key["name"] == "read-only-test-agent"
    )
    assert stored_key["can_copy"] is True
    revealed = web.post(
        f"/admin/keys/{stored_key['id']}/reveal",
        data={"_csrf": csrf},
    )
    assert revealed.status_code == 200
    assert revealed.json() == {"key": token}
    assert revealed.headers["cache-control"] == "no-store"
    assert web.post(
        f"/admin/keys/{stored_key['id']}/reveal",
        data={"_csrf": "wrong"},
    ).status_code == 403


def test_historical_key_without_encrypted_copy_can_be_rotated():
    web = client()
    response = web.post(
        "/admin/login",
        data={"username": "admin", "password": "AdminPassword!123"},
        follow_redirects=True,
    )
    csrf = re.search(r'name="_csrf" value="([^"]+)"', response.text).group(1)
    repository = get_repository()
    account = repository.get_account_by_slug("xiaobao")
    old_token, _prefix = repository.create_agent_key(account.id, "historical-agent", ["content:read"])
    key = next(item for item in repository.list_agent_keys(account.id) if item["name"] == "historical-agent")
    with repository.connect() as connection:
        connection.execute(
            "UPDATE agent_keys SET token_encrypted = NULL WHERE id = ?",
            (key["id"],),
        )

    unavailable = web.post(
        f"/admin/keys/{key['id']}/reveal",
        data={"_csrf": csrf},
    )
    assert unavailable.status_code == 409
    assert "重新生成" in unavailable.json()["detail"]

    page = web.get(f"/admin/accounts/{account.id}/keys")
    assert "重新生成" in page.text
    rotated = web.post(
        f"/admin/keys/{key['id']}/rotate",
        data={"_csrf": csrf, "account_id": str(account.id)},
    )
    assert rotated.status_code == 200
    match = re.search(r"wcb_live_[A-Za-z0-9]+_[A-Za-z0-9_-]+", rotated.text)
    assert match is not None
    new_token = match.group(0)
    assert new_token != old_token
    assert repository.authenticate_agent(old_token) is None
    assert repository.authenticate_agent(new_token) is not None
    refreshed = next(item for item in repository.list_agent_keys(account.id) if item["id"] == key["id"])
    assert refreshed["can_copy"] is True


def test_limited_key_cannot_publish():
    repository = get_repository()
    account = repository.get_account_by_slug("xiaobao")
    token, _prefix = repository.create_agent_key(account.id, "read-only", ["content:read"])
    response = client().post(
        "/wechat/publish",
        headers={"Authorization": f"Bearer {token}"},
        json={"media_id": "DRAFT_MEDIA_ID"},
    )
    assert response.status_code == 403
    assert "publish" in response.json()["detail"]


def test_audit_log_retention_keeps_only_newest_records(monkeypatch):
    repository = get_repository()
    monkeypatch.setattr(repository.settings, "audit_log_retention_count", 5)
    for index in range(7):
        repository.record_audit(
            "admin",
            "retention.test",
            "success",
            target=f"retention-{index}",
        )

    logs = repository.list_audit_logs(limit=20)
    assert len(logs) == 5
    assert [log["target"] for log in logs] == [
        "retention-6",
        "retention-5",
        "retention-4",
        "retention-3",
        "retention-2",
    ]
