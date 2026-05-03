# Telegram Media Downloader Bot

基于 **Telethon + FastAPI + SQLite** 的 Telegram 媒体下载与转发工具。项目提供 Telegram Bot 命令和 Web 控制台，支持频道连接、关键词搜索、时间范围搜索、批量下载、批量转发、代理配置和下载历史管理。

## 功能

- Telegram Bot 控制：私聊 Bot 即可登录用户客户端、连接频道、搜索、下载和转发。
- Web 控制台：默认监听 `127.0.0.1:8000`，可查看队列、历史、频道和登录状态。
- 批量任务队列：下载任务落 SQLite，worker 通过数据库原子领取任务，避免重复处理。
- 下载安全：保存文件名前会做路径净化，避免非法文件名和路径穿越。
- 搜索优化：多频道搜索使用有限并发，兼顾速度和 Telegram 限流风险。
- Web API 安全：生产环境强制设置 `WEB_API_KEY`；API key 使用常量时间比较，连续失败会临时锁定。
- 代理支持：支持 HTTP 和 SOCKS5，可为全局或用户客户端配置代理。

## 目录结构

```text
core/          配置、数据库、路径安全工具
downloader/    下载引擎和任务管理器
telegram/      Telethon 客户端、路由、命令处理和频道搜索
web/           FastAPI 应用、API 路由和请求模型
public/        Web 静态页面
tests/         pytest 测试
data/          SQLite 数据库目录
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
| `/bd` | 批量下载上次搜索结果 |
| `/bdf` | 按文件格式批量下载 |
| `/bf` | 批量转发上次搜索结果 |
| `/forward` | 通过链接添加转发任务 |
| `/dl` | 查看下载队列 |
| `/cancel` 或 `/c` | 取消当前用户待处理任务 |
| `/clear` 或 `/cl` | 清理当前用户搜索缓存 |

## Web 控制台

默认地址：

```text
http://127.0.0.1:8000
```

主要能力：

- 查看下载队列和进度
- 删除、清空、重试任务
- 查看下载历史
- 连接频道、加入频道
- 搜索频道媒体
- 登录用户客户端
- 配置代理

## 数据库与队列

SQLite 数据库默认位于：

```text
data/telegram_downloader.db
```

下载任务会先写入 `download_queue`。worker 不直接信任内存队列，而是通过数据库原子领取任务：

```text
pending/failed -> downloading -> completed/history
```

这使进程重启和多 worker 场景下的重复下载风险更低。

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

当前测试覆盖：

- 文件名净化和下载路径安全
- SQLite 任务原子领取
- 下载完成后的历史归档
- Web API 请求模型约束

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
