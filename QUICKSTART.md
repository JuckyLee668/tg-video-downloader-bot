# 快速开始指南（Bot API 模式）

本指南假设你已经有一个运行中的 **Telegram Bot API 服务器**（例如 `https://rack.xi-han.top/bot-api/`），并且 Bot 已经能正常收发消息、保存大文件到本地磁盘。

---

## 1. 安装依赖

```bash
cd downloader
npm install
```

**要求**：Node.js 18+

---

## 2. 准备 Bot 信息

1. 在 Telegram 中通过 **@BotFather** 创建一个 Bot，获取 `Bot Token`
2. 确保你的 Bot 已经：
   - 被加入到需要下载的频道 / 群组 / 私聊里
   - 拥有读取消息和媒体的权限

---

## 3. 配置敏感信息

**推荐使用 .env 文件**（更安全，不会被提交到版本控制）：

```bash
cp .env.example .env
```

编辑 `.env` 文件，填写你的敏感信息：

```env
# Telegram Bot Token
BOT_TOKEN=你的_bot_token

# 远程 Telegram Bot API 服务器配置
BOT_API_HOST=https://rack.xi-han.top/bot-api/
PUBLIC_FILE_BASE_URL=https://rack.xi-han.top/bot-api-files
TG_BASE_DIR=/media/TGbot
```

> **注意**：如果 `.env` 文件存在，系统会优先使用 `.env` 中的配置，覆盖 `config.yaml` 中的配置。

## 4. 配置 `config.yaml`

打开项目根目录下的 `config.yaml`，配置其他非敏感信息：

# 要监听的聊天列表
# 注意：用户直接发给 Bot 的私聊消息会自动处理，无需在此配置
chat:
  - chat_id: -1001234567890
    last_read_message_id: 0
    # 按日期过滤（可选）
    download_filter: message_date >= 2024-01-01 00:00:00 and message_date <= 2024-12-31 23:59:59

# 是否处理用户直接发给 Bot 的私聊消息（默认 true）
enable_private_chat: true

# 媒体类型
media_types:
  - video
  - document
  - photo
  - audio
  - voice
  - animation

# 文件格式过滤
file_formats:
  video:
    - mp4
  document:
    - pdf
    - epub

# 保存路径
save_path: ./downloads

# 并发下载
max_download_task: 10

# 远程 API 高级配置（可选，针对自建 Bot Server）
remote_api:
  # 单次请求超时时间（毫秒）
  single_request_timeout: 30000
  # 最大并发 API 请求数
  max_api_concurrent: 2
  # 文件轮询间隔（毫秒）
  file_poll_interval: 3000
  # 最大轮询时间（毫秒，大文件可增加到 30 分钟）
  max_poll_time: 300000
```

> **提示**：你当前的环境中，`bot_api_host` 和 `public_file_base_url` 已经配置为 `rack.xi-han.top`，只需确认路径与远程服务器一致即可。

---

## 5. 启动服务

```bash
npm start
```

启动后会：

- ✅ 连接远程 Bot API，验证 Bot 是否可用
- ✅ 注册 Bot 消息监听器，开始监听配置的 `chat_id` 中的新媒体消息
- ✅ 监听用户直接发给 Bot 的私聊消息（如果启用）

如果看到以下输出，说明启动成功：

```
✓ 远程 Telegram Bot API 连接成功
消息监听器已启动（支持私聊和配置的聊天）
✓ Telegram Media Downloader 启动成功
```

---

## 6. 使用方式

### 方式 1: 配置的频道/群组

在配置的 `chat_id` 中发送媒体文件，Bot 会自动下载。

### 方式 2: 私聊（推荐）

直接发送媒体文件给 Bot，Bot 会：
- 发送接收消息："📥 已收到文件：{文件名}\n正在下载中..."
- 下载完成后发送："✅ 下载完成：{文件名}\n📁 保存路径：{路径}"

### 方式 3: 转发消息

转发包含媒体的消息给 Bot，Bot 会自动识别并下载。

---

## 7. 验证下载

下载的文件会保存在 `save_path` 目录下，按照配置的路径规则组织：

```
./downloads/
  频道名称/
    2024_01/
      12345 - 原始文件名.mp4
```

---

## 常见问题

### Q: Bot 没有响应？

- 确认 `bot_token` 配置正确
- 确认 Bot 已加入目标频道/群组
- 查看 `error.log` 是否有错误信息

### Q: 文件下载失败？

- 确认 `public_file_base_url` 能在浏览器中直接访问
- 确认 `tg_base_dir` 与远程服务器实际目录一致
- 查看控制台和 `error.log` 的错误详情

### Q: 获取文件信息超时？

- **自建 Bot Server**：文件可能需要先下载才能获取路径
- 系统会自动轮询等待文件准备好
- 如果经常超时，可以调整 `remote_api` 中的轮询参数

### Q: 如何获取 Chat ID？

1. 使用 Web Telegram：打开 https://web.telegram.org，进入目标频道/群组，从 URL 中提取 chat_id
2. 使用 Bot：使用 @username_to_id_bot 获取 chat_id

---

## 下一步

- 查看 **[README.md](./README.md)** 了解详细配置选项
- 查看 **[BOT_API_GUIDE.md](./BOT_API_GUIDE.md)** 了解 Bot API 工作原理
- 查看 **[REMOTE_API_SETUP.md](./REMOTE_API_SETUP.md)** 了解远程 API 配置

---

## 相关文档

- **[README.md](./README.md)** - 完整文档
- **[BOT_API_GUIDE.md](./BOT_API_GUIDE.md)** - Bot API 详细说明
- **[REMOTE_API_SETUP.md](./REMOTE_API_SETUP.md)** - 远程 API 配置说明
