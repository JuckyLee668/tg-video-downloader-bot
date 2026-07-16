# Telegram Media Downloader Bot

基于 **Telethon + FastAPI + SQLite** 的 Telegram 媒体下载与转发工具。支持 Telegram 频道搜索下载、Twitter/X 视频下载、批量转发、阿里云盘上传、本地文件管理和代理配置。

## 功能

- **Telegram Bot 控制**：私聊 Bot 即可登录、连接频道、搜索、下载和转发。
- **Twitter/X 视频下载**：直接发送链接或 `/tw` 命令，自动解析并下载视频。
- **统一交互选项**：收到任何视频均可选择：仅下载 / 转发到频道 / 上传云盘 / 全部。
- **默认操作**：配置 `/autofwd` 后，收到视频自动执行预设操作，无需手动选择。
- **Web 控制台**：默认监听 `127.0.0.1:8000`，可查看队列、历史、频道、本地文件和登录状态。
- **批量任务队列**：下载任务落 SQLite，worker 通过数据库原子领取任务，避免重复处理。
- **文件去重**：按 chat_id + message_id 自动去重，同一文件不会重复下载。
- **智能重命名**：支持 `{channel_title}/{date}_{original_name}` 等变量，可按频道/日期自动分目录。
- **进度推送**：大文件下载时 Bot 实时推送进度百分比。
- **频道自动监控**：定时扫描已连接频道，匹配关键词/媒体类型，自动创建下载任务。
- **流媒体转发**：转发的视频保留原始分辨率、时长和流式播放能力。
- **回复转发**：支持将文件作为对目标消息的回复发送。
- **下载安全**：保存文件名前做路径净化，避免非法文件名和路径穿越。
- **搜索优化**：多频道搜索使用有限并发，兼顾速度和 Telegram 限流风险。
- **阿里云盘上传**：下载完成后自动上传到阿里云盘指定目录。
- **状态持久化**：交互状态和任务进度持久化到 SQLite，重启不丢失。
- **Web API 安全**：生产环境强制设置 `WEB_API_KEY`；API key 使用常量时间比较，连续失败会临时锁定。
- **代理支持**：支持 HTTP 和 SOCKS5，可为全局或用户客户端配置代理。

## 目录结构

```text
core/                  配置、数据库、路径安全工具
  config.py            全局配置加载 (YAML + 环境变量)
  database.py          SQLite 数据库管理 (队列、历史、统计)
  paths.py             文件名净化与安全路径

downloader/            下载引擎和任务管理器
  engine.py            下载引擎 (Telethon download_media)
  external.py          外部视频下载 (yt-dlp: Twitter/X)
  manager.py           任务管理器 (worker 池、下载、转发、上传)
  aliyundrive_uploader.py  阿里云盘自动上传模块

telegram/              Telethon 客户端、路由、命令和频道搜索
  client.py            Telegram 客户端 (Bot + User)
  router.py            统一命令路由 + URL 自动识别 + FSM 状态机
  search.py            多频道异步搜索 (关键词/时间/最新)
  search_cache.py      搜索结果缓存
  state_manager.py     FSM 交互状态持久化
  limiter.py           消息频率限制
  auto_watch.py        频道自动监控管理器
  handlers/
    auth.py            登录 / 登录状态
    channel.py         频道连接 / 列表
    download.py        批量下载、队列、取消
    forward.py         批量转发、链接转发
    search.py          频道搜索
    cmd_channel.py     频道管理命令路由
    cmd_download.py    下载命令路由
    cmd_forward.py     转发命令路由
    cmd_search.py      搜索命令路由
    cmd_x.py           Twitter / X 视频下载 (/tw)
    cmd_aliyun.py      阿里云盘管理 (/aliyun)
    action_prompt.py   统一视频操作交互 (所有来源共用)
    storage.py         本地文件管理
    system.py          帮助 / 状态
    thumbnail.py       缩略图生成 (视频/图片)
    local_forward.py   默认操作配置 (/autofwd)
    watch_handler.py   频道自动监控配置
    utils.py           公用工具 (索引解析、格式化、文件名提取、文件信息)

web/                   FastAPI 应用、API 路由和请求模型
  server.py            FastAPI 应用创建
  routes.py            API 路由
  api_models.py        Pydantic 请求模型

public/                Web 静态页面
tests/                 pytest 测试
data/                  SQLite 数据库、cookies 文件
```

