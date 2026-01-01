# tg-video-downloader-bot

一个基于 Node.js 的 Telegram 机器人，专为下载和归档用户发送的视频文件而设计。它通过连接**自建的 Telegram Bot API 服务器**，突破官方 API 的文件大小限制，实现对大视频文件（如超过 1GB）的稳定下载。

## 🛠️ 技术栈与解决的关键问题

*   **运行时**：Node.js
*   **核心库**：`node-telegram-bot-api` (用于 Bot 通信)、`axios` (用于流式下载文件)
*   **关键技术点**：
    1.  **自建 API 服务器集成**：配置 Bot 连接至本地 `telegram-bot-api` 实例。
    2.  **文件路径修正**：解决自建服务器返回非常规文件路径导致的 404 问题。
    3.  **流式下载与进度监控**：实现大文件的稳定下载并实时反馈进度。

## 📦 快速开始

1.  **克隆项目**
    ```bash
    git clone https://github.com/JuckyLee668/tg-video-downloader-bot.git
    cd tg-video-downloader-bot
    ```

2.  **安装依赖**
    ```bash
    npm install
    ```

3.  **环境配置**
    复制 `.env.example` 文件为 `.env`，并填入你的配置：
    ```env
    # 你的 Telegram Bot Token (从 @BotFather 获取)
    BOT_TOKEN=YOUR_BOT_TOKEN_HERE
    # 你的自建 Bot API 服务器地址 (例如: http://127.0.0.1:9081)
    BOT_API_HOST=http://127.0.0.1:9081
    # 本地下载保存目录
    DOWNLOAD_DIR=./downloads/
    ```

4.  **运行**
    ```bash
    npm start
    ```

## 🚀 高级配置

确保你的自建 `telegram-bot-api` 服务器已正确启动，并设置了足够大的 `--max-file-size` 参数以支持大文件下载。

## 📄 许可证

本项目采用 [MIT](LICENSE) 许可证。

---

希望这些建议能帮到你！如果你选定了名字，或者需要对介绍内容进行调整，随时可以告诉我。
