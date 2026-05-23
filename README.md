# Telegram Media Downloader Bot

基于 **Telethon + FastAPI + SQLite** 的 Telegram 媒体下载与转发工具。提供 Telegram Bot 命令和 Web 控制台，支持频道连接、关键词搜索、时间范围搜索、批量下载、批量转发、自动上传阿里云盘、本地文件管理和代理配置。

## 功能

- **Telegram Bot 控制**：私聊 Bot 即可登录用户客户端、连接频道、搜索、下载和转发。
- **Web 控制台**：默认监听 `127.0.0.1:8000`，可查看队列、历史、频道、本地文件和登录状态。
- **批量任务队列**：下载任务落 SQLite，worker 通过数据库原子领取任务，避免重复处理。
- **文件去重**：按 chat_id + message_id 自动去重，同一文件不会重复下载。
- **智能重命名**：支持 `{channel_title}/{date}_{original_name}` 等变量，可按频道/日期自动分目录。
- **进度推送**：大文件下载时 Bot 实时推送进度百分比。
- **频道自动监控**：定时扫描已连接频道，匹配关键词/媒体类型，自动创建下载任务。
- **缩略图预览**：搜索结果中为视频和图片生成缩略图，支持 Web 端异步加载。
- **下载安全**：保存文件名前做路径净化，避免非法文件名和路径穿越。
- **搜索优化**：多频道搜索使用有限并发，兼顾速度和 Telegram 限流风险。
- **自动转发**：下载完成后自动转发到指定聊天（如 Saved Messages）。
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
  manager.py           任务管理器 (worker 池、下载、转发、自动上传)
  aliyundrive_uploader.py  阿里云盘自动上传模块

telegram/              Telethon 客户端、路由、命令和频道搜索
  client.py            Telegram 客户端 (Bot + User)
  router.py            统一命令路由 + FSM 状态机 + 媒体自动入库
  search.py            多频道异步搜索 (关键词/时间/最新)
  search_cache.py      搜索结果缓存
  state_manager.py     FSM 交互状态持久化
  limiter.py           消息频率限制
  auto_watch.py        频道自动监控管理器
  handlers/
    auth.py            登录相关命令
    channel.py         频道连接/列表
    download.py        批量下载、队列、取消
    forward.py         批量转发、链接转发
    search.py          频道搜索命令
    storage.py         本地文件管理命令
    system.py          帮助/状态命令
    thumbnail.py       缩略图生成 (视频/图片)
    local_forward.py   下载后自动转发配置
    watch_handler.py   频道自动监控配置命令
    utils.py           公用工具 (索引解析、格式化、文件名提取)

web/                   FastAPI 应用、API 路由和请求模型
  server.py            FastAPI 应用创建
  routes.py            API 路由 (搜索、下载、转发、登录、文件管理)
  api_models.py        Pydantic 请求模型

public/                Web 静态页面
tests/                 pytest 测试
data/                  SQLite 数据库目录
```

## 环境要求

- Python 3.11+ 推荐
- 可访问 Telegram 的网络环境
- Bot Token
- Telegram API ID / API Hash

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

脚本支持只检查环境而不启动服务：

```bash
./start.sh --check
```

```powershell
.\start.ps1 -Check
```

跳过依赖安装：

```bash
./start.sh --skip-install
```

```powershell
.\start.ps1 -SkipInstall
```

临时覆盖 Web 监听地址和端口：

```bash
./start.sh --host 127.0.0.1 --port 8001
```

```powershell
.\start.ps1 -HostOverride 127.0.0.1 -PortOverride 8001
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

### 阿里云盘自动上传

```yaml
aliyundrive_upload:
  enabled: true
  remote_path: /video
  delete_after_upload: true
```

下载完成后自动上传文件到阿里云盘，可选上传后删除本地文件。

### 下载后自动转发

```yaml
local_forward:
  enabled: false
  target_chat: ""    # chat_id 或 @username
  delete_after_forward: false
```

下载完成后自动把文件转发到指定聊天（如 Saved Messages、群组）。可通过 Bot 命令 `/lf` 在线配置。

### 文件去重

```yaml
file_dedup:
  enabled: true
  by_message_id: true   # 按 chat_id + message_id 去重
  by_file_id: false     # 按 Telegram file_id 去重（更严格）
```

下载前自动检查是否已下载过，避免重复下载和存储。

### 智能重命名

```yaml
file_rename:
  enabled: false
  pattern: "{channel_title}/{date}_{original_name}"
```

下载后自动重命名文件。可用变量：

