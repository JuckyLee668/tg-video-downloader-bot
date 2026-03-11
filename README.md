# 🤖 Telegram Media Downloader Bot

一个功能强大的 Telegram 媒体下载机器人，结合了 **MTProto 高速下载** 和 **Web 可视化管理面板**。支持批量下载不可转发频道媒体、关键词搜索、历史记录。

## ✨ 主要特性

*   🚀 **高速下载**: 利用 Telethon 的并行分片逻辑，相比标准下载提升数倍速度。
*   📊 **Web 控制面板**: 现代化的前端界面，支持实时下载队列管理、状态监控。
*   🔍 **深度搜索**: 支持关键词搜索、时间段筛选、获取最近媒体，并支持一键批量下载。
*   🔄 **多端同步**: Web 界面与 Telegram Bot 指令（`/dl`, `/status`）进度完美同步。
*   📂 **自动归档**: 下载成功的媒体会自动记录到历史库，支持搜索和查看。
*   🛠️ **代理支持**: 支持 HTTP 和 SOCKS5 代理。

## 🛠️ 技术栈

*   **Backend**: Python (FastAPI, Telethon, AioSqlite)
*   **Database**: SQLite (WAL 模式)
*   **Frontend**: HTML, Vanilla JS, Tailwind CSS
*   **Logging**: Loguru

## 🚀 快速开始

### 1. 环境准备
确保您的机器已安装 Python 3.8+。

### 2. 克隆并安装
```bash
git clone https://github.com/your-repo/tg-video-downloader-bot.git
cd app
pip install -r requirements.txt
```

### 3. 配置
复制 `.env.example` 为 `.env` 并填入您的 API 信息：
```env
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
```

同时可以在 `config.yaml` 中调整下载路径和并发数。

### 4. 运行
```bash
python main.py
```
运行后，Bot 将在 Telegram 上线，同时 Web 界面默认通过 `http://localhost:8000` 访问。

## 🤖 常用指令

*   `/start` - 开始并查看帮助
*   `/dl` - 查看下载队列和进度
*   `/status` - 系统状态监控
*   `/cc` - 连接到目标频道
*   `/csk` - 搜索频道关键词
*   `/bd` - 批量下载搜索结果
*   `/login` - 登录用户账号（MTProto 下载必需）

## 📦 目录结构

*   `core/`: 核心数据库和配置管理
*   `downloader/`: 下载引擎和队列管理器
*   `telegram/`: Bot 处理器、搜索逻辑和客户端管理
*   `web/`: REST API 和 Web 服务器
*   `public/`: 前端静态文件

## ⚖️ 免责声明
本工具仅供学习和个人备份使用，请遵守 Telegram 的服务条款及当地法律法规。