## 环境要求

- Python 3.11+ 推荐
- 可访问 Telegram 的网络环境
- Bot Token
- Telegram API ID / API Hash
- (可选) ffmpeg — Twitter 视频合并需要，系统一般自带

## 快速开始

1. 安装依赖：

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell：

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. 配置环境变量：

复制 `.env.example` 为 `.env`，填写：

```env
BOT_TOKEN=你的BotToken
USER_API_ID=你的API_ID
USER_API_HASH=你的API_HASH
```

3. 启动：

```bash
python main.py
```

也可以使用一键脚本：

Linux / macOS：

```bash
chmod +x start.sh
./start.sh
```

Windows PowerShell：

```powershell
.\start.ps1
```

启动后访问：

```text
http://127.0.0.1:8000
```

## 配置

项目会优先读取 `config.local.yaml`，不存在时读取 `config.yaml`。仓库提供 `config.example.yaml` 作为参考。

常用配置：

```yaml
save_path: ./downloads
max_download_task: 3
max_connected_channels: 10
web_host: 127.0.0.1
web_port: 8000
web_cors_origins:
  - http://127.0.0.1:8000
environment: local
allowed_user_ids: []
proxy: null
user_api:
  api_id: ''
  api_hash: ''
  proxy: null
```

### 用户权限

`allowed_user_ids` 控制谁可以使用 Bot：

```yaml
allowed_user_ids: []
```

空列表表示不限制，仅建议本地测试使用。

```yaml
allowed_user_ids:
  - me
```

`me` 表示已登录的用户客户端账号。首次登录前，Bot 私聊中的 `/login` 会允许一次初始化流程。

```yaml
allowed_user_ids:
  - "123456789"
  - "@alice"
```

也可以指定 Telegram 用户 ID 或用户名。

### 默认操作 (自动模式)

```yaml
default_action:
  enabled: false
  action: download        # download / forward / cloud / all
  target_chat: ""         # 转发目标 (forward/all 时使用)
  delete_after_forward: false
```

启用后收到任何视频都会自动按配置执行，不再询问。通过 `/autofwd` 命令在线配置。

### Twitter / X 视频下载

**配置登录**：在浏览器中登录 x.com，F12 → Application → Cookies → x.com，找到 `auth_token` 和 `ct0`，发送给 Bot：

```
auth_token <auth_token值> <ct0值>
```

Bot 会自动保存为 `data/twitter_cookies.txt` 并用于所有 Twitter 下载。登录状态可在 `/login` 中查看。

之后直接发送 Twitter/X 链接即可自动识别并下载，或使用 `/tw <url>`。

### 阿里云盘自动上传

```yaml
aliyundrive_upload:
  enabled: true
  remote_path: /video
  delete_after_upload: true
```

下载完成后自动上传文件到阿里云盘，可选上传后删除本地文件。

