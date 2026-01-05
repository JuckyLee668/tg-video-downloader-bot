export class BotHandler {
  constructor(config, downloadManager, logger, apiClient, messageRateLimiter = null) {
    this.config = config;
    this.downloadManager = downloadManager;
    this.logger = logger;
    this.apiClient = apiClient;
    this.messageRateLimiter = messageRateLimiter;
    this.bot = null;
    this.statusMessages = new Map(); // 存储状态消息，用于更新
  }

  async init() {
    if (!this.config.bot_token || this.config.bot_token === 'your_bot_token') {
      this.logger.warn('Bot token 未配置，跳过 Bot 初始化');
      return;
    }

    // 使用主 Bot 实例（已经在 TelegramApiClient 中创建）
    this.bot = this.apiClient.getBot();

    // 设置命令处理
    this.setupCommands();

    // 监听下载进度更新
    this.setupProgressListener();

    this.logger.info('Telegram Bot 命令处理器已启动');
  }

  /**
   * 发送消息（带限流保护）
   */
  async sendMessage(chatId, text, options = {}) {
    if (this.messageRateLimiter) {
      return await this.messageRateLimiter.sendMessage(this.bot, chatId, text, options);
    } else {
      return await this.bot.sendMessage(chatId, text, options);
    }
  }

  setupCommands() {
    // /start 命令
    this.bot.onText(/\/start/, async (msg) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }

      const helpText = `🤖 *Telegram Media Downloader*

📥 *可用命令：*
/forward <链接> - 转发并下载指定链接的消息
/status - 查看下载状态
/help - 显示帮助信息

💡 *提示：* 直接发送媒体文件或转发消息也可以自动下载！`;
      
      await this.sendMessage(msg.chat.id, helpText, { parse_mode: 'Markdown' });
    });

    // /forward 命令 - 解析 Telegram 链接并下载
    this.bot.onText(/\/forward\s+(.+)/, async (msg, match) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }

      const link = match[1].trim();
      const chatId = msg.chat.id;

      try {
        // 解析 Telegram 链接
        const parsed = this.parseTelegramLink(link);
        if (!parsed) {
          return this.sendMessage(chatId, 
            '❌ 无效的 Telegram 链接格式\n\n' +
            '支持的格式：\n' +
            '• https://t.me/c/1518902671/22987\n' +
            '• https://t.me/channel_name/123'
          );
        }

        // 发送初始状态消息
        const status = this.downloadManager.getStatus();
        const statusMsg = await this.sendMessage(chatId, 
          this.formatStatusMessage(status),
          { parse_mode: 'Markdown' }
        );
        
        // 注册状态消息以便自动更新
        this.registerStatusMessage(chatId, statusMsg.message_id);

        // 尝试获取消息并下载
        // 注意：Bot API 不支持直接通过链接获取消息，需要通过其他方式
        // 这里先返回提示
        await this.sendMessage(chatId,
          `⚠️ Bot API 不支持直接通过链接获取历史消息\n\n` +
          `已解析链接：\n` +
          `频道ID: ${parsed.chatId}\n` +
          `消息ID: ${parsed.messageId}\n\n` +
          `💡 建议：直接转发该消息给 Bot 即可自动下载`
        );

      } catch (error) {
        this.logger.error('处理 /forward 命令失败:', error);
        await this.sendMessage(chatId, `❌ 错误: ${error.message}`);
      }
    });

    // /status 命令
    this.bot.onText(/\/status/, async (msg) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }

      const status = this.downloadManager.getStatus();
      const formattedStatus = this.formatStatusMessage(status);
      
      const statusMsg = await this.sendMessage(msg.chat.id, formattedStatus, { parse_mode: 'Markdown' });
      
      // 注册状态消息以便自动更新
      this.registerStatusMessage(msg.chat.id, statusMsg.message_id);
    });

    // /help 命令
    this.bot.onText(/\/help/, async (msg) => {
      const helpText = `📖 *Telegram Media Downloader Bot 命令：*

/forward <链接>
  转发并下载指定链接的消息
  示例: /forward https://t.me/c/1518902671/22987

/status
  查看当前下载状态和进度

/help
  显示此帮助信息

💡 *使用方式：*
• 直接发送媒体文件给 Bot
• 转发带媒体的消息给 Bot
• 使用 /forward 命令下载指定链接`;

      await this.sendMessage(msg.chat.id, helpText, { parse_mode: 'Markdown' });
    });
  }

  /**
   * 解析 Telegram 链接
   * 支持格式：
   * - https://t.me/c/1518902671/22987
   * - https://t.me/channel_name/123
   */
  parseTelegramLink(link) {
    try {
      // 格式1: https://t.me/c/1518902671/22987
      const match1 = link.match(/t\.me\/c\/(\d+)\/(\d+)/);
      if (match1) {
        const chatIdNum = parseInt(match1[1]);
        // 频道/群组需要添加 -100 前缀
        const chatId = `-100${chatIdNum}`;
        const messageId = parseInt(match1[2]);
        return { chatId, messageId };
      }

      // 格式2: https://t.me/channel_name/123
      const match2 = link.match(/t\.me\/([^\/]+)\/(\d+)/);
      if (match2) {
        const channelName = match2[1];
        const messageId = parseInt(match2[2]);
        // 需要通过 channel name 获取 chat_id，这里先返回
        return { channelName, messageId };
      }

      return null;
    } catch (error) {
      this.logger.error('解析 Telegram 链接失败:', error);
      return null;
    }
  }

  /**
   * 格式化状态消息（类似图片中的格式）
   */
  formatStatusMessage(status) {
    const stats = status.stats || {};
    const active = status.active || [];
    const queue = status.queue || 0;

    // 计算下载统计
    const downloading = {
      total: stats.total || 0,
      success: stats.completed || 0,
      failed: stats.failed || 0,
      skipped: stats.skipped || 0
    };

    // 转发统计（暂时使用下载统计）
    const forward = {
      total: downloading.total,
      success: downloading.success,
      failed: downloading.failed,
      skipped: downloading.skipped
    };

    let message = `🤖 *Telegram Media Downloader*\n`;
    message += `🌐 Version: 2.1.7\n\n`;

    // 下载状态
    const totalDownloaded = this.getTotalDownloadedSize(active);
    message += `📥 *Downloading: ${this.formatBytes(totalDownloaded)}*\n`;
    message += `  📁 Total: ${downloading.total}\n`;
    message += `  ✅ Success: ${downloading.success}\n`;
    message += `  ❌ Failed: ${downloading.failed}\n`;
    message += `  ⏭️ Skipped: ${downloading.skipped}\n\n`;

    // 转发状态
    message += `📤 *Forward*\n`;
    message += `  📁 Total: ${forward.total}\n`;
    message += `  ✅ Success: ${forward.success}\n`;
    message += `  ❌ Failed: ${forward.failed}\n`;
    message += `  ⏭️ Skipped: ${forward.skipped}\n\n`;

    // 下载进度
    if (active.length > 0) {
      message += `📊 *Download Progresses:*\n`;
      for (const download of active.slice(0, 5)) { // 最多显示5个
        const fileName = download.filePath ? 
          download.filePath.split(/[/\\]/).pop() : 
          `${download.messageId || 'unknown'}.mp4`;
        const size = download.fileSize || 0;
        const speed = download.speed || 0;
        const progress = download.progress || 0;

        message += `  🆔 Message ID: ${download.messageId || 'unknown'}\n`;
        message += `  📁 : ${fileName}\n`;
        if (size > 0) {
          message += `  📄 : ${this.formatBytes(size)}\n`;
        }
        if (speed > 0) {
          message += `  🚀 : ${this.formatBytes(speed)}/s\n`;
        }
        message += `  📈 : ${this.formatProgressBar(progress)} (${progress}%)\n\n`;
      }
    } else {
      message += `📊 *Download Progresses:*\n`;
      message += `  _暂无进行中的下载任务_\n`;
    }

    return message;
  }

  /**
   * 格式化进度条
   */
  formatProgressBar(progress) {
    const barLength = 20;
    const filled = Math.round((progress / 100) * barLength);
    const empty = barLength - filled;
    return '█'.repeat(filled) + '░'.repeat(empty);
  }

  /**
   * 格式化字节大小
   */
  formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0.0b';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return (bytes / Math.pow(k, i)).toFixed(2) + sizes[i];
  }

  /**
   * 获取总下载大小
   */
  getTotalDownloadedSize(activeDownloads) {
    return activeDownloads.reduce((total, download) => {
      return total + (download.downloadedBytes || 0);
    }, 0);
  }

  /**
   * 设置进度监听器
   */
  setupProgressListener() {
    // 监听下载进度更新
    this.downloadManager.on('progress', (data) => {
      // 更新所有状态消息
      this.updateStatusMessages();
    });

    this.downloadManager.on('status', (data) => {
      this.updateStatusMessages();
    });
  }

  /**
   * 注册状态消息，用于自动更新
   */
  registerStatusMessage(chatId, messageId) {
    this.statusMessages.set(chatId, messageId);
  }

  /**
   * 更新所有状态消息
   */
  async updateStatusMessages() {
    const status = this.downloadManager.getStatus();
    const formattedStatus = this.formatStatusMessage(status);

    // 更新所有存储的状态消息
    for (const [chatId, messageId] of this.statusMessages.entries()) {
      try {
        await this.bot.editMessageText(formattedStatus, {
          chat_id: chatId,
          message_id: messageId,
          parse_mode: 'Markdown'
        });
      } catch (error) {
        // 如果消息不存在或无法编辑，从列表中移除
        if (error.response?.error_code === 400) {
          this.statusMessages.delete(chatId);
        }
      }
    }
  }

  isAllowedUser(userId) {
    const allowedUsers = this.config.allowed_user_ids || ['me'];
    if (allowedUsers.includes('me')) {
      // TODO: 检查是否是配置的 Telegram 账户
      return true;
    }
    return allowedUsers.includes(userId.toString());
  }
}
