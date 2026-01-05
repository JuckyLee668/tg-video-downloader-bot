# Telegram Bot API 使用指南

本项目已改为使用 Telegram Bot API 模式，通过远程 Bot API 服务器连接。

## 配置说明

### 1. 基本配置

在 `config.yaml` 中配置：

```yaml
remote_api:
  # Bot API 服务器地址（远程 Bot API 服务）
  bot_api_host: https://rack.xi-han.top/bot-api/
  # 公网文件访问基础 URL（用于下载文件）
  public_file_base_url: https://rack.xi-han.top/bot-api-files
  # Telegram Bot 基础目录（可选，默认 /media/TGbot）
  tg_base_dir: /media/TGbot

bot_token: your_bot_token
```

### 2. 工作原理

1. **Bot API 连接**：使用 `node-telegram-bot-api` 库连接到远程 Bot API 服务器
2. **消息监听**：通过监听消息事件接收新消息
3. **文件下载**：通过公网文件 URL 下载文件

### 3. 文件路径处理

Bot API 返回的文件路径格式：`/media/TGbot/<token>/videos/file_xxx`

系统会自动：
- 移除 `tg_base_dir` 前缀
- 构造公网可访问的 URL
- 使用 HTTPS 下载文件

## 使用方式

### 方式 1: 消息监听（推荐）

Bot 会自动监听配置的聊天中的新消息，当收到媒体文件时自动下载。

**配置聊天列表**：
```yaml
chat:
  - chat_id: -1001234567890  # 频道/群组 ID
    last_read_message_id: 0
```

### 方式 2: 通过 Bot 指令

如果配置了 `bot_token`，可以通过 Bot 指令手动下载：

- `/download <file_id>` - 下载指定文件

## 注意事项

### Bot API 限制

1. **历史消息**：Bot API 不支持直接获取历史消息
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
3. 检查下载历史记录，如果已下载则跳过
4. 通过 API 请求队列调用 `getFile(file_id)` 获取文件路径
   - **自建服务器轮询**：如果文件路径为空，每 3 秒轮询一次直到获取到路径
5. 构造公网 URL：`{public_file_base_url}/{relative_path}`
6. 使用 HTTPS 下载文件
7. 下载完成后记录到历史记录

## 故障排查

### 连接失败

- 检查 `bot_api_host` 是否正确
- 确认远程 Bot API 服务器正在运行
- 检查网络连接
- 确认 Bot Token 有效

### 文件下载失败

- 检查 `public_file_base_url` 是否正确
- 确认文件路径映射正确
- 检查文件是否在公网可访问
- **自建服务器**：如果获取文件信息超时，系统会自动轮询等待文件下载完成
- 查看日志中的详细错误信息

### 收不到消息

- 确认 Bot 已添加到目标聊天
- 检查 Bot 权限
- 确认 `chat_id` 配置正确

## 示例

### 完整的配置文件

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

save_path: ./downloads
max_download_task: 10

# 高级配置（针对自建 Bot Server）
remote_api:
  single_request_timeout: 30000
  max_api_concurrent: 2
  file_poll_interval: 3000
  max_poll_time: 300000
```

## 与参考代码的对应关系

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
