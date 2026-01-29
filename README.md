# Telegram Media Downloader (Node.js · Bot API 模式)

基于 Node.js 的 Telegram 媒体下载器，通过 **远程 Telegram Bot API 服务器** (查看[编译telegram-bot-api并使用](https://github.com/JuckyLee668/telegram-bot-api))接收消息并下载媒体文件。

## 功能特性

- ✅ **多媒体类型支持**：音频、文档、照片、视频、语音、动画
- ✅ **Bot API 集成**：通过 `node-telegram-bot-api` 连接远程 Bot API 服务器
- ✅ **消息监听下载**：Bot 收到新媒体消息后自动加入下载队列
- ✅ **Telegram 消息通知**：接收和完成时通过 Telegram 发送消息通知
- ✅ **文件格式过滤**：支持按媒体类型和文件格式过滤
- ✅ **自定义路径命名**：支持按频道名、日期、媒体类型分目录
- ✅ **智能队列管理**：
  - 下载队列：消息先暂存，按并发数顺序处理
  - API 请求队列：限制同时获取文件信息的请求数，避免服务器压力过大
- ✅ **下载历史记录**：自动记录已下载文件，避免重复下载
- ✅ **消息限流**：自动控制 Telegram 消息发送频率，避免 429 错误
- ✅ **自建服务器支持**：针对自建 Bot Server 的轮询机制，等待文件下载完成
- ✅ **并发控制**：可配置最大并发下载任务数和 API 请求数
- ✅ **私聊支持**：用户可直接发送媒体文件给 Bot 自动下载
- ✅ **转发队列管理**：智能处理转发消息，避免无限重启循环
- ✅ **断点续传与重新下载**：支持断点续传和强制重新开始下载
- ✅ **用户通知增强**：确保从配置的聊天和转发队列中启动的任务都能收到通知

---

## 安装

```bash
cd downloader
npm install
```

**要求**：Node.js 18+

---

## 快速配置

### 1. 准备 Bot API 环境

你需要一个已部署的 **Telegram Bot API 服务器**，并满足：

- 支持标准 Bot API 接口（`getMe` / `getFile` / 接收消息等）
- 媒体文件存储在服务器目录（如 `/media/TGbot/...`）
- 有公网 HTTP 入口可访问这些媒体文件

### 2. 准备 Bot 信息

1. 在 Telegram 中通过 **@BotFather** 创建一个 Bot，获取 `Bot Token`
2. 确保你的 Bot 已经：
   - 被加入到需要下载的频道 / 群组 / 私聊里
   - 拥有读取消息和媒体的权限

### 3. 配置敏感信息（推荐使用 .env 文件）

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

### 4. 配置 `config.yaml`

编辑 `config.yaml`，配置其他非敏感信息（如聊天列表、媒体类型等）：

```yaml
# 要监听的聊天列表
# 注意：用户直接发给 Bot 的私聊消息会自动处理，无需在此配置
chat:
  - chat_id: -1001234567890   # 频道/群组 ID
    last_read_message_id: 0
    # 可选：按日期过滤
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

# 是否始终重新开始下载（而非断点续传）
# 当设置为 true 时，不管之前的下载进度如何，都重新开始下载
always_fresh_download: false

# 并发下载
max_download_task: 10

# 远程 API 高级配置（可选）
remote_api:
  # 单次请求超时时间（毫秒，默认 30000，即 30 秒）
  single_request_timeout: 30000
  # 最大并发 API 请求数（默认 2）
  # 限制同时获取文件信息的请求数，避免服务器压力过大
  max_api_concurrent: 2
  # 文件轮询间隔（毫秒，默认 3000，即 3 秒）
  # 自建 Bot Server 需要先下载文件才能获取路径
  file_poll_interval: 3000
  # 最大轮询时间（毫秒，默认 300000，即 5 分钟）
  # 对于大文件，可以增加到 30 分钟（1800000）
  max_poll_time: 300000
```

> **重要提示**：
> - 敏感信息（`bot_token`、`bot_api_host`、`public_file_base_url`）建议放在 `.env` 文件中
> - 如果 `.env` 文件存在，环境变量会优先覆盖 `config.yaml` 中的配置
> - `.env` 文件已在 `.gitignore` 中，不会被提交到版本控制

> **注意**：`.env` 文件中的配置会覆盖 `config.yaml` 中的配置。建议将敏感信息放在 `.env` 文件中，避免提交到版本控制系统。

### 5. 启动服务

```bash
npm start
```

启动后会：
- 连接远程 Bot API，验证 Bot 是否可用
- 注册消息监听器，开始监听配置的 `chat_id` 中的新媒体消息
- 监听用户直接发给 Bot 的私聊消息（如果启用）
- 处理转发队列中的下载任务

如果看到以下输出，说明启动成功：

```
✓ 远程 Telegram Bot API 连接成功
消息监听器已启动（支持私聊和配置的聊天）
✓ Telegram Media Downloader 启动成功
```

---

## 工作原理

### 架构说明

**Bot API 模式 vs 传统模式**

**传统模式**（已移除）：
- 需要本地 Telegram 客户端（如 `telegram` 库）
- 需要 `api_id` 和 `api_hash`
- 需要登录流程和 Session 管理

**Bot API 模式**（当前）：
- ✅ 使用 `node-telegram-bot-api` 库
- ✅ 只需要 `bot_token`
- ✅ 通过远程 Bot API 服务器（如 `https://rack.xi-han.top/bot-api/`）
- ✅ 文件通过公网 URL 下载

### 消息监听流程

1. **Bot API 连接**：使用 `node-telegram-bot-api` 连接到远程 Bot API 服务器（启用 polling 模式）
2. **消息监听**：Bot 收到媒体消息时自动触发下载流程
   - ✅ 配置的频道/群组中的消息
   - ✅ **用户直接发给 Bot 的私聊消息**（默认启用）
   - ✅ 转发的消息（包括私聊转发和群组转发）
3. **文件下载**：
   - 提取消息中的 `file_id`
   - 检查下载历史记录，如果已下载则跳过
   - 通过 API 请求队列调用 `getFile(file_id)` 获取文件路径
   - **自建服务器轮询**：如果文件路径为空，说明文件还在下载中，每 3 秒轮询一次直到获取到路径
   - 构造公网 URL：`{public_file_base_url}/{relative_path}`
   - 使用 HTTPS 下载到本地 `save_path`
   - 下载完成后记录到历史记录
4. **消息通知**（带限流）：
   - 接收到文件时：发送 "📥 已收到文件：{文件名}\n正在下载中..."
   - 下载完成时：发送 "✅ 下载完成：{文件名}\n📁 保存路径：{路径}"
   - 下载失败时：发送 "❌ 下载失败：{文件名}"
   - 自动限流：控制消息发送频率，避免触发 Telegram API 429 错误
5. **转发队列管理**：
   - 智能处理转发队列中的下载任务
   - 避免无限重启循环
   - 支持从队列立即开始下载

Bot API 返回的文件路径格式：`/media/TGbot/<token>/videos/file_xxx`

系统会自动：
- 移除 `tg_base_dir` 前缀（`/media/TGbot`）
- 得到相对路径：`<token>/videos/file_xxx`
- 构造公网 URL：`{public_file_base_url}/<token>/videos/file_xxx`
- 使用 HTTPS 下载文件

---

## 配置说明

### 媒体类型 (`media_types`)

控制哪些类型的媒体会被下载：

```yaml
media_types:
  - video
  - document
  - photo
  - audio
  - voice
  - animation
```

### 文件格式过滤 (`file_formats`)

例如只保存 MP4 和 PDF：

```yaml
file_formats:
  video:
    - mp4
  document:
    - pdf
```

### 重新下载选项 (`always_fresh_download`)

控制是否始终重新开始下载（而非断点续传）：

```yaml
# 是否始终重新开始下载（而非断点续传）
# 当设置为 true 时，不管之前的下载进度如何，都重新开始下载
always_fresh_download: false
```

### 目录与文件名规则

- **`file_path_prefix`** 决定子目录结构：
  - `chat_title`：频道/群组标题
  - `media_datetime`：消息时间（按 `date_format` 格式化）
  - `media_type`：视频/图片/文档等

- **`file_name_prefix`** 决定文件名前缀：
  - `message_id`：消息 ID
  - `file_name`：Telegram 文件名
  - `caption`：消息文本（会被清洗为合法文件名）

示例结构：
```
./downloads/
  频道名称/
    2024_01/
      12345 - 原始文件名.mp4
```

### 私聊消息处理

用户可以直接发送媒体文件给 Bot，Bot 会自动下载：

- ✅ 默认启用（`enable_private_chat: true`）
- ✅ 支持所有配置的媒体类型
- ✅ Bot 会发送接收和完成消息
- ✅ 如果发送的不是媒体文件，Bot 会提示支持的媒体类型

要禁用私聊处理，设置：

```yaml
enable_private_chat: false
```

### 下载过滤器 (`download_filter`)

支持按日期区间过滤：

```yaml
chat:
  - chat_id: -1001234567890
    download_filter: message_date >= 2024-01-01 00:00:00 and message_date <= 2024-12-31 23:59:59
```

> **注意**：
> - Bot API 不支持直接获取历史消息，此过滤器主要用于实时收到的新消息
> - 下载过滤器仅应用于配置的频道/群组，不应用于私聊消息

### 下载历史记录

系统会自动记录已下载的文件到 `download_history.json`，避免重复下载：

- ✅ 自动检查：下载前检查文件是否已存在历史记录
- ✅ 自动记录：下载成功后自动记录文件信息
- ✅ 文件验证：如果本地文件已删除，会从历史记录中移除

### 转发队列管理

系统智能管理转发队列，避免无限重启循环：

- ✅ 检测已完成但仍在队列中的任务
- ✅ 定期清理和检查转发队列
- ✅ 合理设置重启阈值（默认2小时）

### 并发控制配置

```yaml
# 最大并发下载任务数（建议 10-20）
max_download_task: 10

remote_api:
  # 最大并发 API 请求数（建议 1-3）
  # 限制同时获取文件信息的请求数，避免服务器压力过大
  max_api_concurrent: 2
```

### 自建 Bot Server 配置

如果你的 Bot Server 需要先下载文件才能获取路径，可以调整轮询参数：

```yaml
remote_api:
  # 单次请求超时时间（毫秒）
  # 降低此值可以更快失败并继续轮询
  single_request_timeout: 30000

  # 轮询间隔（毫秒）
  # 文件路径为空时，等待此时间后再次尝试
  file_poll_interval: 3000

  # 最大轮询时间（毫秒）
  # 对于大文件，可以增加到 30 分钟（1800000）
  max_poll_time: 300000
```

---

## 使用方式

### 方式 1: 配置的频道/群组

在配置的 `chat_id` 中发送媒体文件，Bot 会自动下载。

### 方式 2: 私聊（推荐）

直接发送媒体文件给 Bot，Bot 会：
- 发送接收消息："📥 已收到文件：{文件名}\n正在下载中..."
- 下载完成后发送："✅ 下载完成：{文件名}\n📁 保存路径：{路径}"

### 方式 3: 转发消息

转发包含媒体的消息给 Bot，Bot 会自动识别并下载。

### 方式 4: 通过 Bot 指令

如果配置了 `bot_token`，可以通过 Bot 指令手动下载：

- `/status` - 查看下载状态
- `/help` - 显示帮助信息

---

## 验证下载

下载的文件会保存在 `save_path` 目录下，按照配置的路径规则组织：

```
./downloads/
  频道名称/
    2024_01/
      12345 - 原始文件名.mp4
```

---

## 获取 Chat ID

### 方法 1: 使用 Web Telegram

1. 打开 https://web.telegram.org
2. 进入目标频道/群组
3. 从 URL 中提取 chat_id
4. 对于频道/群组，通常需要添加 `-100` 前缀

### 方法 2: 使用 Bot

使用 @username_to_id_bot 获取 chat_id

---

## 项目结构

```
downloader/
├── src/
│   ├── index.js              # 主入口：初始化配置、Bot API 客户端、下载管理器
│   ├── telegramApiClient.js  # 封装 node-telegram-bot-api + 文件下载逻辑 + 轮询机制
│   ├── downloadManager.js    # 下载队列 + 并发控制 + 命名规则 + 断点续传
│   ├── downloadHistory.js   # 下载历史记录管理
│   ├── forwardedQueue.js    # 转发队列管理
│   ├── apiRequestQueue.js   # API 请求队列管理器
│   ├── messageRateLimiter.js # 消息发送限流器
│   ├── configManager.js      # 加载/更新 config.yaml
│   └── botHandler.js         # Bot 指令处理（/status 等）
├── config.yaml               # 主配置文件
├── README.md                 # 本文档
├── QUICKSTART.md            # 快速开始指南
├── BOT_API_GUIDE.md         # Bot API 详细说明
├── REMOTE_API_SETUP.md     # 远程 API 配置说明
└── package.json
```

---

## 注意事项

- **Bot 权限**：Bot 必须被加入目标频道/群组，并具有读取消息和媒体的权限
- **历史消息**：当前实现主要针对"Bot 收到的新消息"；批量历史消息下载需要额外服务支持
- **公网访问**：`public_file_base_url` 必须能在下载服务器上正常访问
- **磁盘空间**：大文件下载前请确认 `save_path` 盘空间充足
- **Bot API 限制**：
  - 历史消息：Bot API 不支持直接获取历史消息
    - 只能通过消息监听处理新消息
    - 如果需要处理历史消息，需要通过其他方式（如远程服务器提供的接口）
  - 文件访问：文件必须通过公网 URL 访问，确保 `public_file_base_url` 配置正确
  - 聊天权限：Bot 必须是被添加到聊天中的成员，对于频道，Bot 需要有读取权限

---

## 常见问题

### Q: 看不到任何下载任务？

- 确认 `chat.chat_id` 配置正确（注意 `-100` 前缀）
- 确认 Bot 已加入对应频道/群组
- 确认在该聊天中发送了媒体消息
- 查看 `error.log` 是否有错误信息

### Q: 文件下载失败？

- 确认 `public_file_base_url` 能在浏览器中直接访问测试文件
- 确认 `tg_base_dir` 与远程服务器实际目录一致
- 查看控制台和 `error.log` 的错误详情

### Q: 如何限制下载格式？

在 `file_formats` 中只保留需要的扩展名：

```yaml
file_formats:
  video:
    - mp4
  document:
    - pdf
```

### Q: 转发消息无法下载？

- 确认转发的消息包含媒体文件
- 检查媒体类型是否在 `media_types` 配置中
- 查看日志中的详细错误信息

### Q: 获取文件信息超时？

- **自建 Bot Server**：文件可能需要先下载才能获取路径，系统会自动轮询等待
- 如果经常超时，可以：
  - 降低 `max_api_concurrent` 到 1
  - 增加 `single_request_timeout` 到 60 秒
  - 增加 `max_poll_time` 到 30 分钟（大文件）
  - 增加 `file_poll_interval` 到 5 秒（减少服务器压力）

### Q: 收到 429 错误（Too Many Requests）？

- 系统已内置消息限流器，会自动重试
- 如果仍然出现，可以降低消息发送频率
- 检查是否有其他程序同时使用同一个 Bot Token

### Q: 如何强制重新开始下载？

在 `config.yaml` 中设置：
```yaml
# 是否始终重新开始下载（而非断点续传）
always_fresh_download: true
```

### Q: 下载完成后没有收到通知？

- 确认 `enable_private_chat` 为 `true`（对于私聊）
- 确认配置的聊天 ID 正确
- 检查 Bot 是否有权限向指定聊天发送消息

### Q: Bot 没有响应？

- 确认 `bot_token` 配置正确
- 确认 Bot 已加入目标频道/群组
- 查看 `error.log` 是否有错误信息

### Q: 收不到消息？

- 确认 Bot 已添加到目标聊天
- 检查 Bot 权限（需要有读取消息权限）
- 确认 `chat_id` 配置正确
- 查看控制台日志，确认消息监听器已启动

### Q: 连接失败？

- 检查 `bot_api_host` 是否正确
- 确认远程 Bot API 服务器正在运行
- 检查网络连接
- 确认 Bot Token 有效

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

---

## 优势

1. **无需本地 Telegram 客户端**：不需要在本地安装和配置 Telegram 客户端
2. **集中管理**：所有 Telegram 连接集中在远程服务器
3. **更好的安全性**：Bot Token 可以控制访问权限
4. **易于扩展**：可以轻松添加多个下载客户端
5. **智能队列管理**：自动控制并发数，避免服务器压力过大
6. **下载历史记录**：避免重复下载，节省带宽和时间

---

## 开发计划

- [x] 下载历史记录（已实现）
- [x] 消息限流（已实现）
- [x] API 请求队列（已实现）
- [x] 自建服务器轮询支持（已实现）
- [x] 断点续传与重新下载（已实现）
- [x] 转发队列管理（已实现）
- [x] 用户通知增强（已实现）
- [ ] 完善云盘上传功能（rclone/aligo）
- [ ] 添加更多下载过滤器选项
- [ ] 支持批量操作

---

## 相关文档

- **[QUICKSTART.md](./QUICKSTART.md)** - 快速开始指南
- **[BOT_API_GUIDE.md](./BOT_API_GUIDE.md)** - Bot API 详细说明
- **[REMOTE_API_SETUP.md](./REMOTE_API_SETUP.md)** - 远程 API 配置说明

---

## 参考

- [Telegram Bot API 文档](https://core.telegram.org/bots/api)
- [node-telegram-bot-api 文档](https://github.com/yagop/node-telegram-bot-api)
- 基于 [telegram_media_downloader](https://github.com/tangyoha/telegram_media_downloader) 项目，使用 Node.js 重新实现，采用 Telegram Bot API 模式。

---

## 许可证

MIT License
