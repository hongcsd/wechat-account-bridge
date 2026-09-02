from __future__ import annotations

from functools import lru_cache

from .config import get_settings
from .database import AccountRecord, Repository
from .wechat import WeChatClient, WeChatCredentials


@lru_cache
def get_repository() -> Repository:
    repository = Repository(get_settings())
    repository.initialize()
    return repository


class WeChatClientRegistry:
    def __init__(self, repository: Repository):
        self.repository = repository
        self._clients: dict[int, tuple[str, WeChatClient]] = {}

    def get(self, account: AccountRecord) -> WeChatClient:
        cached = self._clients.get(account.id)
        if cached and cached[0] == account.updated_at:
            return cached[1]
        app_id, app_secret, _callback_token = self.repository.decrypt_account_credentials(account)
        client = WeChatClient(
            WeChatCredentials(
                app_id=app_id,
                app_secret=app_secret,
                api_base=self.repository.settings.wechat_api_base,
            )
        )
        self._clients[account.id] = (account.updated_at, client)
        return client

    def invalidate(self, account_id: int) -> None:
        self._clients.pop(account_id, None)


@lru_cache
def get_client_registry() -> WeChatClientRegistry:
    return WeChatClientRegistry(get_repository())
