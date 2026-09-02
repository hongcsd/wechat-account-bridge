# 微信公众号 Bridge：智能体接口使用说明

本服务让智能体通过统一 API 操作一个指定的微信公众号。每个 API Key 只绑定一个公众号，因此请求里不需要传 `account_id`；服务会根据 Key 自动选择对应公众号。

## 接入配置

```dotenv
WECHAT_BRIDGE_BASE_URL=https://bridge.example.test
WECHAT_BRIDGE_API_TOKEN=
WECHAT_BRIDGE_API_DOC_URL=https://bridge.example.test/agent-guide
```

除健康检查和本文档外，请求都使用 Bearer Token：

```http
Authorization: Bearer <WECHAT_BRIDGE_API_TOKEN>
Content-Type: application/json
```

通用 `curl` 形式：

```bash
curl -sS "${WECHAT_BRIDGE_BASE_URL}/wechat/token/status" \
  -H "Authorization: Bearer ${WECHAT_BRIDGE_API_TOKEN}"
```

## 智能体必须遵守

- 不要在消息、日志或文章中输出完整 API Key。
- 不要直接调用微信开放接口；统一调用本 Bridge。
- 默认只创建并检查草稿。只有用户明确要求发布时，才调用发布接口。
- 创建草稿或提交发布发生超时时，不要盲目重试；先查询草稿列表或发布状态，避免重复操作。
- 收到 `403` 权限错误时停止请求，并告知用户需要为当前 Key 增加对应权限。

## 权限

| 权限 | 可执行操作 |
| --- | --- |
| `content:read` | 查询 Token 状态、读取草稿与已发布文章 |
| `content:write` | 刷新 Token、上传图片或素材、创建草稿、清理临时文件 |
| `metrics:read` | 查询文章运营数据 |
| `publish` | 提交发布并查询发布状态 |

一个 Key 可能只拥有部分权限。接口返回的 `403` 会说明缺少哪项权限。

## 接口速查

| 方法 | 路径 | 权限 | 用途 |
| --- | --- | --- | --- |
| `GET` | `/healthz` | 无 | 存活检查 |
| `GET` | `/readyz` | 无 | 就绪检查 |
| `GET` | `/agent-guide` | 无 | 本接口说明 |
| `GET` | `/wechat/token/status` | `content:read` | 查询缓存 Token 状态，不返回微信 Token 明文 |
| `POST` | `/wechat/token/refresh` | `content:write` | 获取或刷新微信 Token |
| `POST` | `/wechat/media/article-image` | `content:write` | 上传正文图片，返回可用于正文的微信图片 URL |
| `POST` | `/wechat/material` | `content:write` | 上传永久图片或封面素材，返回 `media_id` |
| `POST` | `/wechat/drafts` | `content:write` | 创建图文草稿，默认创建后再读取校验 |
| `POST` | `/wechat/drafts/get` | `content:read` | 按 `media_id` 读取草稿 |
| `POST` | `/wechat/drafts/batchget` | `content:read` | 分页读取草稿列表 |
| `POST` | `/wechat/freepublish/batchget` | `content:read` | 分页读取已发布文章 |
| `POST` | `/wechat/freepublish/getarticle` | `content:read` | 按 `article_id` 读取已发布文章 |
| `POST` | `/wechat/metrics/article-total-detail` | `metrics:read` | 查询文章数据，日期跨度最多 30 天 |
| `POST` | `/wechat/publish` | `publish` | 提交草稿发布 |
| `POST` | `/wechat/publish/status` | `publish` | 查询发布任务状态 |
| `POST` | `/maintenance/cleanup-temp` | `content:write` | 清理过期的临时上传文件 |

## 常用请求

### 查询 Token 状态

```bash
curl -sS "${WECHAT_BRIDGE_BASE_URL}/wechat/token/status" \
  -H "Authorization: Bearer ${WECHAT_BRIDGE_API_TOKEN}"
```

刷新 Token：

```bash
curl -sS -X POST "${WECHAT_BRIDGE_BASE_URL}/wechat/token/refresh" \
  -H "Authorization: Bearer ${WECHAT_BRIDGE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"force_refresh":false}'
```

### 上传正文图片

正文图片最大 1 MB。成功后使用返回数据中的微信图片 URL 填入文章正文。

```bash
curl -sS -X POST "${WECHAT_BRIDGE_BASE_URL}/wechat/media/article-image" \
  -H "Authorization: Bearer ${WECHAT_BRIDGE_API_TOKEN}" \
  -F "media=@cover.jpg"
```

### 上传封面或永久图片

`material_type` 可为 `thumb` 或 `image`。`thumb` 仅支持不超过 64 KB 的 JPG；`image` 最大 10 MB。

