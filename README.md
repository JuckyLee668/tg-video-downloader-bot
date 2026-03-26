# Telegram Media Downloader Bot

基于 **Telethon + FastAPI** 的多端媒体下载器，提供 Telegram Bot 指令与 Web 控制台，支持批量下载 / 转发、关键字与时间范围搜索，并可按需启用 HTTP/SOCKS5 代理。

## 功能亮点
- 高并发下载：自适应并发队列，支持断点、批量任务。
- 双端控制：Bot 命令 + Web 界面实时查看/管理任务。
- 搜索增强：按关键字、时间区间或最近消息筛选，并可一键批量下载/转发。
- 账号双客户端：Bot 客户端 + 用户（MTProto）客户端，分离权限更安全。
- 代理支持：可为全局或用户客户端配置 HTTP / SOCKS5 代理（默认关闭）。

## 使用方法
- 直接转发视频或其他文件到Bot后会自动下载。
- 可以在Bot内使用命令连接群组并选择下载或转发（如果不能直接转发会下载并转发，要注意非Premium只能转发大小为2GB内文件）。
- 可以在Web 控制台：`http://127.0.0.1:8000`连接群组然后下载或下载并转发。

## 环境要求
- Python 3.8+
- 可访问 Telegram 的网络（如需代理可在配置中开启）

## 安装与启动
###  1）先配置.env（必填）
复制 `.env.example` 为 `.env` 并填入：
BOT_TOKEN、API_ID 和 API_HASH （获取方法可以查看下文的常见问题的第4、5条）
```
BOT_TOKEN=你的BotToken
USER_API_ID=你的UserApiId
USER_API_HASH=你的UserApiHash
```
### 2）使用一键启动脚本（该脚本会检查配置文件、创建虚拟环境、安装依赖后启动）
#### Linux
```bash
git clone https://github.com/your-repo/tg-video-downloader-bot.git
cd tg-video-downloader-bot
chmod +x start.sh 
./start.sh
```
#### windows
```
 .\start.ps1
```
启动成功后：
- Web 控制台：`http://127.0.0.1:8000`
- Bot 会自动上线（使用你提供的 Bot Token）。

## 配置
### 1) config.yaml（可选）
- 下载路径、并发、文件命名等常规项已默认配置。
- `allowed_user_ids` 用于控制 Bot 命令权限（推荐按下面三种模式配置）：
  - **模式 A：不限制（仅建议本地测试）**
```yaml
allowed_user_ids: []
```
  - **模式 B：仅允许当前 user client 账号（推荐个人使用）**
```yaml
allowed_user_ids:
  - me
```
  - **模式 C：仅允许指定账号（推荐多人协作）**
```yaml
allowed_user_ids:
  - "123456789"
  - "@alice"
```
  - 说明：`me` 表示“当前已登录的 user client 账号”。若首次使用，请先在 Bot 私聊里执行 `/login` 完成初始化。
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

## 常见问题
1) **网页打不开 /502**  
   - 确认 `python main.py` 正在运行且监听 `127.0.0.1:8000`。  
   - 如端口被占用，可在 `main.py` 将 `port=8000` 改为空闲端口重新启动。
2) **需要代理才能连上 Telegram**  
   - 在 `config.yaml` 或 Web 里填好代理参数，保存后重启。
3) **登录失败**  
   - 确保 `.env` 中 USER_API_ID / HASH 正确；在 Telegram 与 Bot 对话中使用 `/login` 按提示输入验证码。
4) **获取BOT_TOKEN（ 使用Telegram客户端申请）**
   - 添加好友 @BotFather。
   - 输入【 /start 】 -【 /newbot 】，给新机器人自定义起名，必须以bot结尾，不能和别人重复。
   - 起名新建成功后会输出Use this token to access the HTTP API，就是你这个机器人的Token。
5) **获取API_ID和API_HASH（通过官方方式申请）**
   - 访问申请页面:打开浏览器进入 `my.telegram.org`,后使用你的 Telegram 账号登录。
   - 创建应用：登录后选择 API development tools。填写 App title、Short name、平台类型等信息。点击 Create application 提交。
   - 获取凭证：创建成功后，页面会显示 API ID 和 API Hash。

## 免责声明
本项目仅供学习与个人备份使用，请遵守 Telegram 服务条款与所在地法律法规。
