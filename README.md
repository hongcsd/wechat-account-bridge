# wechat-account-bridge

面向微信公众号运营自动化的服务端 API Bridge。它把公众号凭据留在受控服务器，为人类管理员或受授权的智能体提供统一、隔离、可审计的操作接口。

> 本仓库不包含任何生产服务器信息、真实域名、公众号标识、API Key、AppSecret、回调 Token、管理员凭据、数据库、日志或运行截图。

## 解决什么问题

公众号自动化常把 `AppID`、`AppSecret` 和 access token 分散在本地脚本、CI 环境或多个智能体中。这会带来凭据扩散、权限过大、无法追溯操作以及多公众号混用的风险。

本项目将这些能力收敛为一个服务端边界：

- 公众号凭据仅保存在 Bridge 所在服务器，并以加密方式持久化；
- 每个调用方使用独立 API Key，并且只绑定一个公众号；
- API Key 可按读取内容、写入内容、读取数据与发布四类 scope 授权；
- 管理后台可创建账号、轮换或撤销 Key，并查看不包含密钥明文的审计记录；
- 智能体通过统一 HTTP API 管理图片、草稿、发布任务及文章数据。

## 工作原理

```text
运营人员 / 智能体
        │  Bearer API Key（按账号和 scope 隔离）
        ▼
wechat-account-bridge
        │  选择对应公众号、验证权限、写入审计记录
        ▼
加密凭据存储 + 微信公众平台 API
```

调用方永远不需要收到公众号 `AppSecret` 或 access token。管理员在 `/admin` 中配置公众号；Bridge 使用该账号的凭据向微信请求临时 access token，并在服务端缓存。

## 功能

- 多公众号账号与凭据隔离
- 管理员登录、CSRF 防护与安全 Cookie
- 一次展示、可撤销、带 scope 的智能体 API Key
- 图片和永久素材上传、草稿创建/读取、发布和状态查询
- 已发布文章读取与文章数据指标查询
- 临时上传文件自动清理、健康检查和审计日志

## 快速开始

### 1. 准备配置

```bash
cp .env.example .env
```

编辑 `.env`，填写你自己的公众号凭据和以下服务端密钥。每个值都应独立随机生成；例如：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

至少设置：

- `WECHAT_BRIDGE_CREDENTIAL_ENCRYPTION_KEY`
- `WECHAT_BRIDGE_API_KEY_PEPPER`
- `WECHAT_BRIDGE_ADMIN_SESSION_SECRET`
- `WECHAT_BRIDGE_ADMIN_INITIAL_PASSWORD`

`WECHAT_BRIDGE_ADMIN_INITIAL_PASSWORD` 仅用于初始化首个管理员；管理员创建完成后请从 `.env` 删除该项。不要提交 `.env`。

### 2. 启动服务

```bash
docker compose up -d --build
```

默认 Compose 配置仅监听本机回环地址。生产环境应通过你自己的 HTTPS 反向代理或私有网络访问；不要把服务端口和管理后台直接暴露到互联网。

### 3. 配置公众号和调用方

1. 打开 `http://127.0.0.1:8080/admin` 并使用初始化管理员登录。
2. 添加公众号，系统会验证其 `AppID` 与 `AppSecret`。
3. 为调用方创建独立 API Key，并按最小权限选择 scope。
4. 将 Key 存入调用方的安全凭据存储，而不是脚本、日志或文章正文。

## API 示例

调用 API 时将 Key 放入请求头，不要写进 URL：

```bash
export WECHAT_BRIDGE_BASE_URL="https://bridge.example.test"
export WECHAT_BRIDGE_API_TOKEN="your-api-key"

curl -sS "$WECHAT_BRIDGE_BASE_URL/wechat/token/status" \
  -H "Authorization: Bearer $WECHAT_BRIDGE_API_TOKEN"
```

常用权限：

| Scope | 用途 |
| --- | --- |
| `content:read` | 读取草稿、已发布文章与 token 状态 |
| `content:write` | 上传素材、创建草稿、刷新 token、清理临时文件 |
| `metrics:read` | 读取文章数据指标 |
| `publish` | 提交发布并读取发布状态 |

完整的智能体接口说明由运行中的服务在 `/agent-guide` 提供；在源码中可阅读 [app/agent-guide.md](app/agent-guide.md)。

## 开发与测试

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## 部署与安全说明

- 所有生产密钥和数据库都必须留在服务器或专用密钥管理系统，私有仓库也不例外。
- 使用 HTTPS 反向代理时，仅接受来自受信任代理的转发头；配置明确的主机名白名单。
- 默认关闭 OpenAPI 文档。只在受控的短暂调试窗口设置 `WECHAT_BRIDGE_ENABLE_DOCS=true`。
- 当怀疑 Key、密码或凭据出现在日志、截图或提交历史中时，立即轮换它；删除文件无法让泄露的凭据重新安全。

漏洞报告方式见 [SECURITY.md](SECURITY.md)。

## 许可证

许可证将在维护者确认后添加。在此之前，请勿假定自己拥有再发布或商业使用的授权。