```bash
curl -sS -X POST "${WECHAT_BRIDGE_BASE_URL}/wechat/material" \
  -H "Authorization: Bearer ${WECHAT_BRIDGE_API_TOKEN}" \
  -F "material_type=thumb" \
  -F "media=@cover.jpg"
```

### 创建草稿

一次可提交 1–8 篇文章。`articles` 内的字段遵循微信公众号草稿接口，例如 `title`、`author`、`digest`、`content`、`content_source_url`、`thumb_media_id`、`need_open_comment` 和 `only_fans_can_comment`。

```bash
curl -sS -X POST "${WECHAT_BRIDGE_BASE_URL}/wechat/drafts" \
  -H "Authorization: Bearer ${WECHAT_BRIDGE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "articles": [{
      "title": "文章标题",
      "author": "作者",
      "digest": "文章摘要",
      "content": "<p>正文 HTML</p>",
      "content_source_url": "",
      "thumb_media_id": "封面素材 media_id",
      "need_open_comment": 0,
      "only_fans_can_comment": 0
    }],
    "verify_after_create": true
  }'
```

成功响应中的 `media_id` 是草稿标识；`verified: true` 表示服务已在创建后重新读取并确认草稿存在。

### 读取草稿

```bash
curl -sS -X POST "${WECHAT_BRIDGE_BASE_URL}/wechat/drafts/get" \
  -H "Authorization: Bearer ${WECHAT_BRIDGE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"media_id":"草稿 media_id"}'
```

读取最近草稿：

```bash
curl -sS -X POST "${WECHAT_BRIDGE_BASE_URL}/wechat/drafts/batchget" \
  -H "Authorization: Bearer ${WECHAT_BRIDGE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"offset":0,"count":20,"no_content":1}'
```

`count` 范围为 1–20；`no_content` 为 `1` 时不返回正文，可降低响应大小。

### 读取已发布文章

列表：

```bash
curl -sS -X POST "${WECHAT_BRIDGE_BASE_URL}/wechat/freepublish/batchget" \
  -H "Authorization: Bearer ${WECHAT_BRIDGE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"offset":0,"count":20,"no_content":1}'
```

单篇：

```bash
curl -sS -X POST "${WECHAT_BRIDGE_BASE_URL}/wechat/freepublish/getarticle" \
  -H "Authorization: Bearer ${WECHAT_BRIDGE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"article_id":"已发布文章 article_id"}'
```

### 查询文章运营数据

日期格式为 `YYYY-MM-DD`，起止日期跨度最多 30 天。

```bash
curl -sS -X POST "${WECHAT_BRIDGE_BASE_URL}/wechat/metrics/article-total-detail" \
  -H "Authorization: Bearer ${WECHAT_BRIDGE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"begin_date":"2026-08-01","end_date":"2026-08-07"}'
```

### 发布草稿

仅在用户明确授权发布后执行：

```bash
curl -sS -X POST "${WECHAT_BRIDGE_BASE_URL}/wechat/publish" \
  -H "Authorization: Bearer ${WECHAT_BRIDGE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"media_id":"草稿 media_id"}'
```

保存响应中的 `publish_id`，然后查询状态：

```bash
curl -sS -X POST "${WECHAT_BRIDGE_BASE_URL}/wechat/publish/status" \
  -H "Authorization: Bearer ${WECHAT_BRIDGE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"publish_id":"发布任务 publish_id"}'
```

## 响应与错误

成功响应通常包含 `"ok": true`，并在 `wechat` 字段保留微信接口返回值。每个响应都有 `X-Request-ID`，排查问题时可提供该值。

| HTTP 状态 | 含义 | 智能体处理方式 |
| --- | --- | --- |
| `401` | 缺少 Bearer Token | 检查是否传入了 `WECHAT_BRIDGE_API_TOKEN` |
| `403` | Key 无效、已撤销，或缺少权限 | 停止重试并提示用户检查 Key 或权限 |
| `413` | 上传文件超出限制 | 压缩或更换文件后再试 |
| `422` | 请求字段、格式或日期范围不合法 | 按响应中的 `detail` 修正参数 |
| `502` 或其他微信错误 | 微信接口拒绝或暂时异常 | 阅读 `detail.wechat`；不要对创建或发布请求盲目重试 |

## 推荐工作流

1. 调用 `/wechat/token/status` 确认凭证可用。
2. 上传正文图片和封面素材。
3. 调用 `/wechat/drafts` 创建草稿，并确认 `verified`。
4. 把草稿结果交给用户检查。
5. 仅在用户明确同意后调用 `/wechat/publish`。
6. 用 `/wechat/publish/status` 查询发布结果并回报 `article_id`。
