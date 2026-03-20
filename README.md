# Telegram Media Downloader Bot

基于 **Telethon + FastAPI** 的多端媒体下载器，提供 Telegram Bot 指令与 Web 控制台，支持批量下载 / 转发、关键字与时间范围搜索，并可按需启用 HTTP/SOCKS5 代理。

## 功能亮点
- 高并发下载：自适应并发队列，支持断点、批量任务。
- 双端控制：Bot 命令 + Web 界面实时查看/管理任务。
- 搜索增强：按关键字、时间区间或最近消息筛选，并可一键批量下载/转发。
- 账号双客户端：Bot 客户端 + 用户（MTProto）客户端，分离权限更安全。
- 代理支持：可为全局或用户客户端配置 HTTP / SOCKS5 代理（默认关闭）。

## 环境要求
- Python 3.8+
- 可访问 Telegram 的网络（如需代理可在配置中开启）

## 安装与启动
```bash
git clone https://github.com/your-repo/tg-video-downloader-bot.git
cd tg-video-downloader-bot
python -m venv venv
.\venv\Scripts\activate   # Linux/macOS 用 source venv/bin/activate
pip install -r requirements.txt
python main.py
```
启动成功后：
- Web 控制台：`http://127.0.0.1:8000`
- Bot 会自动上线（使用你提供的 Bot Token）。

## 配置
### 1) .env（必填）
复制 `.env.example` 为 `.env` 并填入：
```
BOT_TOKEN=你的BotToken
USER_API_ID=你的UserApiId
USER_API_HASH=你的UserApiHash
```

### 2) config.yaml（可选）
- 下载路径、并发、文件命名等常规项已默认配置。
- **代理默认关闭**：
```yaml
proxy: null
user_api:
  api_id: "<填在 .env>"
  api_hash: "<填在 .env>"
  proxy: null
```
- 如需开启全局/用户代理，填写：
```yaml
proxy:
  scheme: socks5   # 或 http
  hostname: 127.0.0.1
  port: 10808
  username: null
  password: null
  rdns: true
user_api:
  api_id: "<...>"
  api_hash: "<...>"
  proxy: null      # 若只想用户端走代理，可在这里填，global 仍为 null
```
也可以在 Web 控制台 “Settings & Proxy” 中保存；保存后写入 config.yaml，并同时应用到用户客户端。

## Bot 命令速览
| 命令 | 作用 |
| --- | --- |
| /start | 帮助 / 功能列表 |
| /status ( /s ) | 查看系统/下载状态 |
| /auth ( /login_status ) | 检查用户客户端登录与代理状态 |
| /login | 登录用户客户端（MTProto） |
| /dl | 查看下载队列 |
| /bd | 批量下载最近一次搜索结果 |
| /bf | 批量转发到指定聊天（会询问范围与转发后是否删除文件，删除标记为 [DEL] 前缀） |
| /csk | 渠道关键字搜索 |
| /cst | 渠道时间范围搜索 |
| /csr | 渠道最近消息 |
| /cc | 连接/切换渠道 |
| /channels | 已连接渠道列表 |

## Web 控制台
- 地址：`http://127.0.0.1:8000`
- Tab “Settings & Proxy” 可配置并保存代理（默认关闭）。保存后需重启以完全作用于 Telegram 客户端。


## GitHub Actions 打包 Windows / Linux 可执行文件
可以直接用 PyInstaller 在 GitHub Actions 里生成 Windows `.exe` 和 Linux 可执行目录。仓库里已经有统一入口 `main.py`，并且 Web 静态资源依赖 `public/` 目录，所以打包时需要把这些资源一起带上。

### 触发方式
- 手动：GitHub → **Actions** → **Build desktop binaries** → **Run workflow**
- 自动：推送 `v*` 标签时自动构建，例如 `v1.0.0`

### 产物说明
- `tg-video-downloader-windows.zip`
- `tg-video-downloader-linux.tar.gz`

解压后目录内会包含：
- 可执行程序
- `public/` 静态页面资源
- `.env.example`
- `config.yaml`（由 `config.example.yaml` 复制而来）

### 使用步骤
1. 下载对应系统的 Actions artifact。
2. 解压后，把 `.env.example` 复制为 `.env`。
3. 在 `.env` 中填写 `BOT_TOKEN`、`USER_API_ID`、`USER_API_HASH`。
4. 按需修改 `config.yaml`。
5. 运行：
   - Windows：双击 `tg-video-downloader.exe`
   - Linux：`chmod +x tg-video-downloader && ./tg-video-downloader`

### 工作流文件
工作流位于 `.github/workflows/build.yml`，使用 `pyinstaller --onedir` 构建。之所以采用 `--onedir`，是因为这个项目在运行时还要读写 `config.yaml`、`session.txt`、SQLite 数据库和下载目录，目录模式更稳定。

### 原来的启动方式还能不能用？
可以，原来的源码运行方式没有变，仍然可以继续使用 `python main.py`、`start.sh` 或 `start.ps1` 启动；这次改动主要是补充了打包构建，并把运行时文件路径改成了更稳定的“相对于程序目录”解析方式。

## 常见问题
1) **网页打不开 /502**  
   - 确认 `python main.py` 正在运行且监听 `127.0.0.1:8000`。  
   - 如端口被占用，可在 `main.py` 将 `port=8000` 改为空闲端口重新启动。
2) **需要代理才能连上 Telegram**  
   - 在 `config.yaml` 或 Web 里填好代理参数，保存后重启。
3) **登录失败**  
   - 确保 `.env` 中 USER_API_ID / HASH 正确；在 Telegram 与 Bot 对话中使用 `/login` 按提示输入验证码。

## 目录结构速览
- `core/` 配置与数据库
- `telegram/` Bot & 用户客户端、命令、搜索
- `downloader/` 下载管理队列
- `web/` FastAPI 路由与前端资源
- `public/` Web 前端静态文件

## 免责声明
本项目仅供学习与个人备份使用，请遵守 Telegram 服务条款与所在地法律法规。
