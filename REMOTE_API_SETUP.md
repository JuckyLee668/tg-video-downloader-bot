# Telegram Bot API 配置指南

本项目使用 **Telegram Bot API** 模式，通过远程 Bot API 服务器连接，不再需要本地 Telegram 客户端。

---

## 架构说明

### Bot API 模式 vs 传统模式

**传统模式**（已移除）：
- 需要本地 Telegram 客户端（如 `telegram` 库）
- 需要 `api_id` 和 `api_hash`
- 需要登录流程和 Session 管理

**Bot API 模式**（当前）：
- ✅ 使用 `node-telegram-bot-api` 库
- ✅ 只需要 `bot_token`
- ✅ 通过远程 Bot API 服务器（如 `https://rack.xi-han.top/bot-api/`）
- ✅ 文件通过公网 URL 下载

---

## 配置步骤

### 1. 基本配置

编辑 `config.yaml` 文件：

```yaml
# 远程 Telegram Bot API 服务器配置
remote_api:
  # Bot API 服务器地址（必需）
  bot_api_host: https://rack.xi-han.top/bot-api/
  # 公网文件访问基础 URL（必需，用于下载文件）
  public_file_base_url: https://rack.xi-han.top/bot-api-files
  # Telegram Bot 基础目录（可选，默认 /media/TGbot）
  tg_base_dir: /media/TGbot

# Bot Token（必需，从 @BotFather 获取）
bot_token: 你的_bot_token
```

### 2. Bot API 服务器要求

你的 Bot API 服务器需要：

1. **支持标准 Bot API 接口**：
   - `getMe` - 获取 Bot 信息
   - `getFile` - 获取文件信息
   - 接收消息（通过 webhook 或 polling）

2. **文件存储**：
   - 媒体文件存储在服务器目录（如 `/media/TGbot/<token>/videos/file_xxx`）
   - 文件路径可通过 `getFile(file_id)` 获取
   - **自建服务器**：如果文件需要先下载才能获取路径，系统会自动轮询等待

3. **公网访问**：
   - 媒体文件可以通过 HTTP/HTTPS 公网访问
   - URL 格式：`{public_file_base_url}/{relative_path}`

### 3. 文件路径处理

Bot API 返回的文件路径格式示例：`/media/TGbot/<token>/videos/file_xxx`

系统会自动：
- 移除 `tg_base_dir` 前缀（`/media/TGbot`）
- 得到相对路径：`<token>/videos/file_xxx`
- 构造公网 URL：`{public_file_base_url}/<token>/videos/file_xxx`
- 使用 HTTPS 下载文件

---

## 工作原理

### 消息监听流程

1. Bot 收到消息（包含媒体文件）
2. 提取 `file_id`（从 `msg.video.file_id` / `msg.document.file_id` 等）
3. 检查下载历史记录，如果已下载则跳过
4. 通过 API 请求队列调用 `getFile(file_id)` 获取文件路径
   - **自建服务器轮询**：如果文件路径为空，说明文件还在下载中，系统会每 3 秒轮询一次，直到获取到文件路径或超时
5. 构造公网 URL：`{public_file_base_url}/{relative_path}`
6. 使用 HTTPS 下载文件到本地
7. 下载完成后记录到历史记录

### 代码对应关系

参考代码中的关键点：

1. **Bot 初始化**：
   ```javascript
   const bot = new TelegramBot(token, {
     polling: true,
     baseApiUrl: BOT_API_HOST
   });
   ```
   本项目：在 `TelegramApiClient` 中实现

2. **文件下载**：
   ```javascript
   const fileMeta = await bot.getFile(fileId);
   const publicUrl = `${PUBLIC_FILE_BASE_URL}/${relativePath}`;
   ```
   本项目：在 `downloadMediaByFileId` 方法中实现

3. **消息监听**：
   ```javascript
   bot.on('message', async (msg) => { ... });
   ```
   本项目：在 `setupMessageListener` 方法中实现

---

## 使用方式

### 方式 1: 消息监听（推荐）

Bot 会自动监听配置的聊天中的新消息，当收到媒体文件时自动下载。