**自动安装 CLI**：首次使用 `/aliyun` 命令时，如果系统未安装 `aliyunpan` CLI，Bot 会自动检测操作系统和 CPU 架构，从 [tickstep/aliyunpan](https://github.com/tickstep/aliyunpan) 下载对应版本的 `.zip` 并安装：

| 平台 | 资产识别 | 安装路径 |
|------|---------|---------|
| Linux amd64/arm64 | `aliyunpan-v*-linux-*.zip` | `/usr/local/bin/aliyunpan` |
| Windows x64/x86/arm64 | `aliyunpan-v*-windows-*.zip` | PATH 可写目录 |
| macOS amd64/arm64 | `aliyunpan-v*-darwin-*.zip` | `/usr/local/bin/aliyunpan` |

### 下载后自动转发（旧配置）

```yaml
local_forward:
  enabled: false
  target_chat: ""    # chat_id 或 @username
  delete_after_forward: false
```

下载完成后自动把文件转发到指定聊天。建议改用新的 `default_action` 配置，配合 `/autofwd` 使用，适用范围更广。

### 文件去重

```yaml
file_dedup:
  enabled: true
  by_message_id: true   # 按 chat_id + message_id 去重
  by_file_id: false     # 按 Telegram file_id 去重
```

下载前自动检查是否已下载过，避免重复下载和存储。

### 智能重命名

```yaml
file_rename:
  enabled: false
  pattern: "{channel_title}/{date}_{original_name}"
```

下载后自动重命名文件。可用变量（默认 pattern 自动追加扩展名）：

| 变量 | 说明 | 示例 |
|------|------|------|
| `{channel_title}` | 频道标题 / 作者 | `MyChannel` |
| `{channel_username}` | 频道用户名 | `@mychannel` |
| `{date}` | 日期 | `2024_01` |
| `{time}` | 时间 | `15_30_00` |
| `{original_name}` | 原始文件名（不含扩展名） | `video` |
| `{ext}` | 文件扩展名（含点号） | `.mp4` |

### 进度推送

```yaml
progress_notification: true
```

大文件下载时 Bot 会每 20% 发送一次进度消息。可在聊天中通过 `/push` 命令开关。

### 频道自动监控

```yaml
watch:
  interval: 300   # 轮询间隔（秒）
```

通过 Bot 命令 `/watch` 在线管理监控规则。详见 Bot 命令章节。

### Web API 安全

本地默认不要求 `WEB_API_KEY`。如果要让 Web 服务监听公网地址，必须设置强随机密钥：

```env
WEB_API_KEY=your_strong_random_key
APP_ENV=production
WEB_HOST=0.0.0.0
```

### 代理

```yaml
proxy:
  scheme: socks5
  hostname: 127.0.0.1
  port: 10808
  username: null
  password: null
  rdns: true
user_api:
  proxy: null
```

`user_api.proxy` 优先级高于全局 `proxy`。

## Bot 命令

| 命令 | 作用 | 子命令 |
| --- | --- | --- |
| `/start` | 查看帮助 | — |
| `/help` `/h` | 完整命令菜单 | — |
| `/status` `/s` | 系统和任务状态 | — |
| `/login` | 登录状态 / 登录账号 | 无参显示状态，有参开始登录 |
| `/channel` | 频道管理 | `connect @xx`、`list`。回复链接可直接添加 |
| `/search` | 搜索频道媒体 | `keyword xx`、`recent [n]`、`time <开始> <结束>`、`history` |
| `/download` | 批量下载 | `[序号]`、`format <格式> [序号]`。单选弹出交互选项 |
| `/forward` | 批量转发 | `[序号]`、`to @目标 [序号]`、`link <url>`。单选弹出交互选项 |
| `/tw` | Twitter/X 视频下载 | 直接发链接也可自动识别 |
| `/dl` | 下载队列 | — |
| `/cancel` `/c` | 取消操作 | — |
| `/clear` `/cl` | 清理下载队列 | — |
| `/files` `/f` | 本地文件管理 | `del <序号>`、`clear`、`thumbs` |
| `/aliyun` | 阿里云盘管理 | `login`、`logout`、`ls`、`tree`、`on`、`off`、`path` |
| `/autofwd` | 默认操作配置 | `on`、`off`、`action <类型>`、`target <id>` |
| `/push` | 下载进度推送 | `on`、`off` |
| `/rename` | 智能重命名 | `set <pattern>`、`on`、`off` |
| `/watch` | 频道自动监控 | `add`、`remove`、`on`、`off` |

### 旧命令别名（仍可使用）

| 别名 | 等价于 |
|------|--------|
| `/bf` | `/forward` |
| `/bd` | `/download` |
| `/bdf` | `/download format` |

### 默认操作命令

| 命令 | 说明 |
|------|------|
| `/autofwd` | 查看当前配置 |
| `/autofwd on` | 启用 — 收到视频自动执行 |
| `/autofwd off` | 禁用 — 收到视频询问操作 |
| `/autofwd action download` | 默认：下载到本地 |
| `/autofwd action forward` | 默认：下载并转发 |
| `/autofwd action cloud` | 默认：下载并上传云盘 |
| `/autofwd action all` | 默认：全部执行 |
| `/autofwd target @xxx` | 设置转发目标 |

### 频道自动监控命令

| 命令 | 说明 |
|------|------|
| `/watch` | 查看所有监控规则 |
| `/watch add @channel [keyword]` | 添加规则（可选关键词） |
| `/watch remove <id>` | 删除规则 |
| `/watch on <id>` | 启用规则 |
| `/watch off <id>` | 禁用规则 |

### 本地文件管理

- `/files` — 查看下载文件列表
- `/files thumbs` — 查看缩略图缓存状态
- `/files del <序号>` — 删除文件
- `/files clear` — 清空全部下载文件

## Web 控制台

默认地址：

```text
http://127.0.0.1:8000
```

主要能力：

- 查看下载队列和进度
- 删除、清空、重试任务
- 查看下载历史
- 搜索历史记录
- 连接频道、加入频道
- 搜索频道媒体（关键词/时间/最新）
- 批量下载和转发
- 登录用户客户端
- 配置代理
- 本地文件管理（查看/删除/清空）
- 缩略图缓存管理

## 数据库与队列

SQLite 数据库默认位于：

```text
data/telegram_downloader.db
```

### 表结构

| 表名 | 用途 |
|------|------|
| `download_queue` | 下载任务队列（pending → downloading → completed） |
| `download_history` | 下载历史记录 |
| `user_states` | 交互状态（FSM）持久化 |
| `auto_watch` | 频道监控规则 |
| `watch_state` | 监控状态（记录已读消息 ID） |

下载任务会先写入 `download_queue`。worker 不直接信任内存队列，而是通过数据库原子领取任务：

```text
pending/failed -> downloading -> completed/history
```

## Docker

构建镜像：

```bash
docker build -t tg-media-downloader .
```

运行：

```bash
docker run --rm -p 8000:8000 \
  --env-file .env \
  -e APP_ENV=production \
  -e WEB_HOST=0.0.0.0 \
  -e WEB_API_KEY=your_strong_random_key \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/downloads:/app/downloads" \
  tg-media-downloader
```

## 测试与代码质量

运行测试：

```bash
pytest
```

运行 lint：

```bash
ruff check core downloader telegram web tests
```

### 测试覆盖

| 模块 | 文件 | 覆盖内容 |
|------|------|----------|
| `core/paths.py` | `test_paths.py` | 文件名净化、路径穿越防护 |
| `core/database.py` | `test_database.py` | 任务原子领取、完成归档 |
| `core/config.py` | `test_config.py` | YAML/环境变量加载、配置默认值 |
| `web/api_models.py` | `test_api_models.py` | 搜索/下载/转发/代理/登录模型验证 |
| `telegram/handlers/utils.py` | `test_utils.py` | 序号解析、格式化、文件名提取 |
| `telegram/search_cache.py` | `test_search_cache.py` | TTL 过期、容量上限 |
| `telegram/state_manager.py` | `test_state_manager.py` | FSM 状态读写、隔离、清除 |
| `downloader/manager.py` | `test_resolve_forward_peer.py` | 转发目标解析（含消息 ID） |
| `downloader/external.py` | `test_external_downloader.py` | Twitter URL 检测、信息提取 |

## CI

仓库包含 GitHub Actions 工作流：

```text
.github/workflows/ci.yml
```

CI 会执行：

- 安装依赖
- `ruff check`
- `pytest`

## 常见问题

### Web 页面打不开

确认程序正在运行，并监听 `127.0.0.1:8000`。如果端口被占用，设置 `WEB_PORT=8001`。

### 无法连接 Telegram

确认网络可访问 Telegram。如需代理，在 `config.local.yaml` 或 Web 控制台中配置 HTTP/SOCKS5 代理。

### 登录失败

检查 `.env` 中的 `USER_API_ID` 和 `USER_API_HASH`，然后在 Bot 私聊中执行 `/login`。

### Twitter 视频下载失败

1. 确认 cookies 有效：`/login` 查看 "Twitter Cookies" 状态
2. 重新获取 cookies：浏览器 x.com → F12 → Application → Cookies → `auth_token` + `ct0` → 发送 `auth_token <值1> <值2>` 给 Bot
3. confirm ffmpeg 已安装：`ffmpeg -version`

### 搜索结果没有缩略图

- 确保用户客户端已登录（`/login` 查看）
- 视频没有内置缩略图时会跳过
- Web 页面需要等待异步生成完成

### 公开部署注意事项

公网部署必须同时满足：

- 设置 `APP_ENV=production`
- 设置强随机 `WEB_API_KEY`
- 配置正确的 `web_cors_origins`
- 使用反向代理提供 HTTPS
- 不提交 `.env`、`session.txt`、`*.session`、数据库和下载目录

## 获取 Telegram 凭据

Bot Token：在 Telegram 中联系 `@BotFather`，使用 `/newbot` 创建。

API ID / API Hash：访问 `https://my.telegram.org`，进入 API development tools 创建应用。

## 免责声明

本项目仅供学习、个人备份和合法用途。请遵守 Telegram 服务条款以及所在地法律法规。
