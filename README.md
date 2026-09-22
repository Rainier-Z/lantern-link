# 一、Rainier Link v0.2

Rainier Link 是一个运行在 Windows 上的私人局域网文字与图片通道。Windows 运行本地 HTTP 服务，iPhone 使用 Safari 访问同一页面；两端无需账号、云服务或 iPhone App，即可双向发送文字和图片。

## （一）项目范围

v0.2 在 v0.1 文字链路的基础上增加以下闭环：

1. Windows 启动 Rainier Link 并显示配对二维码
2. iPhone 扫码后在 Safari 打开页面
3. Windows 与 iPhone 双向发送文字
4. 两端通过 HTTP 轮询在约两秒内看到新消息
5. 每条消息可复制到系统剪贴板
6. PC 与 iPhone 发送 JPG、PNG、GIF、WEBP、HEIC 或 HEIF 图片
7. 图片按流式上传、SHA-256 校验，并保存到本地日期目录
8. 文字与图片元数据写入 SQLite，服务重启后仍可查看历史
9. 图片可在线查看或下载，消息可软删除

图片二进制不写入 SQLite BLOB，而是保存于 `data/assets/`；SQLite 只保存消息、图片元数据和文件相对路径。上传中的文件先写入 `data/staging/`，完成校验并提交元数据后再进入日期目录。

## （二）明确不包含

本版本不实现通用文件、视频、超大文件分块续传、断点续传、系统剪贴板自动同步、原生 iOS App、Windows 原生 GUI、账号、多用户、多设备发现、互联网传输、云服务器、WebSocket、mDNS、UDP Multicast、HEIC 自动转换或复杂加密体系。单张图片上限为 100 MB。

## （三）运行边界

Rainier Link 只面向用户自己的固定设备和可信的家庭或办公局域网。配对令牌用于阻止同一 Wi-Fi 中的未授权请求，但本版本不提供 TLS 或互联网安全防护；不要在公共网络上开放服务。图片接口、消息接口、历史接口和下载接口均应通过当前启动的配对令牌鉴权。

# 二、Windows 环境准备

## （一）前置条件

需要准备：

- Windows 10 或更高版本
- Python 3.11 或更高版本
- 一台运行 Safari 的 iPhone
- Windows 与 iPhone 连接到同一个 Wi-Fi

不需要 Node.js、npm、Docker 或外部数据库；SQLite 由 Python 标准库提供。

## （二）创建虚拟环境

在项目目录打开 PowerShell，执行：

```powershell
py -3.11 -m venv .venv
```

激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 不允许激活脚本，可跳过激活，直接使用虚拟环境中的 Python：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## （三）安装依赖

激活虚拟环境后执行：

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

依赖包括 FastAPI、Uvicorn、qrcode、Pillow 和 `python-multipart`。最后一项用于解析浏览器的 multipart 图片上传。依赖应安装在项目的 `.venv` 中，不要把个人令牌、局域网地址、图片内容或其他机器信息写入代码和文档。

## （四）自动化测试

安装开发依赖并执行测试：

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

测试依赖仅用于本地验证，不会改变运行时依赖。

# 三、启动与配对

## （一）启动服务

在项目根目录执行：

```powershell
python -m app.main
```

启动后，程序会监听配置的本地端口，并自动尝试在 Windows 浏览器打开配对页面。页面显示本次启动生成的二维码和连接状态。

首次启动会创建 `data/rainier.db`、`data/staging/`、`data/assets/` 和日志目录（如启用）。这些都是本机运行数据，不应提交到 Git。启动时如果发现上一次中断留下的 `.partial` 文件，只会报告文件并要求明确确认；不会自动删除。

如果浏览器没有自动打开，请使用当前服务生成的本机配对链接访问，不要复用旧二维码或旧链接。

## （二）首次配对

1. 确认 Windows 与 iPhone 使用同一个 Wi-Fi。
2. 在 Windows 浏览器打开 Rainier Link 页面，确认二维码已经显示。
3. 用 iPhone 系统相机扫描二维码。
4. 点击提示，在 Safari 中打开二维码提供的地址。
5. 第一次进入时按页面提示确认设备为 iPhone；Windows 页面确认设备为 PC。
6. 分别发送一条测试文字，确认两端都能收到。

二维码 URL 含有本次启动生成的临时配对令牌。不要把二维码或完整 URL 发给不可信的人；服务重启后应重新使用新二维码配对。

