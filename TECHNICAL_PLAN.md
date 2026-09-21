# 一、项目目标

Rainier Link v0.1 是运行在 Windows 上的私人局域网通信服务。它让 Windows 与用户自己的 iPhone 通过浏览器建立连接，在无需账号、云服务器或 iOS App 的前提下，完成双向文字传输。

本阶段验证的唯一核心假设是：

```text
Windows HTTP 服务 ↔ 同一局域网 ↔ iPhone Safari
PC 发送文字 → iPhone 收到
iPhone 发送文字 → PC 收到
```

## （一）交付范围

1. Windows 本地 HTTP 服务
2. 响应式 HTML、CSS 和原生 JavaScript 页面
3. PC 与 iPhone 双向文字消息
4. 基于 HTTP 轮询的消息同步
5. 启动时生成二维码和临时配对令牌
6. 消息复制、基础错误提示和设备身份记忆
7. 面向 Windows、Chrome、Edge 和 iPhone Safari 的手工验收

## （二）非目标

v0.1 不实现图片、文件、大文件、断点续传、原生 iOS App、Windows 原生 GUI、账号、多用户、多设备发现、mDNS、UDP Multicast、互联网跨网络传输、云服务器、数据库、WebSocket、自动剪贴板同步或复杂密钥体系。

# 二、总体架构

## （一）部署形态

Windows 同时承担服务端、消息暂存、Web 页面提供方和 PC 浏览器客户端角色。服务绑定 `0.0.0.0:9527`，本机页面使用回环地址访问，iPhone 使用启动时检测到的局域网地址访问。

```text
Windows
┌────────────────────────────────────────┐
│ FastAPI + Uvicorn                      │
│ 静态 Web UI                            │
│ 内存 Message Store                     │
│ 临时 Pairing Token                     │
└───────────────────┬────────────────────┘
                    │ HTTP / Local Wi-Fi
                    ▼
              iPhone Safari
```

## （二）技术选型

- Python 3.11+
- FastAPI 与 Uvicorn
- HTML、CSS、Vanilla JavaScript
- qrcode 与 Pillow，用于生成配对二维码
- 内存列表，用于运行期间的消息存储

不引入 React、Vue、Node.js、npm、Rust、Docker、Redis、PostgreSQL、RabbitMQ 或其他与文字链路无关的基础设施。

# 三、通信协议

## （一）消息模型

消息统一使用 Message 语义，当前只允许 `type=text`。字段如下：

| 字段 | 约束 |
| --- | --- |
| `id` | 服务端生成的唯一标识 |
| `sender` | `pc` 或 `iphone` |
| `type` | 当前固定为 `text` |
| `content` | UTF-8 文字，单条不超过 64 KB |
| `created_at` | 服务端生成的时间戳 |

Transport 与 Payload 分离。API 不命名为 `send-text`，为未来增加其他 Payload 保留 `messages` 资源边界；但本版本拒绝图片、文件和 Base64 数据。

## （二）HTTP API

### 1. 健康检查

```text
GET /api/health
```

返回服务在线状态，用于确认设备能访问 Windows。

### 2. 发送消息

```text
POST /api/messages
```

请求包含 `sender`、`type` 和 `content`。服务端校验令牌、设备身份、消息类型、非空内容和 64 KB 大小限制，然后生成完整 Message 并写入 Message Store。

### 3. 获取消息

```text
GET /api/messages
```

返回当前服务进程内已保存的消息列表。客户端持有 `last_message_id`，可通过 `after` 参数只获取该消息之后的新消息。

```text
GET /api/messages?after=<message_id>
```

### 4. 错误约定

- `400`：空消息、非法 sender 或不支持的 type
- `401`：缺少或无效的配对令牌
- `413`：消息超过 64 KB
- `500`：未预期的服务错误

# 四、同步与配对

## （一）HTTP 轮询

PC 页面和 iPhone 页面每约 1000 毫秒请求一次 `GET /api/messages` 或增量查询。发送使用 `POST /api/messages`，服务端追加到内存列表；另一端在下一次轮询时取得新消息。v0.1 接受约两秒的端到端可见延迟，不使用 WebSocket 或其他长连接协议。

## （二）配对令牌

每次服务启动生成新的随机令牌。二维码编码启动提示中的局域网地址和令牌，客户端首次打开页面后将令牌保存在浏览器本地存储，并在 API 请求中发送 Bearer 令牌。

服务端对除健康检查外的 API 请求执行令牌校验：

```text
缺少令牌或令牌不匹配 → 401 Unauthorized
```

令牌只提供局域网内的轻量访问控制，不等同于 TLS 或完整身份系统。服务重启后令牌失效，客户端需重新扫描二维码。

## （三）设备身份

