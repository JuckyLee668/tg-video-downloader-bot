export class BotHandler {
  constructor(config, downloadManager, logger, apiClient, messageRateLimiter = null, userClient = null, databaseManager = null) {
    this.config = config;
    this.downloadManager = downloadManager;
    this.logger = logger;
    this.apiClient = apiClient;
    this.messageRateLimiter = messageRateLimiter;
    this.userClient = userClient; // optional user-mode Telegram client for channel operations
    this.databaseManager = databaseManager; // SQLite database manager
    this.bot = null;
    this.statusMessages = new Map(); // 存储状态消息，用于更新

    // 频道搜索相关状态（按用户ID保存）
    this.userChannels = new Map(); // 存储每个用户的当前连接频道 {userId: {id, username, title}}
    this.userSearchResults = new Map(); // 存储每个用户的最后一次搜索结果 {userId: messages[]}

    // 登录流程相关状态
    this.loginStates = new Map(); // 存储用户的登录状态 {userId: {step, phoneNumber, phoneCodeHash, timeout}}
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

      const helpText =
        '<b>🤖 Telegram Media Downloader</b>\n\n' +
        '<b>📥 可用命令：</b>\n' +
        '/forward [链接] - 转发并下载指定链接的消息\n' +
        '/status - 查看下载状态\n' +
        '/help - 显示帮助信息\n\n' +
        '💡 <b>提示：</b> 直接发送媒体文件或转发消息也可以自动下载！';

      await this.sendMessage(msg.chat.id, helpText, { parse_mode: 'HTML' });
    });
    // /login 命令 - 辅助用户登录 Telegram 账号
    this.bot.onText(/\/login/, async (msg) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }
      if (!this.userClient) {
        return this.sendMessage(msg.chat.id, 
          '❌ 频道功能未启用\n\n' +
          '原因：未在 .env 文件中配置 USER_API_ID 和 USER_API_HASH\n\n' +
          '解决方法：\n' +
          '1. 访问 https://my.telegram.org\n' +
          '2. 创建一个新的应用获取 api_id 和 api_hash\n' +
          '3. 在 .env 文件中填写：\n' +
          '   USER_API_ID=你的api_id\n' +
          '   USER_API_HASH=你的api_hash\n' +
          '4. 重启 Bot'
        );
      }
      if (!this.userClient.client) {
        return this.sendMessage(msg.chat.id, 
          '❌ Telegram 用户客户端初始化失败\n\n' +
          '请检查：\n' +
          '1. USER_API_ID 和 USER_API_HASH 是否正确\n' +
          '2. 网络连接是否正常\n3. 是否需要代理\n\n' +
          '查看日志以获取详细错误信息'
        );
      }
      if (this.userClient.isConnected) {
        return this.sendMessage(msg.chat.id, '✅ 用户账号已登录，无需重复登录');
      }

      const userId = msg.from.id;
      const chatId = msg.chat.id;

      const loginMsg = 
        '🔐 Telegram 用户登录\n\n' +
        '✅ 安全登录方式已启用\n\n' +
        '登录时验证码的处理方式：\n' +
        '• 在 Telegram 应用中查看验证码\n' +
        '• 将每一位数字减 1 后转发给 Bot\n' +
        '• 例如：123456 → 发送 012345\n\n' +
        '🔒 安全优势：\n' +
        '✔️ 真实验证码不在网络中传输\n' +
        '✔️ 即使被截获也无法直接使用\n' +
        '✔️ 提供额外的安全保护层\n\n' +
        '---\n\n' +
        '如果您一定要在 Bot 中登录，请回复 /confirm_login 继续';

      await this.sendMessage(chatId, loginMsg);
    });

    // /confirm_login 命令 - 确认开始 Bot 内登录
    this.bot.onText(/\/confirm_login/, async (msg) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }

      const userId = msg.from.id;
      const chatId = msg.chat.id;

      // 初始化登录状态
      this.loginStates.set(userId, {
        step: 'phone',
        chatId: chatId,
        codeAttempts: 0, // 验证码尝试次数
        timeout: setTimeout(() => {
          this.loginStates.delete(userId);
        }, 10 * 60 * 1000) // 10分钟超时
      });

      const phoneMsg =
        '🔐 开始 Bot 内登录\n\n' +
        '⚠️ 安全提示：验证码有安全风险\n\n' +
        '请按步骤完成登录：\n\n' +
        '第1步：输入电话号码\n' +
        '请回复您的 Telegram 电话号码（包含国家代码）\n' +
        '例如：+861234567890\n\n' +
        '注意：\n' +
        '• 电话号码不会被保存\n' +
        '• 登录过程将在10分钟后自动取消\n' +
        '• 发送 /cancel 随时取消登录';

      await this.sendMessage(chatId, phoneMsg);
    });

    // /forward 命令 - 解析 Telegram 链接并下载
    this.bot.onText(/\/forward\s+(.+)$/, async (msg, match) => {
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
    this.bot.onText(/\/status(?:\s|$)/, async (msg) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }

      const status = this.downloadManager.getStatus();
      const formattedStatus = this.formatStatusMessage(status);
      
      const statusMsg = await this.sendMessage(msg.chat.id, formattedStatus, { parse_mode: 'Markdown' });
      
      // 注册状态消息以便自动更新
      this.registerStatusMessage(msg.chat.id, statusMsg.message_id);
    });

    // /channels 命令
    this.bot.onText(/\/channels(?:\s|$)/, async (msg) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }
      if (!this.userClient) {
        return this.sendMessage(msg.chat.id, '频道功能未启用，请在配置中设置 user_api');
      }
      if (!this.userClient.isConnected) {
        return this.sendMessage(msg.chat.id, '请先登录用户账号 (运行 login-user.js)，然后重启 Bot');
      }
      try {
        const result = await this.userClient.getAllChannels();
        if (result.success) {
          const list = result.channels.map(c => `• ${c.title || c.username || c.id} (${c.id})`).join('\n');
          await this.sendMessage(msg.chat.id, `📃 已加入频道/超群：\n${list}`);
        } else {
          await this.sendMessage(msg.chat.id, `获取频道列表失败：${result.error}`);
        }
      } catch (e) {
        this.logger.error('获取频道列表失败:', e);
        await this.sendMessage(msg.chat.id, `错误：${e.message}`);
      }
    });

    // /channel_join 命令
    this.bot.onText(/\/channel_join\s+(.+)/, async (msg, match) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }
      if (!this.userClient) {
        return this.sendMessage(msg.chat.id, '频道功能未启用，请在配置中设置 user_api');
      }
      if (!this.userClient.isConnected) {
        return this.sendMessage(msg.chat.id, '请先登录用户账号 (运行 login-user.js)，然后重启 Bot');
      }
      const input = match[1].trim();
      try {
        const result = await this.userClient.findAndJoinChannel(input);
        if (result.success) {
          await this.sendMessage(msg.chat.id, `已加入频道：${result.channel.title || result.channel.username || result.channel.id}`);
        } else {
          await this.sendMessage(msg.chat.id, `加入失败：${result.error}`);
        }
      } catch (e) {
        this.logger.error('频道加入失败:', e);
        await this.sendMessage(msg.chat.id, `错误：${e.message}`);
      }
    });

    // /channel_search 命令
    this.bot.onText(/\/channel_search\s+(\S+)\s+(.+)/, async (msg, match) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }
      if (!this.userClient) {
        return this.sendMessage(msg.chat.id, '频道功能未启用，请在配置中设置 user_api');
      }
      const channel = match[1].trim();
      const keyword = match[2].trim();
      try {
        const msgs = await this.userClient.getChannelMessages({ username: channel }, { keyword, limit: 20 });
        if (msgs.length === 0) {
          return this.sendMessage(msg.chat.id, '未找到匹配消息');
        }
        const lines = msgs.map(m => `#${m.id} ${m.hasMedia?'📎':'💬'} "${(m.message||'').slice(0,40)}"`).join('\n');
        await this.sendMessage(msg.chat.id, `🔍 搜索结果：\n${lines}`);
      } catch (e) {
        this.logger.error('频道搜索失败:', e);
        await this.sendMessage(msg.chat.id, `错误：${e.message}`);
      }
    });

    // /download_list 命令 - 查看下载列表
    this.bot.onText(/\/download_list(?:\s+(\d+))?(?:\s+(\d+))?/, async (msg, match) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }

      const page = parseInt(match[1]) || 1;
      const perPage = parseInt(match[2]) || 10;
      const offset = (page - 1) * perPage;

      try {
        // 获取下载队列
        const queueItems = this.databaseManager.getDownloadQueueList(perPage, offset);
        // 获取下载历史
        const historyItems = this.databaseManager.getDownloadHistoryList(perPage, offset);

        let message = `<b>📋 下载列表 (第${page}页)</b>\n\n`;

        // 下载队列
        if (queueItems.length > 0) {
          message += `<b>📥 下载队列:</b>\n`;
          queueItems.forEach((item, index) => {
            const statusEmoji = {
              'pending': '⏳',
              'downloading': '📥',
              'completed': '✅',
              'failed': '❌'
            }[item.status] || '❓';
            message += `${statusEmoji} ${this.escapeHTML(item.file_name)} (${this.formatBytes(item.file_size || 0)})\n`;
            message += `   ID: ${this.escapeHTML(item.task_id)}\n\n`;
          });
        } else {
          message += `<b>📥 下载队列:</b> 空\n\n`;
        }

        // 下载历史
        if (historyItems.length > 0) {
          message += `<b>📚 下载历史:</b>\n`;
          historyItems.slice(0, 5).forEach((item, index) => {
            const channel = item.channel_title || item.channel_username || item.chat_id;
            message += `✅ ${this.escapeHTML(item.file_name)} (${this.formatBytes(item.file_size || 0)})\n`;
            message += `   频道: ${this.escapeHTML(channel)}\n\n`;
          });
          if (historyItems.length > 5) {
            message += `... 还有 ${historyItems.length - 5} 个历史记录\n`;
          }
        } else {
          message += `<b>📚 下载历史:</b> 空\n`;
        }

        message += `\n💡 使用 /download_list [页码] [每页数量] 查看更多`;

        await this.sendMessage(msg.chat.id, message, { parse_mode: 'HTML' });
      } catch (error) {
        this.logger.error('获取下载列表失败:', error);
        await this.sendMessage(msg.chat.id, `❌ 获取下载列表失败: ${this.escapeHTML(error.message)}`);
      }
    });

    // /search_history 命令 - 搜索下载历史
    this.bot.onText(/\/search_history\s+(.+)/, async (msg, match) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }

      const keyword = match[1].trim();

      try {
        const results = this.databaseManager.searchDownloadHistory(keyword, 20);

        if (results.length === 0) {
          return this.sendMessage(msg.chat.id, `🔍 未找到包含"${this.escapeHTML(keyword)}"的下载记录`);
        }

        let message = `🔍 <b>搜索结果: "${this.escapeHTML(keyword)}"</b>\n\n`;
        results.forEach((item, index) => {
          const channel = item.channel_title || item.channel_username || item.chat_id;
          const date = new Date(item.downloaded_at).toLocaleString('zh-CN');
          message += `${index + 1}. ${this.escapeHTML(item.file_name)}\n`;
          message += `   📄 大小: ${this.formatBytes(item.file_size || 0)}\n`;
          message += `   📺 频道: ${this.escapeHTML(channel)}\n`;
          message += `   📅 时间: ${this.escapeHTML(date)}\n\n`;
        });

        await this.sendMessage(msg.chat.id, message, { parse_mode: 'HTML' });
      } catch (error) {
        this.logger.error('搜索下载历史失败:', error);
        await this.sendMessage(msg.chat.id, `❌ 搜索失败: ${this.escapeHTML(error.message)}`);
      }
    });

    // /channel_connect 命令 - 连接到频道进行搜索
    this.bot.onText(/\/channel_connect\s+(.+)/, async (msg, match) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }
      if (!this.userClient) {
        return this.sendMessage(msg.chat.id, '频道功能未启用，请在配置中设置 user_api');
      }
      if (!this.userClient.isConnected) {
        return this.sendMessage(msg.chat.id, '请先登录用户账号 (运行 login-user.js)，然后重启 Bot');
      }

      const channelInput = match[1].trim();
      const chatId = msg.chat.id;

      try {
        // 尝试查找并加入频道
        const result = await this.userClient.findAndJoinChannel(channelInput);

        if (result.success) {
          const userId = msg.from.id;
          const channelInfo = {
            id: result.channel.id,
            username: result.channel.username,
            title: result.channel.title
          };
          
          // 1. 为该用户保存当前连接的频道信息（内存）
          this.userChannels.set(userId, channelInfo);
          
          // 2. 保存到数据库（持久化）
          const maxChannels = this.config.max_connected_channels || 10;
          this.databaseManager.saveConnectedChannel(channelInfo, maxChannels);

          await this.sendMessage(chatId,
            `✅ <b>频道连接成功并已保存</b>\n\n` +
            `📺 频道: ${this.escapeHTML(result.channel.title || result.channel.username)}\n` +
            `🆔 ID: ${this.escapeHTML(result.channel.id)}\n\n` +
            `💡 现在可以使用搜索命令在<b>所有已保存频道</b>中并行搜索了。`
          , { parse_mode: 'HTML' });
        } else {
          await this.sendMessage(chatId, `❌ 连接频道失败: ${this.escapeHTML(result.error)}`);
        }
      } catch (error) {
        this.logger.error('连接频道失败:', error);
        await this.sendMessage(chatId, `❌ 连接频道失败: ${this.escapeHTML(error.message)}`);
      }
    });

    // /channel_search_keyword 命令 - 在所有已保存频道中并行搜索
    this.bot.onText(/\/channel_search_keyword\s+(.+)/, async (msg, match) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }
      if (!this.userClient || !this.userClient.isConnected) {
        return this.sendMessage(msg.chat.id, '请确认用户账号已登录且频道功能已启用');
      }

      const keyword = match[1].trim();
      const chatId = msg.chat.id;
      const userId = msg.from.id;

      // 获取所有已保存的频道
      const savedChannels = this.databaseManager.getSavedChannels(this.config.max_connected_channels || 10);
      
      if (savedChannels.length === 0) {
        return this.sendMessage(chatId, '❌ 尚未连接任何频道。请先使用 /channel_connect 连接频道。');
      }

      try {
        await this.sendMessage(chatId, `🔍 正在 ${savedChannels.length} 个频道中并行搜索 "${keyword}"...`);

        // 并行搜索所有频道
        const searchPromises = savedChannels.map(async (channel) => {
          try {
            const results = await this.userClient.getChannelMessages(
              { id: channel.channel_id, username: channel.username },
              { keyword, limit: 20 }
            );
            return { channel, results };
          } catch (err) {
            this.logger.error(`搜索频道 ${channel.title} 失败:`, err.message);
            return { channel, results: [], error: err.message };
          }
        });

        const allResults = await Promise.all(searchPromises);
        
        // 合并所有结果
        const mergedMessages = [];
        let totalFound = 0;
        
        allResults.forEach(({ channel, results }) => {
          totalFound += results.length;
          results.forEach(m => {
            // 给消息打上频道标签，方便后续下载
            mergedMessages.push({
              ...m,
              channelInfo: {
                id: channel.channel_id,
                username: channel.username,
                title: channel.title
              }
            });
          });
        });

        if (totalFound === 0) {
          return this.sendMessage(chatId, `❌ 在 ${savedChannels.length} 个频道中均未找到包含"${keyword}"的消息`);
        }

        // 存储搜索结果用于批量下载（按用户保存）
        this.userSearchResults.set(userId, mergedMessages);

        let message = `🔍 <b>并行搜索结果: "${this.escapeHTML(keyword)}"</b>\n\n`;
        message += `📊 在 ${savedChannels.length} 个频道中共找到 ${totalFound} 条消息\n\n`;

        // 显示前 15 条结果
        mergedMessages.slice(0, 15).forEach((msg, index) => {
          const hasMedia = msg.hasMedia ? '📎' : '💬';
          const text = (msg.message || '').replace(/[\n\r]+/g, ' ').slice(0, 40);
          const channelTitle = msg.channelInfo.title || msg.channelInfo.username;
          message += `${index + 1}. ${hasMedia} [${this.escapeHTML(channelTitle)}] #${msg.id}\n   ${this.escapeHTML(text)}${text.length > 40 ? '...' : ''}\n\n`;
        });

        if (mergedMessages.length > 15) {
          message += `... 还有 ${mergedMessages.length - 15} 条消息\n`;
        }

        message += `\n💡 使用 /batch_download &lt;序号范围，如 1-5&gt; 批量下载这些消息`;

        await this.sendMessage(chatId, message, { parse_mode: 'HTML' });
      } catch (error) {
        this.logger.error('并行搜索失败:', error);
        await this.sendMessage(chatId, `❌ 搜索过程中发生错误: ${this.escapeHTML(error.message)}`);
      }
    });

    // /channelsearchkeyword 命令 - 别名（无下划线版本）
    this.bot.onText(/\/channelsearchkeyword\s+(.+)/, async (msg, match) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }
      if (!this.userClient) {
        return this.sendMessage(msg.chat.id, '频道功能未启用，请在配置中设置 user_api');
      }
      if (!this.userClient.isConnected) {
        return this.sendMessage(msg.chat.id, '请先登录用户账号 (运行 login-user.js)，然后重启 Bot');
      }

      const userId = msg.from.id;
      const currentChannel = this.userChannels.get(userId);
      if (!currentChannel) {
        return this.sendMessage(msg.chat.id, '请先使用 /channel_connect 连接到频道');
      }

      const keyword = match[1].trim();
      const chatId = msg.chat.id;

      try {
        await this.sendMessage(chatId, `🔍 正在搜索频道 "${currentChannel.title || currentChannel.username}" 中的 "${keyword}"...`);

        const messages = await this.userClient.getChannelMessages(
          { id: currentChannel.id },
          { keyword, limit: 50 }
        );

        if (messages.length === 0) {
          return this.sendMessage(chatId, `❌ 未找到包含"${keyword}"的消息`);
        }

        // 存储搜索结果用于批量下载（按用户保存）
        this.userSearchResults.set(userId, messages);

        let message = `🔍 搜索结果: "${keyword}"\n\n`;
        message += `📺 频道: ${currentChannel.title || currentChannel.username}\n`;
        message += `📊 找到 ${messages.length} 条消息\n\n`;

        messages.slice(0, 10).forEach((msg, index) => {
          const hasMedia = msg.media ? '📎' : '💬';
          const text = (msg.message || '').slice(0, 50);
          message += `${index + 1}. ${hasMedia} #${msg.id} ${text}${text.length > 50 ? '...' : ''}\n`;
        });

        if (messages.length > 10) {
          message += `\n... 还有 ${messages.length - 10} 条消息\n`;
        }

        message += `\n💡 使用 /batch_download <消息ID1,ID2,...> 批量下载这些消息`;

        await this.sendMessage(chatId, message);
      } catch (error) {
        this.logger.error('频道关键字搜索失败:', error);
        await this.sendMessage(chatId, `❌ 搜索失败: ${error.message}`);
      }
    });

    // /channel_search_time 命令 - 频道内容时间筛选
    this.bot.onText(/\/channel_search_time\s+(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})/, async (msg, match) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }
      if (!this.userClient) {
        return this.sendMessage(msg.chat.id, '频道功能未启用，请在配置中设置 user_api');
      }
      if (!this.userClient.isConnected) {
        return this.sendMessage(msg.chat.id, '请先登录用户账号 (运行 login-user.js)，然后重启 Bot');
      }

      const userId = msg.from.id;
      const currentChannel = this.userChannels.get(userId);
      if (!currentChannel) {
        return this.sendMessage(msg.chat.id, '请先使用 /channel_connect 连接到频道');
      }

      const startDate = match[1];
      const endDate = match[2];
      const chatId = msg.chat.id;

      try {
        await this.sendMessage(chatId, `🔍 正在搜索时间范围 ${startDate} 到 ${endDate} 的消息...`);

        const startTime = new Date(startDate + 'T00:00:00Z').getTime() / 1000;
        const endTime = new Date(endDate + 'T23:59:59Z').getTime() / 1000;

        const messages = await this.userClient.getChannelMessages(
          { id: currentChannel.id },
          { minId: startTime, maxId: endTime, limit: 100 }
        );

        if (messages.length === 0) {
          return this.sendMessage(chatId, `❌ 在指定时间范围内未找到消息`);
        }

        // 存储搜索结果用于批量下载（按用户保存）
        this.userSearchResults.set(userId, messages);

        let message = `🔍 时间范围搜索结果\n\n`;
        message += `📺 频道: ${currentChannel.title || currentChannel.username}\n`;
        message += `📅 时间: ${startDate} ~ ${endDate}\n`;
        message += `📊 找到 ${messages.length} 条消息\n\n`;

        messages.slice(0, 10).forEach((msg, index) => {
          const hasMedia = msg.media ? '📎' : '💬';
          const date = new Date(msg.date * 1000).toLocaleString('zh-CN');
          const text = (msg.message || '').slice(0, 50);
          message += `${index + 1}. ${hasMedia} #${msg.id} (${date})\n   ${text}${text.length > 50 ? '...' : ''}\n`;
        });

        if (messages.length > 10) {
          message += `\n... 还有 ${messages.length - 10} 条消息\n`;
        }

        message += `\n💡 使用 /batch_download [消息ID1,ID2,...] 批量下载这些消息`;

        await this.sendMessage(chatId, message);
      } catch (error) {
        this.logger.error('频道时间搜索失败:', error);
        await this.sendMessage(chatId, `❌ 搜索失败: ${error.message}`);
      }
    });

    // /channelsearchtime 命令 - 别名（无下划线版本）
    this.bot.onText(/\/channelsearchtime\s+(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})/, async (msg, match) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }
      if (!this.userClient) {
        return this.sendMessage(msg.chat.id, '频道功能未启用，请在配置中设置 user_api');
      }
      if (!this.userClient.isConnected) {
        return this.sendMessage(msg.chat.id, '请先登录用户账号 (运行 login-user.js)，然后重启 Bot');
      }

      const userId = msg.from.id;
      const currentChannel = this.userChannels.get(userId);
      if (!currentChannel) {
        return this.sendMessage(msg.chat.id, '请先使用 /channel_connect 连接到频道');
      }

      const startDate = match[1];
      const endDate = match[2];
      const chatId = msg.chat.id;

      try {
        await this.sendMessage(chatId, `🔍 正在搜索时间范围 ${startDate} 到 ${endDate} 的消息...`);

        const startTime = new Date(startDate + 'T00:00:00Z').getTime() / 1000;
        const endTime = new Date(endDate + 'T23:59:59Z').getTime() / 1000;

        const messages = await this.userClient.getChannelMessages(
          { id: currentChannel.id },
          { minId: startTime, maxId: endTime, limit: 100 }
        );

        if (messages.length === 0) {
          return this.sendMessage(chatId, `❌ 在指定时间范围内未找到消息`);
        }

        // 存储搜索结果用于批量下载（按用户保存）
        this.userSearchResults.set(userId, messages);

        let message = `🔍 时间范围搜索结果\n\n`;
        message += `📺 频道: ${currentChannel.title || currentChannel.username}\n`;
        message += `📅 时间: ${startDate} ~ ${endDate}\n`;
        message += `📊 找到 ${messages.length} 条消息\n\n`;

        messages.slice(0, 10).forEach((msg, index) => {
          const hasMedia = msg.media ? '📎' : '💬';
          const date = new Date(msg.date * 1000).toLocaleString('zh-CN');
          const text = (msg.message || '').slice(0, 50);
          message += `${index + 1}. ${hasMedia} #${msg.id} (${date})\n   ${text}${text.length > 50 ? '...' : ''}\n`;
        });

        if (messages.length > 10) {
          message += `\n... 还有 ${messages.length - 10} 条消息\n`;
        }

        message += `\n💡 使用 /batch_download [消息ID1,ID2,...] 批量下载这些消息`;

        await this.sendMessage(chatId, message);
      } catch (error) {
        this.logger.error('频道时间搜索失败:', error);
        await this.sendMessage(chatId, `❌ 搜索失败: ${error.message}`);
      }
    });

    // /channel_search_recent 命令 - 获取频道最新消息
    this.bot.onText(/\/channel_search_recent(?:\s+(\d+))?/, async (msg, match) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }
      if (!this.userClient) {
        return this.sendMessage(msg.chat.id, '频道功能未启用，请在配置中设置 user_api');
      }
      if (!this.userClient.isConnected) {
        return this.sendMessage(msg.chat.id, '请先登录用户账号 (运行 login-user.js)，然后重启 Bot');
      }

      const userId = msg.from.id;
      const currentChannel = this.userChannels.get(userId);
      if (!currentChannel) {
        return this.sendMessage(msg.chat.id, '请先使用 /channel_connect 连接到频道');
      }

      const limit = Math.min(parseInt(match[1]) || 20, 50);
      const chatId = msg.chat.id;

      try {
        await this.sendMessage(chatId, `🔍 正在获取最新 ${limit} 条消息...`);

        const messages = await this.userClient.getChannelMessages(
          { id: currentChannel.id },
          { limit }
        );

        if (messages.length === 0) {
          return this.sendMessage(chatId, `❌ 未找到消息`);
        }

        // 存储搜索结果用于批量下载（按用户保存）
        this.userSearchResults.set(userId, messages);

        let message = `🔍 最新消息\n\n`;
        message += `📺 频道: ${currentChannel.title || currentChannel.username}\n`;
        message += `📊 显示 ${messages.length} 条消息\n\n`;

        messages.forEach((msg, index) => {
          const hasMedia = msg.media ? '📎' : '💬';
          const date = new Date(msg.date * 1000).toLocaleString('zh-CN');
          const text = (msg.message || '').slice(0, 50);
          message += `${index + 1}. ${hasMedia} #${msg.id} (${date})\n   ${text}${text.length > 50 ? '...' : ''}\n`;
        });

        message += `\n💡 使用 /batch_download [消息ID1,ID2,...] 批量下载这些消息`;

        await this.sendMessage(chatId, message);
      } catch (error) {
        this.logger.error('获取最新消息失败:', error);
        await this.sendMessage(chatId, `❌ 获取失败: ${error.message}`);
      }
    });

    // /channelsearchrecent 命令 - 别名（无下划线版本）
    this.bot.onText(/\/channelsearchrecent(?:\s+(\d+))?/, async (msg, match) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }
      if (!this.userClient) {
        return this.sendMessage(msg.chat.id, '频道功能未启用，请在配置中设置 user_api');
      }
      if (!this.userClient.isConnected) {
        return this.sendMessage(msg.chat.id, '请先登录用户账号 (运行 login-user.js)，然后重启 Bot');
      }

      const userId = msg.from.id;
      const currentChannel = this.userChannels.get(userId);
      if (!currentChannel) {
        return this.sendMessage(msg.chat.id, '请先使用 /channel_connect 连接到频道');
      }

      const limit = Math.min(parseInt(match[1]) || 20, 50);
      const chatId = msg.chat.id;

      try {
        await this.sendMessage(chatId, `🔍 正在获取最新 ${limit} 条消息...`);

        const messages = await this.userClient.getChannelMessages(
          { id: currentChannel.id },
          { limit }
        );

        if (messages.length === 0) {
          return this.sendMessage(chatId, `❌ 未找到消息`);
        }

        // 存储搜索结果用于批量下载（按用户保存）
        this.userSearchResults.set(userId, messages);

        let message = `🔍 最新消息\n\n`;
        message += `📺 频道: ${currentChannel.title || currentChannel.username}\n`;
        message += `📊 显示 ${messages.length} 条消息\n\n`;

        messages.forEach((msg, index) => {
          const hasMedia = msg.media ? '📎' : '💬';
          const date = new Date(msg.date * 1000).toLocaleString('zh-CN');
          const text = (msg.message || '').slice(0, 50);
          message += `${index + 1}. ${hasMedia} #${msg.id} (${date})\n   ${text}${text.length > 50 ? '...' : ''}\n`;
        });

        message += `\n💡 使用 /batch_download [消息ID1,ID2,...] 批量下载这些消息`;

        await this.sendMessage(chatId, message);
      } catch (error) {
        this.logger.error('获取最新消息失败:', error);
        await this.sendMessage(chatId, `❌ 获取失败: ${error.message}`);
      }
    });

    // /batch_download 命令 - 搜索列表批量下载（支持序号范围、消息ID和强制下载参数）
    this.bot.onText(/\/batch_download\s+([\d,\s\-]+)(?:\s+(force|f))?/, async (msg, match) => {
      if (!this.isAllowedUser(msg.from.id)) {
        return this.sendMessage(msg.chat.id, '您没有权限使用此 Bot');
      }
      if (!this.userClient) {
        return this.sendMessage(msg.chat.id, '频道功能未启用，请在配置中设置 user_api');
      }

      const userId = msg.from.id;
      const searchResults = this.userSearchResults.get(userId);
      if (!searchResults || searchResults.length === 0) {
        return this.sendMessage(msg.chat.id, '请先执行搜索命令获取消息列表');
      }

      const idInput = match[1].replace(/\s+/g, ''); // 移除所有空格
      const isForce = !!match[2]; // 是否强制重新下载
      let ids = [];

      // 检查是否是范围格式 (1-8) 还是ID列表 (8932,8779)
      if (idInput.includes('-')) {
        // 处理范围格式：1-8 表示序号1到8
        const rangeParts = idInput.split('-');
        if (rangeParts.length === 2) {
          const start = parseInt(rangeParts[0]);
          const end = parseInt(rangeParts[1]);
          
          if (!isNaN(start) && !isNaN(end) && start > 0 && end > 0 && start <= searchResults.length && end <= searchResults.length) {
            // 获取范围内的消息ID
            for (let i = start - 1; i < end; i++) {
              if (searchResults[i]) {
                ids.push(searchResults[i].id);
              }
            }
          } else {
            return this.sendMessage(msg.chat.id, `❌ 范围无效。搜索结果共 ${searchResults.length} 条消息，请使用 1-${searchResults.length} 的范围`);
          }
        }
      } else {
        // 处理ID列表格式
        ids = idInput.split(',').map(id => parseInt(id.trim())).filter(id => !isNaN(id));
      }

      if (ids.length === 0) {
        return this.sendMessage(msg.chat.id, `❌ 无有效的下载项。使用方法：\n• 序号范围：/batch_download 1-8\n• 消息ID：/batch_download 8932,8779,8560\n• 强制重新下载：/batch_download 1-8 force`);
      }

      const chatId = msg.chat.id;

      try {
        await this.sendMessage(chatId, `📥 正在准备批量下载 ${ids.length} 个文件${isForce ? ' (强制重新下载模式)' : ''}...`);

        let successCount = 0;
        let skipCount = 0;
        let failCount = 0;

        for (const messageId of ids) {
          try {
            // 在循环中添加微小延迟，避免请求过快
            await new Promise(resolve => setTimeout(resolve, 500));

            // 尝试更灵活的 ID 匹配
            const message = searchResults.find(m => String(m.id) === String(messageId));
            if (!message) {
              this.logger.warn(`消息 ${messageId} 不在当前缓存的搜索结果中`);
              failCount++;
              continue;
            }

            // 检查是否有媒体文件
            if (!message.hasMedia) {
              this.logger.warn(`消息 ${messageId} 没有媒体文件，跳过`);
              failCount++;
              continue;
            }

            try {
              // 优先使用消息自带的频道信息
              const targetChannel = message.channelInfo || this.userChannels.get(userId);

              if (!targetChannel) {
                this.logger.error(`无法确定消息 ${messageId} 的所属频道`);
                failCount++;
                continue;
              }

              // 获取文件信息
              const fileInfo = await this.userClient.getFileInfo(targetChannel, messageId);

              // 尝试获取更好的文件名
              let fileName = fileInfo.fileName;
              if (!fileName || fileName === '未知文件') {
                const text = message.message || '';
                if (text) {
                  fileName = text.replace(/[\n\r\s]+/g, ' ').trim().substring(0, 50);
                }
              }

              // 创建下载任务
              const task = {
                useUserClient: true,
                channel: targetChannel,
                messageId: messageId,
                message: {
                  message_id: messageId,
                  date: message.date,
                  text: message.message,
                  caption: message.message,
                  chat: {
                    id: targetChannel.id,
                    title: targetChannel.title,
                    type: 'channel'
                  }
                },
                chatId: targetChannel.id,
                chatTitle: targetChannel.title || targetChannel.username,
                mediaType: message.mediaType || 'unknown',
                fileName: fileName,
                forceFreshDownload: isForce
              };

              const result = await this.downloadManager.addDownloadTask(task);

              if (result === 'added') {
                successCount++;
              } else if (result === 'skipped' || result === 'duplicate') {
                skipCount++;
              }
            } catch (fileError) {
              this.logger.error(`获取文件信息失败 ${messageId}: ${fileError.message}`);
              failCount++;
            }

          } catch (error) {
            this.logger.error(`添加下载任务失败 ${messageId}: ${error.message}`);
            failCount++;
          }
        }
        let summary = `📥 <b>批量下载任务处理完成</b>\n\n`;
        summary += `✅ <b>成功:</b> ${successCount} 个 (已加入队列)\n`;
        summary += `⏭️ <b>跳过:</b> ${skipCount} 个 (已下载或已在队列中)\n`;
        summary += `❌ <b>失败:</b> ${failCount} 个\n\n`;
        
        if (skipCount > 0 && !isForce) {
          summary += `💡 <b>提示:</b> 部分文件因已下载被跳过。如需重新下载，请在命令后加上 force，例如：\n<code>/batch_download ${idInput} force</code>\n\n`;
        }
        
        summary += `使用 /status 查看下载进度`;

        await this.sendMessage(chatId, summary, { parse_mode: 'HTML' });

      } catch (error) {
        this.logger.error('批量下载失败:', error);
        await this.sendMessage(chatId, `❌ 批量下载失败: ${this.escapeHTML(error.message)}`);
      }
    });

    // /help 命令
    this.bot.onText(/\/help(?:\s|$)/, async (msg) => {
      const helpText =
        '<b>🤖 Telegram Media Downloader Bot</b>\n\n' +
        '<b>📋 下载管理:</b>\n' +
        '/download_list [页码] [每页数量] - 查看下载队列和历史\n' +
        '/search_history 关键词 - 搜索下载历史记录\n\n' +
        '<b>🔍 频道搜索:</b>\n' +
        '/channel_connect 频道名/链接 - 连接到频道进行搜索\n' +
        '/channel_search_keyword 关键词 - 在已连接频道中搜索关键词\n' +
        '/channel_search_time 开始日期 结束日期 - 按时间范围搜索 (格式: YYYY-MM-DD)\n' +
        '/channel_search_recent [数量] - 获取频道最新消息\n\n' +
        '<b>📥 批量下载:</b>\n' +
        '/batch_download 消息ID1,ID2,... - 批量下载搜索结果中的消息\n\n' +
        '<b>📊 状态监控:</b>\n' +
        '/status - 查看当前下载状态和进度\n\n' +
        '<b>📺 频道管理:</b>\n' +
        '/channels - 列出已加入的频道/超级群\n' +
        '/channel_join 用户名或邀请链接 - 加入新频道\n' +
        '/login - 登录 Telegram 用户账号（推荐：运行 node login-user.js）\n\n' +
        '<b>🔗 直接下载:</b>\n' +
        '/forward 链接 - 下载指定链接的消息\n\n' +
        '<b>❓ 帮助:</b>\n' +
        '/help - 显示此帮助信息\n' +
        '/features - 显示详细功能介绍\n\n' +
        '<b>💡 使用提示:</b>\n' +
        '• 直接发送媒体文件或转发消息即可自动下载\n' +
        '• 先用 /channel_connect 连接频道，再使用搜索功能\n' +
        '• 搜索结果会自动保存，可用 /batch_download 批量下载';

      await this.sendMessage(msg.chat.id, helpText, { parse_mode: 'HTML' });
    });

    // /features 命令 - 功能介绍
    this.bot.onText(/\/features(?:\s|$)/, async (msg) => {
      const featuresText = `🚀 *Telegram Media Downloader - 完整功能介绍*

🎯 *核心功能:*
• ✅ 自动媒体下载 - 支持图片、视频、音频、文档等
• ✅ 批量处理 - 支持多文件同时下载
• ✅ 断点续传 - 网络中断后自动恢复下载
• ✅ 重复检测 - 智能避免重复下载
• ✅ 进度监控 - 实时显示下载进度和状态

📊 *数据管理:*
• ✅ SQLite数据库 - 高性能持久化存储
• ✅ 下载历史 - 完整记录所有下载任务
• ✅ 队列管理 - 智能任务队列调度
• ✅ 统计分析 - 下载数据统计和分析

🔍 *频道搜索:*
• ✅ 频道连接 - 支持用户名、邀请链接等多种方式
• ✅ 关键字搜索 - 在频道内容中搜索特定关键词
• ✅ 时间筛选 - 按日期范围筛选频道消息
• ✅ 最新消息 - 获取频道最新发布的内容
• ✅ 批量下载 - 从搜索结果中批量下载媒体文件

🤖 *Bot 交互:*
• ✅ 命令系统 - 丰富的命令行操作界面
• ✅ 状态监控 - 实时查看下载进度和系统状态
• ✅ 权限控制 - 支持多用户权限管理
• ✅ 消息通知 - 下载完成自动通知

⚙️ *系统特性:*
• ✅ 跨平台支持 - Windows、Linux、macOS
• ✅ 高并发处理 - 支持大量文件同时下载
• ✅ 错误恢复 - 自动重试失败的下载任务
• ✅ 日志记录 - 详细的操作日志和错误记录

🔧 *技术架构:*
• Node.js + ES Modules
• Telegram Bot API + User API
• SQLite 数据库
• Winston 日志系统
• 异步任务队列

📈 *性能优化:*
• WAL 模式数据库
• 预编译 SQL 语句
• 内存缓存机制
• 智能并发控制
• 资源使用监控

💻 *使用方式:*
1. 配置 Bot Token 和 User API
2. 启动机器人服务
3. 使用 /channel_connect 连接目标频道
4. 使用搜索命令查找内容
5. 使用 /batch_download 批量下载
6. 使用 /status 监控下载进度

🎉 *开始使用:*
发送 /help 查看所有可用命令`;

      await this.sendMessage(msg.chat.id, featuresText, { parse_mode: 'Markdown' });
    });

    // 处理登录流程的消息
    this.bot.on('message', async (msg) => {
      // 跳过命令消息（以/开头的消息）
      if (!msg.text || msg.text.startsWith('/')) {
        return;
      }

      const userId = msg.from.id;
      const loginState = this.loginStates.get(userId);

      if (!loginState) {
        return; // 没有登录状态，跳过
      }

      const text = msg.text.trim();

      // 处理取消命令
      if (text === '/cancel') {
        clearTimeout(loginState.timeout);
        this.loginStates.delete(userId);
        await this.sendMessage(loginState.chatId, '❌ 登录已取消');
        return;
      }

      try {
        switch (loginState.step) {
          case 'phone':
            // 验证电话号码格式，先移除所有空格
            const phoneNumber = text.replace(/\s+/g, '');
            if (!phoneNumber.match(/^\+\d{10,15}$/)) {
              await this.sendMessage(loginState.chatId,
                '❌ 电话号码格式无效，请包含国家代码，例如：+861234567890\n\n' +
                '发送 /cancel 取消登录'
              );
              return;
            }

            loginState.phoneNumber = phoneNumber;
            loginState.step = 'waiting_code';
            loginState.codeAttempts = 0; // 重置验证码尝试计数

            // 发送验证码请求
            await this.sendMessage(loginState.chatId, '📨 正在发送验证码，请稍候...');

            try {
              const result = await this.userClient.startInteractiveLogin(phoneNumber);

              loginState.phoneCodeHash = result.phoneCodeHash;
              loginState.step = 'code';

              const codeMsg = 
                '✅ 验证码已准备就绪\n\n' +
                '第2步：获取并输入验证码\n\n' +
                '重要步骤：\n' +
                '1️⃣ 打开您的 Telegram 应用\n' +
                '2️⃣ 查看验证码\n' +
                '3️⃣ 🔒 安全做法：将验证码每一位都减 1 后发送给 Bot\n' +
                '   例如：真实验证码 123456 → 发送 012345\n\n' +
                '⏱️ 验证码有效期为 5 分钟\n' +
                '⚡ 请尽快完成输入\n\n' +
                '💡 如果没收到，可以：\n' +
                '• 重新发送：/login\n' +
                '• 取消登录：/cancel';
              await this.sendMessage(loginState.chatId, codeMsg);
            } catch (error) {
              this.logger.error('发送验证码失败:', error);
              clearTimeout(loginState.timeout);
              this.loginStates.delete(userId);
              await this.sendMessage(loginState.chatId, `❌ 发送验证码失败: ${error.message}\n\n请稍后重试或使用 login-user.js 脚本手动登录`);
            }
            break;

          case 'code':
            // 验证验证码格式
            if (!text.match(/^\d{5,6}$/)) {
              await this.sendMessage(loginState.chatId,
                '❌ 验证码格式无效，请输入5-6位数字\n\n' +
                '发送 /cancel 取消登录'
              );
              return;
            }

            // 还原验证码：每位加 1（因为用户发送的是每位减 1 的）
            const restoredCode = text.split('').map(digit => {
              let restored = (parseInt(digit) + 1) % 10;
              return restored.toString();
            }).join('');

            loginState.code = text;
            loginState.step = 'waiting_signin';

            await this.sendMessage(loginState.chatId, '🔐 正在验证并登录...');

            try {
              const result = await this.userClient.signInWithCode(restoredCode, loginState.phoneCodeHash);

              if (result.success) {
                // 登录成功
                clearTimeout(loginState.timeout);
                this.loginStates.delete(userId);
                this.userClient.isConnected = true;

                const successMsg = 
                  '🎉 登录成功！\n\n' +
                  '✅ Telegram 用户账号已连接\n' +
                  '✅ 会话已保存到 session.txt\n\n' +
                  '现在您可以使用频道搜索功能：\n' +
                  '• /channels - 查看已加入频道\n' +
                  '• /channel_connect 频道名 - 连接频道\n' +
                  '• /channel_search_keyword 关键词 - 搜索内容';

                await this.sendMessage(loginState.chatId, successMsg);
              } else if (result.needsPassword) {
                loginState.step = 'password';
                const passwordMsg = 
                  '🔑 需要两步验证密码\n\n' +
                  '第3步：输入两步验证密码\n' +
                  '请回复您的两步验证密码\n\n' +
                  '⚠️ 密码不会被保存\n' +
                  '发送 /cancel 取消登录';
                
                await this.sendMessage(loginState.chatId, passwordMsg);
              }
            } catch (signInError) {
              this.logger.error('登录失败:', signInError);
              
              // 检查是否是验证码过期错误
              const errorMsg = signInError.message || String(signInError);
              if (errorMsg.includes('PHONE_CODE_EXPIRED')) {
                // 验证码过期，自动重新申请新验证码
                loginState.codeAttempts = 0; // 重置尝试计数
                
                try {
                  const resendResult = await this.userClient.resendCode();
                  loginState.phoneCodeHash = resendResult.phoneCodeHash;
                  loginState.step = 'code';
                  
                  const expiredMsg = 
                    '⏱️ 验证码已过期，已自动重新发送\n\n' +
                    '请重新查看新的验证码\n' +
                    '打开 Telegram 应用查看新验证码\n\n' +
                    '⚡ 请立即输入 6 位数字验证码\n' +
                    '⏰ 新验证码有效期为 5 分钟\n\n' +
                    '发送 /cancel 取消登录';

                  await this.sendMessage(loginState.chatId, expiredMsg);
                } catch (resendError) {
                  this.logger.error('重新发送验证码失败:', resendError);
                  loginState.step = 'phone';
                  const failedMsg = 
                    '❌ 验证码已过期，重新申请失败\n\n' +
                    '请从头开始，回复您的 Telegram 电话号码：\n' +
                    '例如：+861234567890\n\n' +
                    '发送 /cancel 取消登录';

                  await this.sendMessage(loginState.chatId, failedMsg);
                }
              } else if (errorMsg.includes('PHONE_CODE_INVALID')) {
                // 验证码错误 - 增加重试计数
                loginState.codeAttempts = (loginState.codeAttempts || 0) + 1;
                
                if (loginState.codeAttempts >= 3) {
                  // 超过3次重试，强制重新获取验证码
                  loginState.step = 'phone';
                  loginState.codeAttempts = 0;
                  await this.sendMessage(loginState.chatId,
                    '❌ 验证码错误次数过多（已尝试3次）\n\n' +
                    '请重新发送验证码，回复您的 Telegram 电话号码：\n' +
                    '例如：+861234567890\n\n' +
                    '发送 /cancel 取消登录',
                    { parse_mode: 'Markdown' }
                  );
                } else {
                  // 允许重试
                  const remaining = 3 - loginState.codeAttempts;
                  await this.sendMessage(loginState.chatId,
                    `❌ 验证码错误（剩余${remaining}次尝试）\n\n` +
                    '请检查验证码是否正确并重新输入\n' +
                    `还可以尝试 ${remaining} 次，超过后需要重新获取验证码\n\n` +
                    '发送 /cancel 取消登录'
                  );
                }
              } else {
                // 其他错误
                clearTimeout(loginState.timeout);
                this.loginStates.delete(userId);
                await this.sendMessage(loginState.chatId, 
                  `❌ 登录失败: ${errorMsg}\n\n` +
                  `请稍后重试或使用 login-user.js 脚本手动登录`
                );
              }
            }
            break;

          case 'password':
            loginState.step = 'waiting_password_signin';
            await this.sendMessage(loginState.chatId, '🔐 正在验证密码并登录...');

            try {
              const result = await this.userClient.signInWithPassword(text);

              if (result.success) {
                // 登录成功
                clearTimeout(loginState.timeout);
                this.loginStates.delete(userId);
                this.userClient.isConnected = true;

                await this.sendMessage(loginState.chatId,
                  '🎉 *登录成功！*\n\n' +
                  '✅ Telegram 用户账号已连接\n' +
                  '✅ 会话已保存到 session.txt\n\n' +
                  '现在您可以使用频道搜索功能：\n' +
                  '• /channels - 查看已加入频道\n' +
                  '• /channel_connect 频道名 - 连接频道\n' +
                  '• /channel_search_keyword 关键词 - 搜索内容',
                  { parse_mode: 'Markdown' }
                );
              }
            } catch (passwordError) {
              this.logger.error('密码登录失败:', passwordError);
              clearTimeout(loginState.timeout);
              this.loginStates.delete(userId);
              await this.sendMessage(loginState.chatId, `❌ 密码验证失败: ${passwordError.message}\n\n请检查密码是否正确，或使用 login-user.js 脚本手动登录`);
            }
            break;
        }
      } catch (error) {
        this.logger.error('登录流程错误:', error);
        clearTimeout(loginState.timeout);
        this.loginStates.delete(userId);
        await this.sendMessage(loginState.chatId, `❌ 登录过程中发生错误: ${error.message}\n\n请使用 login-user.js 脚本手动登录`);
      }
    });
  }

  /**
   * 检查用户是否在登录流程中
   */
  isUserInLoginFlow(userId) {
    return this.loginStates.has(userId);
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
    
    // 从数据库获取真实持久化统计
    const dbStats = this.databaseManager ? this.databaseManager.getQueueStats() : null;
    const historyStats = this.databaseManager ? this.databaseManager.getDownloadHistoryStats() : null;
    const forwardStats = this.databaseManager ? this.databaseManager.getForwardedQueueStats() : null;

    // 下载统计逻辑：
    // 1. 成功数：取自历史记录表
    // 2. 总计：已完成的 + 正在队列中的
    const successCount = historyStats ? historyStats.total : (stats.completed || 0);
    const pendingInQueue = dbStats ? (dbStats.pending + dbStats.downloading + dbStats.failed) : (stats.total - stats.completed);
    
    const downloading = {
      total: successCount + pendingInQueue,
      success: successCount,
      failed: dbStats ? dbStats.failed : (stats.failed || 0),
      skipped: stats.skipped || 0
    };

    // 转发统计
    const forward = {
      total: forwardStats ? forwardStats.total : 0,
      success: forwardStats ? forwardStats.completed : 0,
      failed: forwardStats ? (forwardStats.total - forwardStats.pending - forwardStats.downloading - forwardStats.completed) : 0,
    };

    let message = `🤖 <b>Telegram Media Downloader</b>\n`;
    message += `🌐 Version: 2.1.7\n\n`;

    // 下载状态
    const totalDownloadedSize = active.length > 0 ? this.getTotalDownloadedSize(active) : 0;
    message += `📥 <b>Downloading: ${this.formatBytes(totalDownloadedSize)}</b>\n`;
    message += `  📁 Total: ${downloading.total}\n`;
    message += `  ✅ Success: ${downloading.success}\n`;
    message += `  ❌ Failed: ${downloading.failed}\n`;
    message += `  ⏭️ Skipped: ${downloading.skipped}\n\n`;

    // 转发状态
    message += `📤 <b>Forward</b>\n`;
    message += `  📁 Total: ${forward.total}\n`;
    message += `  ✅ Success: ${forward.success}\n`;
    message += `  ❌ Failed: ${forward.failed}\n\n`;

    // 下载进度
    if (active.length > 0) {
      message += `📊 <b>Download Progresses:</b>\n`;
      for (const download of active.slice(0, 5)) { // 最多显示5个
        const fileName = download.fileName || (download.filePath ? 
          download.filePath.split(/[/\\]/).pop() : 
          `${download.messageId || 'unknown'}.mp4`);
        const size = download.fileSize || 0;
        const speed = download.speed || 0;
        const progress = download.progress || 0;

        message += `  🆔 Message ID: ${download.messageId || 'unknown'}\n`;
        message += `  📁 : ${this.escapeHTML(fileName)}\n`;
        if (size > 0) {
          message += `  📄 : ${this.formatBytes(size)}\n`;
        }
        if (speed > 0) {
          message += `  🚀 : ${this.formatBytes(speed)}/s\n`;
        }
        message += `  📈 : ${this.formatProgressBar(progress)} (${progress}%)\n\n`;
      }
    } else {
      message += `📊 <b>Download Progresses:</b>\n`;
      message += `  <i>暂无进行中的下载任务</i>\n`;
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

  /**
   * 转义 HTML 特殊字符
   */
  escapeHTML(text) {
    if (!text) return '';
    return text
      .toString()
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
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