## （三）日常使用

打开服务后，Windows 和 iPhone 使用各自的 Rainier Link 页面发送文字或图片。页面通过 HTTP 轮询检查新消息，不使用 WebSocket。单条文字上限为 64 KB，单张图片上限为 100 MB。图片原始字节保持不变，不自动转换 HEIC；不支持预览的格式仍可下载。服务重启后，SQLite 中的历史消息和本地图片元数据会保留。

# 四、Windows 防火墙

第一次监听局域网端口时，Windows Defender Firewall 可能弹出允许访问提示。只勾选或允许：

- 专用网络（Private networks）

不要勾选或允许：

- 公用网络（Public networks）

如果误选了公用网络，请在 Windows 防火墙的允许应用列表中撤销公用网络权限，并仅保留专用网络。网络配置也应确认当前 Wi-Fi 被 Windows 识别为专用网络。

# 五、v0.2 验收清单

完成以下项目后，Rainier Link v0.2 才算验收通过：

- [ ] `python -m app.main` 能在 Windows 正常启动
- [ ] Windows 浏览器可打开本机 Rainier Link 页面
- [ ] 启动时能生成并显示二维码或二维码地址
- [ ] iPhone 相机扫码后能在 Safari 打开页面
- [ ] PC → iPhone 的中文、英文、数字、Emoji、多行文字均能收到
- [ ] iPhone → PC 的中文、英文、数字、Emoji、多行文字均能收到
- [ ] 两端新消息通常在两秒内出现
- [ ] 任意消息的 Copy 按钮能复制文字
- [ ] 没有有效令牌的消息接口请求返回 401
- [ ] 空文字被拒绝，超过 64 KB 的文字被拒绝
- [ ] 服务重启后历史文字消息仍可查看，并能重新启动和配对
- [ ] JPG、PNG、HEIC 图片可上传、持久化、查看或下载
- [ ] 图片上传显示进度，超过 100 MB 或不支持格式时被拒绝
- [ ] 图片字节数、SHA-256 和原始文件名与上传结果一致
- [ ] 删除消息前显示明确确认，删除后历史不可见，未被引用的本地资源进入删除状态
- [ ] 中断上传不会把 `.partial` 文件伪装成可用图片；残留文件只报告并等待确认
- [ ] 防火墙仅允许专用网络，公用网络未开放

# 六、故障排查

## （一）iPhone 无法打开页面

按以下顺序检查：

1. Windows 与 iPhone 是否连接同一个 Wi-Fi，而不是一个使用移动数据。
2. Windows 防火墙是否仅允许专用网络访问。
3. 是否使用了启动终端显示的当前局域网地址。
4. 路由器是否启用了 AP 隔离、客户端隔离或访客网络隔离。
5. Windows 是否连接了 VPN；如有，暂时断开后重试。
6. 服务窗口是否仍显示在线；若已停止，请重新启动并扫描新二维码。

## （二）扫码后提示无权限

令牌可能已失效、被截断或来自旧的服务进程。关闭旧页面，确认当前服务仍在运行，用当前启动生成的二维码重新配对。不要手动修改二维码 URL。

## （三）只能发送，收不到新消息

确认两个页面都已完成设备身份选择，并保持页面打开。先刷新页面，再各发送一条短文字；若仍无效，停止服务后重新启动并重新配对。

## （四）文字乱码或发送失败

先用短的纯文本测试，再检查消息是否超过 64 KB。中文、Emoji 和多行文字属于验收范围；文件或 Base64 数据不属于本版本支持范围。

## （五）端口已被占用

关闭占用该端口的旧 Rainier Link 进程后再启动。不要在文档或代码中硬编码个人机器地址；如端口需要调整，应由项目配置统一变更，并同步更新启动提示与防火墙规则。

## （六）服务重启后历史仍在

v0.2 的消息与图片元数据存储在 `data/rainier.db`，图片文件存储在 `data/assets/`，正常重启后应继续可见。若某个数据库记录对应的文件已被外部删除，页面应显示资源缺失状态并返回不可用提示；不要手动改动数据目录。

# 七、后续版本方向

后续版本可评估大文件分块、断点续传、图片预览转换、SSE 或 WebSocket，以及更细粒度的设备管理。任何后续功能都不应破坏 v0.2 的消息接口、SQLite 元数据约束、本地文件存储边界和局域网安全边界。