本机页面默认身份为 `pc`；通过局域网地址进入的页面默认身份为 `iphone`。必要时允许使用 URL 参数明确指定身份。客户端将用户选择保存到浏览器本地存储，刷新页面后沿用。

# 五、网络与启动行为

## （一）局域网地址

启动时通过 UDP Socket 的本地地址推断当前局域网 IP，不发送业务数据。若自动检测失败，应提示用户输入本次使用的局域网地址；自动检测必须是默认路径。文档、代码和测试不得写入真实个人 IP。

## （二）启动流程

执行 `python -m app.main` 后，应用按以下顺序工作：

1. 初始化 FastAPI、路由和静态文件
2. 生成本次启动的随机令牌
3. 检测局域网地址并构造配对 URL
4. 生成二维码
5. 监听 `0.0.0.0:9527`
6. 输出本机地址、局域网地址、二维码位置和在线状态
7. 尝试打开 Windows 本机页面

首次监听时，Windows 防火墙只允许专用网络；禁止向公用网络开放端口。路由器 AP 隔离、VPN 和访客网络可能阻断设备间访问，应在故障排查中明确提示。

# 六、模块边界

## （一）应用入口

`app/main.py` 负责 FastAPI 初始化、路由注册、静态文件、启动事件、令牌和二维码初始化，以及打开本机浏览器。

## （二）核心模块

- `core/network.py`：局域网地址检测和 Host URL 生成，不处理消息业务
- `core/security.py`：随机令牌生成、校验和请求鉴权
- `models/message.py`：Message 与 MessageCreate 数据模型
- `services/message_store.py`：内存存储的追加、列表和增量查询
- `api/messages.py`：只负责 REST API，不直接操作全局列表

## （三）Web 模块

`web/index.html`、`web/app.js` 和 `web/style.css` 提供统一响应式页面。页面必须支持 Windows Chrome、Windows Edge 和 iPhone Safari，包含连接状态、消息列表、输入框、发送按钮和 Copy 按钮。

# 七、目录约定

```text
rainier-link/
├── README.md
├── TECHNICAL_PLAN.md
├── requirements.txt
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── api/
│   │   ├── __init__.py
│   │   └── messages.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── network.py
│   │   └── security.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── message.py
│   ├── services/
│   │   ├── __init__.py
│   │   └── message_store.py
│   └── web/
│       ├── index.html
│       ├── app.js
│       └── style.css
├── runtime/
│   └── .gitkeep
└── tests/
    ├── test_health.py
    ├── test_messages.py
    └── test_auth.py
```

运行时生成的二维码属于临时产物，不应提交个人令牌或地址。消息内存存储不落盘。

# 八、实施顺序

## （一）阶段一至三：本机链路

1. FastAPI 启动与 `GET /api/health`
2. Message 模型、`POST /api/messages` 和 `GET /api/messages`
3. 静态 Web UI 与 Windows 双页面发送接收

每一步先在 Windows 本机验证通过，再进入下一步。

## （二）阶段四至六：局域网链路

1. 自动检测局域网地址并监听 `0.0.0.0:9527`
2. iPhone Safari 访问并完成双向文字传输
3. 接入令牌校验、二维码配对和无效令牌拒绝

## （三）阶段七：验收

补齐响应式布局、Copy、错误提示和 Windows 防火墙说明，然后执行验收清单与手工测试。不要在本机文字链路跑通前开发文件协议、复杂安全体系或视觉扩展。

# 九、验收标准

## （一）功能验收

- Windows 服务可用 `python -m app.main` 启动
- 本机页面可访问并显示在线状态
- iPhone 扫码后可在 Safari 打开同一应用
- PC → iPhone 和 iPhone → PC 的文字均可在两秒内显示
- 中文、英文、数字、Emoji、URL 和多行文字保持正确
- 消息可复制到系统剪贴板

## （二）边界验收

- 空消息返回 `400`
- 超过 64 KB 返回 `413`
- 无效令牌访问消息 API 返回 `401`
- 服务重启后消息清空且可重新启动
- 公用网络未开放，专用网络可按提示访问

## （三）手工测试

至少覆盖短中文、1000 字文本、URL、Emoji、多行文本、空文本、64 KB 边界、错误令牌、iPhone Safari 刷新、PC 页面刷新和 Windows 服务重启。

# 十、演进约束

后续版本可在不改变消息 API 语义的前提下替换 Message Store 为 SQLite，再评估持久设备配对与 SSE 或 WebSocket。图片和文件应进入独立 Transfer Layer，例如 `transfers` 资源、分块、校验和续传，不得把二进制或 Base64 塞进 `POST /api/messages`。

MVP 的优先级固定为：跑通、稳定、干净。任何新增技术必须直接服务于 Windows 与 iPhone 的局域网双向文字链路。