| 变量 | 说明 | 示例 |
|------|------|------|
| `{channel_title}` | 频道标题 | `MyChannel` |
| `{channel_username}` | 频道用户名 | `@mychannel` |
| `{date}` | 消息日期 | `2024_01` |
| `{time}` | 消息时间 | `15_30_00` |
| `{original_name}` | 原始文件名 | `video` |
| `{ext}` | 文件扩展名 | `.mp4` |

支持子目录（在 pattern 中使用 `/`）。

### 进度推送

```yaml
progress_notification: true
```

大文件下载时 Bot 会每 20% 发送一次进度消息。

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

生产环境下，如果 `APP_ENV=production` 但没有设置 `WEB_API_KEY`，API 会拒绝服务。

### CORS

默认只允许本地控制台来源：

```yaml
web_cors_origins:
  - http://127.0.0.1:8000
```

如需反向代理或自定义域名，显式添加对应来源。

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

| 命令 | 作用 |
| --- | --- |
| `/start` | 查看帮助 |
| `/help` | 查看完整命令 |
| `/status` 或 `/s` | 查看系统和任务状态 |
| `/auth` | 查看用户客户端登录状态 |
| `/login` | 登录 Telegram 用户客户端 |
| `/cc` | 连接频道 |
| `/channels` | 查看已连接频道 |
| `/csk` | 按关键词搜索频道媒体 |
| `/csr` | 获取最近媒体消息 |
| `/cst` | 按时间范围搜索媒体 |
| `/sh` | 搜索下载历史 |
| `/bd` | 批量下载上次搜索结果 |
| `/bdf` | 按文件格式批量下载 |
| `/bf` | 批量转发上次搜索结果 |
| `/forward` | 通过链接添加转发任务 |
| `/dl` | 查看下载队列 |
| `/cancel` 或 `/c` | 取消当前用户待处理任务 |
| `/clear` 或 `/cl` | 清理当前用户搜索缓存 |
| `/files` 或 `/f` | 查看/管理本地下载文件 |
| `/lf` | 配置下载后自动转发 |
| `/watch` | 管理频道自动监控规则 |

### 频道自动监控命令

| 命令 | 说明 |
|------|------|
| `/watch` | 查看所有监控规则 |
| `/watch add @channel [keyword]` | 添加规则（可选关键词） |
| `/watch remove <id>` | 删除规则 |
| `/watch on <id>` | 启用规则 |
| `/watch off <id>` | 禁用规则 |

监控规则会每 5 分钟自动扫描频道新消息，匹配规则后自动创建下载任务。关键词不区分大小写，留空则监控全部媒体。

### 本地文件管理

`/files` 命令支持以下子命令：

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

交互状态（FSM）和频道监控状态也会持久化到同一数据库，重启后自动恢复。

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

测试覆盖以下核心模块（114 个测试用例）：

| 模块 | 文件 | 覆盖内容 |
|------|------|----------|
| `core/paths.py` | `test_paths.py` | 文件名净化、路径穿越防护 |
| `core/database.py` | `test_database.py` | 任务原子领取、完成归档 |
| `core/config.py` | `test_config.py` | YAML/环境变量加载、配置默认值、本地覆写、持久化 |
| `web/api_models.py` | `test_api_models.py` | 搜索/下载/转发/代理/登录模型约束验证 |
| `telegram/handlers/utils.py` | `test_utils.py` | 序号解析、文件大小格式化、时间格式化、文件名提取 |
| `telegram/search_cache.py` | `test_search_cache.py` | TTL 过期、容量上限、增删查 |
| `telegram/limiter.py` | `test_limiter.py` | 每秒/每分钟限速、并发安全、历史过期 |
| `telegram/auto_watch.py` | `test_watch_manager.py` | 媒体文件信息提取（视频/图片/音频/文档等） |

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

确认程序正在运行，并监听：

```text
127.0.0.1:8000
```

如果端口被占用，可以设置：

```env
WEB_PORT=8001
```

### 无法连接 Telegram

确认网络可访问 Telegram。如需代理，在 `config.local.yaml` 或 Web 控制台中配置 HTTP/SOCKS5 代理。

### 登录失败

检查 `.env` 中的：

```env
USER_API_ID=
USER_API_HASH=
```

然后在 Bot 私聊中执行 `/login`。

### Web API 返回 WEB_API_KEY missing

说明当前已设置 `WEB_API_KEY`，调用 API 时需要带请求头：

```text
X-API-Key: your_strong_random_key
```

### 搜索结果没有缩略图

搜索 Bot 命令自动为前 15-20 条视频/图片生成缩略图。如果没有显示：
- 确保用户客户端已登录（`/auth` 查看）
- 视频没有内置缩略图时会跳过（下载整个视频抽帧开销太大）
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
