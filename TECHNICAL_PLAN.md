# Rainier Link v0.2 技术方案

## 一、目标与状态

Rainier Link 在 Windows 上提供面向可信局域网的浏览器通信服务。v0.1 已完成文字消息、二维码配对和 HTTP 轮询；v0.2 将消息历史迁移到 SQLite，并增加图片传输与本地文件持久化。

本文件描述 v0.2 的实现边界、数据约束和验收标准。图片上传、图片历史展示、预览与删除属于本版本的实现目标；没有完成实现的接口或页面不得在发布说明中宣称已可用。

## 二、范围与非目标

### （一）本版本范围

1. 保留 v0.1 的文字消息、二维码配对和 HTTP 轮询。
2. 使用 SQLite 保存消息历史与图片元数据，服务重启后历史仍可读取。
3. 使用本地文件系统保存图片二进制，SQLite 不保存图片 BLOB。
4. 支持 JPG/JPEG、PNG、GIF、WEBP、HEIC、HEIF 的上传、保存与下载。
5. 上传使用流式读取、1 MB 分块、大小限制和 SHA-256 校验。
6. 图片以消息形式进入历史，可在线查看或下载；删除消息使用软删除语义。
7. 所有非健康检查请求继续使用当前启动生成的配对令牌鉴权。

### （二）非目标

不实现通用文件传输、视频、大文件分块协议、断点续传、云存储、账号和多用户体系、原生 iOS App、Windows 原生 GUI、互联网中继、WebSocket、mDNS、UDP Multicast、HEIC 自动转换或复杂密钥体系。单张图片上限为 100 MB，单条文字上限为 64 KB。

## 三、总体架构

```text
Windows
┌────────────────────────────────────────────┐
│ FastAPI + Uvicorn                           │
│ 文字消息 API + 图片上传/下载 API              │
│ SQLite 元数据仓库                           │
│ data/staging/  →  data/assets/YYYY/MM/DD/   │
│ 响应式 Web UI + 临时配对令牌                │
└──────────────────┬─────────────────────────┘
                   │ HTTP / 私有局域网
                   ▼
             iPhone Safari
```

SQLite 仅负责可查询的元数据、历史和关系；图片内容始终留在文件系统。文件路径写入数据库时使用相对 `data/` 的路径，不写入本机绝对路径。

运行时目录约定如下：

```text
rainier-link/
├── data/
│   ├── rainier.db
│   ├── staging/
│   │   └── <upload-id>.partial
│   ├── assets/
│   │   └── YYYY/MM/DD/<asset-id>.<ext>
│   └── logs/
├── app/
├── tests/
├── README.md
└── TECHNICAL_PLAN.md
```

`data/rainier.db`、图片、暂存文件和日志均为本机运行数据，不能提交到 Git；目录中的 `.gitkeep` 用于保留空目录。

## 四、持久化模型

### （一）messages

| 字段 | 约束 |
| --- | --- |
| `id` | 服务端生成的唯一标识 |
| `sender` | `pc` 或 `iphone` |
| `type` | `text` 或 `image` |
| `text_content` | 文字消息内容；图片消息为空 |
| `asset_id` | 图片消息引用 `assets.id`；文字消息为空 |
| `status` | 可见、删除等消息状态 |
| `created_at` | 服务端生成的时间戳 |
| `deleted_at` | 软删除时间，可为空 |

### （二）assets

| 字段 | 约束 |
| --- | --- |
| `id` | 服务端生成的唯一标识 |
| `kind` | 当前为 `image` |
| `original_filename` | 用户上传时的原始文件名，仅作为元数据保存 |
| `stored_filename` | 服务端生成的安全文件名 |
| `extension` | 规范化后的允许扩展名 |
| `mime_type` | 允许的图片 MIME 类型 |
| `size` | 实际写入字节数，不超过 100 MB |
| `sha256` | 完成上传后计算的完整文件摘要 |
| `relative_path` | 相对 `data/` 的存储路径 |
| `status` | `STAGING`、`AVAILABLE`、`DELETED` 或 `MISSING` |
| `created_at` | 服务端生成的时间戳 |
| `deleted_at` | 资源删除时间，可为空 |

图片上传不做自动去重；同一图片重复上传也生成独立资源。未来的保留策略可以增加设置表，但不在当前版本实现。

## 五、图片上传生命周期

1. 客户端提交 multipart 图片和发送方身份。
2. 服务端校验令牌、发送方、文件名、扩展名、MIME 类型和大小上限。
3. 服务端以 1 MB 分块读取到 `data/staging/<upload-id>.partial`，同时计算 SHA-256。
4. 读取完成后校验实际字节数和允许格式；失败上传不得成为可用资源。
5. 为最终资源生成安全文件名，并以原子方式将暂存文件移入 `data/assets/YYYY/MM/DD/`。
6. 在数据库事务中写入 `assets` 记录和对应的 `messages` 记录。
7. 若数据库事务失败，应将已落地的最终文件置为不可用并报告错误，不能留下指向有效历史的孤儿记录。
8. 上传源文件只读，不移动或覆盖用户原始文件。