**配置聊天列表**：
```yaml
chat:
  - chat_id: -1001234567890  # 频道/群组 ID
    last_read_message_id: 0
```

### 方式 2: 通过 Bot 指令（预留）

如果配置了 `bot_token`，可以通过 Bot 指令手动下载：

- `/status` - 查看下载状态
- `/help` - 显示帮助信息

---

## 注意事项

### Bot API 限制

1. **历史消息**：
   - Bot API 不支持直接获取历史消息
   - 只能通过消息监听处理新消息
   - 如果需要处理历史消息，需要通过其他方式（如远程服务器提供的接口）

2. **文件访问**：
   - 文件必须通过公网 URL 访问
   - 确保 `public_file_base_url` 配置正确
   - 文件路径需要正确映射

3. **聊天权限**：
   - Bot 必须是被添加到聊天中的成员
   - 对于频道，Bot 需要有读取权限

### 文件下载流程

1. Bot 收到消息（包含媒体文件）
2. 提取 `file_id`
3. 调用 `getFile(file_id)` 获取文件路径
4. 构造公网 URL：`{public_file_base_url}/{relative_path}`
5. 使用 HTTPS 下载文件

---

## 故障排查

### 连接失败

- ✅ 检查 `bot_api_host` 是否正确
- ✅ 确认远程 Bot API 服务器正在运行
- ✅ 检查网络连接
- ✅ 确认 Bot Token 有效

### 文件下载失败

- ✅ 检查 `public_file_base_url` 是否正确
- ✅ 确认文件路径映射正确（`tg_base_dir` 配置）
- ✅ 检查文件是否在公网可访问
- ✅ 查看 `error.log` 中的详细错误信息

### 收不到消息

- ✅ 确认 Bot 已添加到目标聊天
- ✅ 检查 Bot 权限（需要有读取消息权限）
- ✅ 确认 `chat_id` 配置正确
- ✅ 查看控制台日志，确认消息监听器已启动

---

## 完整配置示例

```yaml
remote_api:
  bot_api_host: https://rack.xi-han.top/bot-api/
  public_file_base_url: https://rack.xi-han.top/bot-api-files
  tg_base_dir: /media/TGbot

bot_token: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz

chat:
  - chat_id: -1001234567890
    last_read_message_id: 0

media_types:
  - video
  - audio
  - document

file_formats:
  video:
    - mp4
  document:
    - pdf

save_path: ./downloads
max_download_task: 10

# 高级配置（针对自建 Bot Server）
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

---

## 高级功能

### 下载历史记录

系统会自动记录已下载的文件到 `download_history.json`：
- ✅ 自动检查：下载前检查文件是否已存在历史记录
- ✅ 自动记录：下载成功后自动记录文件信息
- ✅ 文件验证：如果本地文件已删除，会从历史记录中移除

### API 请求队列

限制同时获取文件信息的请求数，避免服务器压力过大：
- 默认最多 2 个并发请求
- 其他请求在队列中等待
- 可通过 `max_api_concurrent` 配置

### 消息限流

自动控制 Telegram 消息发送频率，避免 429 错误：
- 全局每秒/每分钟限制
- 每个聊天每秒限制
- 自动重试机制

### 自建服务器轮询

针对自建 Bot Server 的特殊处理：
- 如果文件路径为空，说明文件还在下载中
- 系统会自动轮询等待文件准备好
- 可配置轮询间隔和最大等待时间

## 优势

1. **无需本地 Telegram 客户端**：不需要在本地安装和配置 Telegram 客户端
2. **集中管理**：所有 Telegram 连接集中在远程服务器
3. **更好的安全性**：Bot Token 可以控制访问权限
4. **易于扩展**：可以轻松添加多个下载客户端
5. **智能队列管理**：自动控制并发数，避免服务器压力过大
6. **下载历史记录**：避免重复下载，节省带宽和时间

---

## 参考

- [Telegram Bot API 文档](https://core.telegram.org/bots/api)
- [node-telegram-bot-api 文档](https://github.com/yagop/node-telegram-bot-api)
