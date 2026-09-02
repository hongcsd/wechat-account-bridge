from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class BridgeResponse(BaseModel):
    ok: bool = True


class UploadResponse(BridgeResponse):
    sha256: str
    filename: str
    size: int
    wechat: dict[str, Any]


class DraftCreateRequest(BaseModel):
    articles: list[dict[str, Any]] = Field(..., min_length=1, max_length=8)
    verify_after_create: bool = True


class DraftCreateResponse(BridgeResponse):
    media_id: Optional[str] = None
    wechat: dict[str, Any]
    verified: bool = False
    draft: Optional[dict[str, Any]] = None


class DraftGetRequest(BaseModel):
    media_id: str = Field(..., min_length=1)


class DraftGetResponse(BridgeResponse):
    media_id: str
    wechat: dict[str, Any]


class DraftBatchGetRequest(BaseModel):
    offset: int = Field(default=0, ge=0)
    count: int = Field(default=20, ge=1, le=20)
    no_content: int = Field(default=1, ge=0, le=1)


class DraftBatchGetResponse(BridgeResponse):
    wechat: dict[str, Any]


class FreePublishBatchGetRequest(BaseModel):
    offset: int = Field(default=0, ge=0)
    count: int = Field(default=20, ge=1, le=20)
    no_content: int = Field(default=1, ge=0, le=1)


class FreePublishBatchGetResponse(BridgeResponse):
    wechat: dict[str, Any]


class FreePublishGetArticleRequest(BaseModel):
    article_id: str = Field(..., min_length=1)


class FreePublishGetArticleResponse(BridgeResponse):
    article_id: str
    wechat: dict[str, Any]


class ArticleTotalDetailRequest(BaseModel):
    begin_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")


class ArticleTotalDetailResponse(BridgeResponse):
    metrics: dict[str, Any]


class PublishRequest(BaseModel):
    media_id: str = Field(..., min_length=1)


class PublishResponse(BridgeResponse):
    publish_id: Optional[str] = None
    msg_data_id: Optional[str] = None
    wechat: dict[str, Any]


class PublishStatusRequest(BaseModel):
    publish_id: str = Field(..., min_length=1)


class PublishStatusResponse(BridgeResponse):
    publish_id: Optional[str] = None
    publish_status: Optional[int] = None
    article_id: Optional[str] = None
    wechat: dict[str, Any]


class TokenStatusResponse(BridgeResponse):
    cached: bool
    expires_in_seconds: Optional[int]


class TokenRefreshRequest(BaseModel):
    force_refresh: bool = False


class TokenRefreshResponse(BridgeResponse):
    cached: bool
    expires_in_seconds: Optional[int]


MaterialType = Literal["image", "thumb"]