服务启动检查 `data/staging/` 中的 `.partial` 文件时，只报告发现的文件并要求明确确认；本版本不自动删除任何残留文件。

## 六、HTTP API 目标边界

以下是 v0.2 已实现的资源边界。

### （一）现有文字资源

```text
GET  /api/health
POST /api/messages
GET  /api/messages?limit=50&before=<id>&after=<id>
```

`GET /api/messages` 默认返回最新历史，客户端按时间顺序显示。`limit` 默认 50；`before` 和 `after` 用于分页或增量同步。

### （二）图片资源

```text
POST   /api/assets/images
GET    /api/assets/{asset_id}
DELETE /api/messages/{message_id}
GET    /api/storage/stats
```

图片响应支持 inline 查看和 `download=1` 附件下载。资源记录存在但文件缺失时应标记 `MISSING` 并返回不可用状态，而不是返回伪造的成功内容。

### （三）错误约定

- `400`：字段格式、发送方、扩展名或 MIME 类型不合法
- `401`：缺少或无效的配对令牌
- `404`：消息或资源不存在
- `410`：资源元数据存在但物理文件缺失
- `413`：文字或图片超过大小限制
- `500`：未预期的服务错误；不得泄露本机绝对路径

## 七、安全与本地数据边界

服务只面向用户自己的可信局域网。令牌只提供轻量访问控制，不等同于 TLS；不要在公共网络开放服务。代码、测试、日志和文档不得写入真实局域网地址、个人绝对路径、令牌、API key、密码或图片内容。

运行数据目录必须被 Git 忽略，避免提交数据库、图片、日志和中断上传残留。删除消息需要在用户界面显示明确确认；后端删除动作应是软删除，并在资源无引用时再按既定策略处理本地文件。任何启动残留的删除都必须先向用户列出目标并获得确认。

## 八、模块边界

- `app/core/database.py`：SQLite 连接、初始化和事务边界。
- `app/repositories/`：消息与资源的查询、写入和状态更新。
- `app/services/message_service.py`：文字消息历史与分页语义。
- `app/services/asset_service.py`：图片校验、流式写入、摘要和生命周期。
- `app/services/asset_service.py`：数据目录、日期路径、流式写入、原子落盘和统计。
- `app/api/messages.py`：文字资源与删除 API；不直接操作 SQL。
- `app/api/assets.py`：图片上传、查看、下载和统计 API。
- `app/main.py`：应用初始化、启动检查、路由注册和本机页面。
- `app/web/`：文字与图片历史、上传进度、下载和删除确认的响应式页面。

页面实现需要同时覆盖 Windows 浏览器和 iPhone Safari；不依赖外部 CDN，不把图片转成 Base64 塞进消息接口。

## 九、实施顺序

1. 初始化 SQLite schema 和 repository，保持现有文字 API 兼容。
2. 将文字消息从内存存储迁移到 SQLite，并覆盖重启、分页和鉴权测试。
3. 增加数据目录、暂存文件和最终资源的 storage service。
4. 增加图片校验、流式上传、摘要、原子落盘和目标图片资源 API。
5. 增加图片历史展示、上传进度、查看/下载与删除确认页面。
6. 执行 Windows 本机、iPhone Safari、重启、缺失文件和残留 `.partial` 的验收。

每一步都应先通过自动化测试，再进入下一步；不因图片功能破坏既有文字链路、配对和局域网边界。

## 十、验收标准

- 文字消息在服务重启后仍可读取，旧的鉴权和轮询行为保持有效。
- JPG、PNG、HEIC/HEIF 至少各覆盖 PC 与 iPhone 来源样例。
- 图片上传不超过 100 MB，流式写入且不把二进制写入 SQLite。
- 存储后的字节数和 SHA-256 与输入一致，Unicode、空格和 Emoji 文件名可保存。
- 中断上传不会显示为可用消息；残留 `.partial` 只报告并等待确认。
- 删除消息需要显式确认；删除后历史不可见，资源状态符合引用关系。
- 手动移走或删除资源文件后，历史显示缺失状态并返回不可用响应。
- 所有受保护资源缺少或使用错误令牌时返回 `401`。
- 数据库、图片、暂存文件和日志不会出现在 Git 变更中，`.gitkeep` 可以保留。

## 十一、后续演进

大文件分块、断点续传、预览转换、SSE/WebSocket、持久设备管理和云端同步均属于后续评估项。新增能力必须保持图片二进制与 SQLite 元数据分离，并遵守局域网与鉴权边界。
