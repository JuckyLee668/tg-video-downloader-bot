import { fileURLToPath } from 'url';
import { dirname } from 'path';
import chalk from 'chalk';
import { createLogger, format, transports } from 'winston';
import { parseProxy } from './utils.js';
import { DownloadManager } from './downloadManager.js';
import { BotHandler } from './botHandler.js';
import { ConfigManager } from './configManager.js';
import { TelegramApiClient } from './telegramApiClient.js';
import { TelegramUserClient } from './channelClient.js';
import { DownloadHistory } from './downloadHistory.js';
import { MessageRateLimiter } from './messageRateLimiter.js';
import { ForwardedQueue } from './forwardedQueue.js';
import { UnfinishedDownloadManager } from './unfinishedDownloadManager.js';
import { DatabaseManager } from './databaseManager.js';
import { WebServer } from './webServer.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// 创建日志记录器
const logger = createLogger({
  level: 'info',
  format: format.combine(
    format.timestamp({ format: 'YYYY-MM-DD HH:mm:ss' }),
    format.errors({ stack: true }),
    format.simple()
  ),
  transports: [
    new transports.File({ filename: 'error.log', level: 'error' }),
    new transports.File({ filename: 'combined.log' }),
    new transports.Console({
      format: format.combine(
        format.colorize(),
        format.simple()
      )
    })
  ]
});

class TelegramMediaDownloader {
  constructor() {
    this.apiClient = null;
    this.config = null;
    this.downloadManager = null;
    this.botHandler = null;
    this.downloadHistory = null;
    this.messageRateLimiter = null;
    this.forwardedQueue = null; // New forwarded queue
    this.unfinishedDownloadManager = null; // New unfinished download manager
    this.databaseManager = null; // SQLite database manager
    this.webServer = null; // Web server for admin interface
    // 存储每个下载任务的消息信息，用于完成后发送消息
    this.downloadTasks = new Map(); // taskId -> { chatId, messageId, fileName, isPrivateChat }
  }

  async init() {
    try {
      // 加载配置
      this.config = ConfigManager.loadConfig();
      logger.info('配置加载成功');

      // 初始化 SQLite 数据库管理器
      this.databaseManager = new DatabaseManager(this.config, logger);
      logger.info(chalk.green('✓ SQLite 数据库管理器已初始化'));

      // 初始化远程 Telegram Bot API 客户端
      this.apiClient = new TelegramApiClient(this.config, logger);
      await this.apiClient.checkConnection();
      logger.info(chalk.green('✓ 远程 Telegram Bot API 连接成功'));

      // 如果配置了 user_api，则额外创建一个普通 user 客户端用于频道搜索/下载
      this.userClient = null;
      if (this.config.user_api && this.config.user_api.api_id && this.config.user_api.api_hash) {
        try {
          const proxy = this.config.user_api.proxy || parseProxy();
          this.userClient = new TelegramUserClient(
            parseInt(this.config.user_api.api_id),
            this.config.user_api.api_hash,
            proxy
          );
          this.userClient.init();
          const status = await this.userClient.checkConnection();
          if (status.connected) {
            logger.info(chalk.green('✓ Telegram 用户客户端已连接 (频道功能可用)'));
          } else {
            logger.warn('Telegram 用户客户端尚未登录:', status.error);
            logger.warn('📌 Telegram 用户账号账号必须登录后才能使用频道搜索功能');
            logger.warn('推荐方式: 运行 node login-user.js 脚本进行本地登录');
            logger.warn('备选方式: 在 Bot 中发送 /login 命令（不推荐，安全性较低）');
          }
        } catch (err) {
          logger.error('创建 Telegram 用户客户端失败:', err.message);
          logger.error('堆栈:', err.stack);
          logger.error('请确保 USER_API_ID 和 USER_API_HASH 正确设置在 .env 文件中');
        }
      } else {
        logger.warn('未配置 user_api，频道功能将不可用（USER_API_ID 或 USER_API_HASH 未设置）');
      }

      // 初始化下载历史记录
      this.downloadHistory = new DownloadHistory(this.config, logger, this.databaseManager);
      logger.info(chalk.green('✓ 下载历史记录已初始化'));

      // 初始化转发队列
      this.forwardedQueue = new ForwardedQueue(this.config, logger, this.databaseManager);
      logger.info(chalk.green('✓ 转发待下载队列已初始化'));

      // 初始化未完成下载管理器
      this.unfinishedDownloadManager = new UnfinishedDownloadManager(this.config, logger, this.downloadHistory, this.forwardedQueue, this.databaseManager);
      logger.info(chalk.green('✓ 未完成下载管理器已初始化'));

      // 初始化消息发送限流器（更保守的配置）
      this.messageRateLimiter = new MessageRateLimiter(logger, {
        maxPerSecond: 5,        // 全局每秒最多 5 条
        maxPerMinute: 15,        // 全局每分钟最多 15 条
        maxPerChatPerSecond: 1   // 同一聊天每秒最多 1 条
      });
      logger.info(chalk.green('✓ 消息限流器已初始化（保守模式）'));

      // 设置消息监听（用于接收新消息）
      this.setupMessageListener();

      // 初始化下载管理器（传入下载历史记录和可选的 userClient）
      this.downloadManager = new DownloadManager(this.config, logger, this.apiClient, this.downloadHistory, this.userClient, this.databaseManager);
      await this.downloadManager.init();

      // 恢复未完成的下载任务（重构版）
      await this.restoreUnfinishedDownloads();

      // 设置下载完成监听
      this.setupDownloadCompleteListener();

      // 初始化 Bot（如果配置了 bot_token）
      if (this.config.bot_token && this.config.bot_token !== 'your_bot_token') {
        this.botHandler = new BotHandler(
          this.config,
          this.downloadManager,
          logger,
          this.apiClient,
          this.messageRateLimiter,
          this.userClient,
          this.databaseManager
        );
        await this.botHandler.init();
      }

      // 注意：Bot API 不支持直接获取历史消息
      // 如果需要处理历史消息，需要通过远程服务器提供的其他接口
      // 或者通过消息监听来处理新消息
      logger.info('Bot API 模式：将通过消息监听处理新消息');

      // 初始化Web服务器
      this.webServer = new WebServer(this);
      this.webServer.start();
      logger.info(chalk.green('✓ Web管理界面已启动'));

      logger.info(chalk.green('✓ Telegram Media Downloader 启动成功'));

      // Start periodic check for pending forwarded downloads
      // this.startForwardedQueueProcessor(); // 禁用以防止自动转发文件
    } catch (error) {
      logger.error('初始化失败:', error);
      process.exit(1);
    }
  }

  /**
   * Start continuous processor for forwarded queue items (one-time check and continuous processing until done)
   */
  startForwardedQueueProcessor() {
    // Run continuous processing asynchronously to avoid blocking startup
    setImmediate(async () => {
      try {
        await this.continuousProcessPendingForwardedDownloads();
      } catch (error) {
        logger.error('启动连续处理转发队列失败:', error);
      }
    }); // Execute immediately but in the next tick to avoid blocking startup

    logger.info('转发队列连续处理器已启动 (一次性检查并持续处理直到完成)');
  }

  /**
   * Stop the forwarded queue processor
   */
  stopForwardedQueueProcessor() {
    if (this.forwardedQueueProcessorInterval) {
      clearInterval(this.forwardedQueueProcessorInterval);
      this.forwardedQueueProcessorInterval = null;
      logger.info('转发队列处理器已停止');
    }
  }

  /**
   * 设置下载完成监听器
   */
  setupDownloadCompleteListener() {
    // 消息发送队列（避免同时发送多个消息）
    this.messageQueue = [];
    this.isProcessingMessages = false;

    this.downloadManager.on('complete', async (data) => {
      const { taskId, filePath, status, chatId, fileName } = data;
      const taskInfo = this.downloadTasks.get(taskId);

      // 使用 taskInfo 或 data 中的信息
      const finalChatId = taskInfo?.chatId || chatId;
      const finalFileName = taskInfo?.fileName || fileName || '文件';
      const isPrivateChat = taskInfo?.isPrivateChat || false;

      // 如果没有 chatId，尝试从 taskId 解析
      let effectiveChatId = finalChatId;
      let effectiveMessageId = null;

      if (!effectiveChatId && taskId) {
        // Try to parse from taskId (format: "chatId_messageId")
        const parts = taskId.split('_');
        if (parts.length >= 2) {
          effectiveChatId = parts[0];
          effectiveMessageId = parts.slice(1).join('_'); // messageId might itself contain underscores
        }
      }

      // 如果仍然没有 chatId，无法发送消息
      if (!effectiveChatId) {
        logger.warn(`无法发送完成消息：缺少 chatId (taskId: ${taskId})`);
        if (taskInfo) {
          this.downloadTasks.delete(taskId);
        }
        return;
      }

      // Try to get messageId from other sources if not available
      if (!effectiveMessageId) {
        if (data.messageId) {
          effectiveMessageId = data.messageId;
        } else if (taskInfo?.messageId) {
          effectiveMessageId = taskInfo.messageId;
        } else if (taskId) {
          const parts = taskId.split('_');
          if (parts.length >= 2) {
            effectiveMessageId = parts[1];
          }
        }
      }

      const bot = this.apiClient.getBot();

      // 确定是否应该发送完成消息
      // 1. 私聊消息（直接发送文件给bot，或在私聊中转发）
      // 2. 在配置的聊天中接收的消息（但避免在公共群组/频道中发送消息影响其他人）
      // 对于已配置的聊天（即用户明确添加到配置中），我们也应该发送通知
      const isConfiguredChat = this.config.chat?.some(c => {
        const configChatId = c.chat_id.toString();
        return configChatId === effectiveChatId || configChatId === `-${effectiveChatId}`;
      }) || false;

      // 仅在私聊或在配置的聊天中发送通知
      // 注意：对于公开的群组/频道，我们不会发送通知以避免影响其他成员
      const shouldSendNotification = isPrivateChat || isConfiguredChat;

      // 记录通知决策
      logger.info(`下载完成通知决策: taskId=${taskId}, chatId=${effectiveChatId}, isPrivateChat=${isPrivateChat}, isConfiguredChat=${isConfiguredChat}, shouldSend=${shouldSendNotification}, status=${status}`);

      if (shouldSendNotification) {
        // 将消息添加到队列
        this.messageQueue.push({
          bot,
          chatId: effectiveChatId,
          status,
          filePath,
          fileName: finalFileName,
          error: data.error,
          errorDetails: data.errorDetails
        });

        // 启动消息处理队列
        this.processMessageQueue();
      } else {
        logger.info(`跳过发送完成消息: taskId=${taskId}, chatId=${effectiveChatId}, 原因: 不满足通知条件`);
      }

      // 清理任务信息
      if (taskInfo) {
        this.downloadTasks.delete(taskId);
        logger.debug(`已清理任务信息: ${taskId}`);
      }

      // 检查是否在转发队列中并相应处理
      if (effectiveMessageId) {
        // Check if this item exists in the forwarded queue and remove if completed
        if (this.forwardedQueue.isInQueue(effectiveChatId, effectiveMessageId)) {
          if (status === 'completed' || status === 'skipped') {
            // Check if file actually exists and has content before removing from queue
            // For skipped files, we should also remove them from queue since they were intentionally skipped
            try {
              if (status === 'completed') {
                // For completed files, verify that the file exists and has content
                const fs = await import('fs');
                if (filePath && fs.existsSync(filePath)) {
                  const stats = fs.statSync(filePath);
                  if (stats.size > 0) {
                    // File exists and has content, update queue status and remove
                    this.forwardedQueue.updateStatus(effectiveChatId, effectiveMessageId, 'completed');
                    this.forwardedQueue.removeFromQueue(effectiveChatId, effectiveMessageId);
                    logger.info(`转发文件${status === 'completed' ? '下载完成' : '已跳过'}, 已从待下载队列移除: ${effectiveChatId}_${effectiveMessageId} (文件大小: ${this.formatBytes(stats.size)})`);
                  } else {
                    logger.warn(`转发文件${status === 'completed' ? '下载完成但' : ''}大小为0，保留在队列中: ${effectiveChatId}_${effectiveMessageId}`);
                    // Keep in queue for possible retry
                    this.forwardedQueue.updateStatus(effectiveChatId, effectiveMessageId, 'pending');
                  }
                } else {
                  logger.warn(`转发文件不存在，保留在队列中: ${effectiveChatId}_${effectiveMessageId}`);
                  // Keep in queue for possible retry
                  this.forwardedQueue.updateStatus(effectiveChatId, effectiveMessageId, 'pending');
                }
              } else {
                // For skipped files (already exists in history), we can safely remove from queue
                this.forwardedQueue.updateStatus(effectiveChatId, effectiveMessageId, 'completed');
                this.forwardedQueue.removeFromQueue(effectiveChatId, effectiveMessageId);
                logger.info(`转发文件已跳过（已存在），已从待下载队列移除: ${effectiveChatId}_${effectiveMessageId}`);
              }
            } catch (error) {
              logger.error(`检查转发文件状态时出错: ${effectiveChatId}_${effectiveMessageId}`, error);
              if (status === 'completed') {
                // For completed files, if there's an error checking file status, still remove from queue
                // since the download completed successfully (otherwise it would be 'failed' status)
                this.forwardedQueue.updateStatus(effectiveChatId, effectiveMessageId, 'completed');
                this.forwardedQueue.removeFromQueue(effectiveChatId, effectiveMessageId);
                logger.info(`转发文件${status === 'completed' ? '下载完成' : '已跳过'}, 已从待下载队列移除 (错误检查): ${effectiveChatId}_${effectiveMessageId}`);
              } else {
                // For skipped files, we can still safely remove from queue
                this.forwardedQueue.updateStatus(effectiveChatId, effectiveMessageId, 'completed');
                this.forwardedQueue.removeFromQueue(effectiveChatId, effectiveMessageId);
                logger.info(`转发文件已跳过（已存在），已从待下载队列移除 (错误检查): ${effectiveChatId}_${effectiveMessageId}`);
              }
            }
          }
        }
      }
    });
  }

  /**
   * 处理消息发送队列
   */
  async processMessageQueue() {
    if (this.isProcessingMessages) return;
    this.isProcessingMessages = true;

    while (this.messageQueue.length > 0) {
      const item = this.messageQueue.shift();
      const { bot, chatId, status, filePath, fileName, error, errorDetails } = item;

      // 消息之间增加间隔，避免触发限流
      await new Promise(resolve => setTimeout(resolve, 1000));

      try {
        let text;
        if (status === 'completed') {
          text = `✅ 下载完成：${fileName}\n📁 保存路径：${filePath || '未知'}`;
        } else if (status === 'skipped') {
          text = `⏭️ 文件已存在，已跳过：${fileName}`;
        } else if (status === 'failed') {
          let errorMessage = '';
          const errorDetail = error || errorDetails?.message || '';

          if (errorDetail && (errorDetail.includes('wrong file_id') || errorDetail.includes('temporarily unavailable'))) {
            errorMessage = '\n\n⚠️ 文件在 Telegram 服务器上已不可用，可能已被删除或过期。';
          } else if (errorDetail && errorDetail.includes('ENOENT')) {
            errorMessage = '\n\n⚠️ 文件保存失败，请检查保存路径权限。';
          } else if (errorDetail && (errorDetail.includes('timeout') || errorDetail.includes('超时'))) {
            errorMessage = '\n\n⚠️ 下载超时，请稍后重试。';
          } else if (errorDetail) {
            errorMessage = `\n\n错误信息：${errorDetail}`;
          }
          text = `❌ 下载失败：${fileName}${errorMessage}`;
        } else {
          // 默认情况，处理未知状态
          text = `⚠️ 未知状态：${fileName} (状态: ${status})`;
        }

        // 确保消息文本不为空
        if (!text || text.trim() === '') {
          text = `📝 处理完成：${fileName} (状态: ${status || 'unknown'})`;
        }

        await this.messageRateLimiter.sendMessage(bot, chatId, text);
      } catch (err) {
        logger.error('发送完成消息失败:', err);
        // 如果失败，将消息放回队列末尾（最多重试3次）
        if (!item.retryCount || item.retryCount < 3) {
          item.retryCount = (item.retryCount || 0) + 1;
          this.messageQueue.push(item);
          logger.info(`消息发送失败，放回队列重试 (${item.retryCount}/3)`);
        } else {
          logger.error(`消息发送失败，已重试3次，放弃: ${fileName}`);
        }
      }
    }

    this.isProcessingMessages = false;
  }


  /**
   * 设置消息监听器
   * Bot API 模式下，通过监听消息事件来处理新消息
   * 支持处理：
   * 1. 配置的频道/群组中的消息
   * 2. 用户直接发给 Bot 的私聊消息
   * 3. 转发的消息（包括私聊转发和群组转发）
   */
  setupMessageListener() {
    const bot = this.apiClient.getBot();
    
    // 监听所有消息
    bot.on('message', async (msg) => {
      try {
        // 跳过命令消息（以/开头的消息 - 由 botHandler 处理）
        if (msg.text && msg.text.startsWith('/')) {
          return;
        }

        const chatId = msg.chat.id.toString();
        const messageId = msg.message_id;
        const chatType = msg.chat.type; // 'private', 'group', 'supergroup', 'channel'
        const userId = msg.from.id;
        
        // 跳过登录流程中的用户消息
        if (this.botHandler && this.botHandler.isUserInLoginFlow && this.botHandler.isUserInLoginFlow(userId)) {
          return;
        }
        
        // 检查是否是频道消息
        const isChannelMessage = chatType === 'channel';
        
        // 调试日志：记录频道消息的详细信息
        if (isChannelMessage) {
          logger.info(`收到频道消息 - 频道: ${chatId}, 消息ID: ${messageId}, 类型: ${chatType}`);
          logger.info(`频道消息媒体字段: photo=${!!msg.photo}, video=${!!msg.video}, document=${!!msg.document}, audio=${!!msg.audio}, voice=${!!msg.voice}, animation=${!!msg.animation}`);
          if (msg.video) logger.info(`视频详情: file_name=${msg.video.file_name}, mime_type=${msg.video.mime_type}, file_id=${msg.video.file_id}`);
          if (msg.document) logger.info(`文档详情: file_name=${msg.document.file_name}, mime_type=${msg.document.mime_type}, file_id=${msg.document.file_id}`);
          if (msg.audio) logger.info(`音频详情: file_name=${msg.audio.file_name}, mime_type=${msg.audio.mime_type}, file_id=${msg.audio.file_id}`);
        }
        
        if (!mediaType || !this.config.media_types.includes(mediaType)) {
          // 如果是转发消息但没有检测到媒体，记录详细信息
          if (isForwarded && !mediaType) {
            logger.warn(`转发消息未检测到媒体 - 消息ID: ${messageId}, 可能原因: 转发的消息不包含媒体或媒体格式不支持`);
            // 如果是私聊转发，给用户提示
            if (isPrivateChat) {
              try {
                await this.messageRateLimiter.sendMessage(
                  bot,
                  chatId, 
                  '⚠️ 转发的消息未检测到媒体文件。\n\n' +
                  '请确保：\n' +
                  '1. 转发的消息包含媒体文件（视频、图片、文档等）\n' +
                  '2. 媒体类型在支持列表中：视频、音频、文档、图片、语音、动画'
                );
              } catch (e) {
                logger.error('发送提示消息失败:', e);
              }
            }
          }
          
          // 如果是私聊但没有媒体，可以发送提示消息
          if (isPrivateChat && !mediaType && !isForwarded) {
            try {
              await this.messageRateLimiter.sendMessage(
                bot,
                chatId, 
                '👋 你好！请发送媒体文件（视频、图片、文档等）给我，我会自动下载。\n\n' +
                '支持的媒体类型：视频、音频、文档、图片、语音、动画\n\n' +
                '💡 提示：转发带媒体的消息也可以下载！'
              );
            } catch (e) {
              // 忽略发送消息错误
            }
          }
          return;
        }

        // 检查文件格式
        const shouldDownload = this.shouldDownloadFileFromMessage(msg, mediaType);
        if (!shouldDownload) {
          logger.warn(`文件格式过滤：消息 ${messageId} 的媒体类型 ${mediaType} 不符合文件格式要求`);
          logger.warn(`消息详情: video=${!!msg.video}, document=${!!msg.document}`);
          if (msg.video) logger.warn(`视频信息: file_name=${msg.video.file_name || '无'}, mime_type=${msg.video.mime_type || '无'}`);
          if (msg.document) logger.warn(`文档信息: file_name=${msg.document.file_name || '无'}, mime_type=${msg.document.mime_type || '无'}`);
          if (isPrivateChat) {
            try {
              await this.messageRateLimiter.sendMessage(
                bot,
                chatId, 
                `⚠️ 文件格式不符合要求，已跳过下载。\n` +
                `媒体类型: ${mediaType}\n` +
                `配置的格式: ${JSON.stringify(this.config.file_formats[mediaType] || [])}`
              );
            } catch (e) {
              // 忽略发送消息错误
            }
          }
          return;
        }

        // 应用下载过滤器（仅对配置的聊天应用）
        if (chatConfig && chatConfig.download_filter) {
          const messageDate = new Date(msg.date * 1000);
          if (!this.evaluateFilter({ date: msg.date }, chatConfig.download_filter)) {
            return;
          }
        }

        // 获取聊天标题
        let chatTitle;
        if (isPrivateChat) {
          chatTitle = msg.chat.first_name || msg.chat.username || `用户_${chatId}`;
        } else {
          chatTitle = msg.chat.title || msg.chat.first_name || `Chat_${chatId}`;
        }

        // 如果是转发的消息，在标题中标注
        if (isForwarded) {
          const forwardFrom = msg.forward_from_chat?.title || 
                             msg.forward_from?.first_name || 
                             msg.forward_from?.username || 
                             '未知来源';
          chatTitle = `${chatTitle} [转发自: ${forwardFrom}]`;
        }

        // 获取 file_id
        const fileId = this.getFileIdFromMessage(msg, mediaType);
        if (!fileId) {
          logger.error(`无法获取 file_id - 消息ID: ${messageId}, 媒体类型: ${mediaType}`);
          if (isPrivateChat) {
            try {
              await this.messageRateLimiter.sendMessage(
                bot,
                chatId, 
                `❌ 无法获取文件ID，下载失败。\n媒体类型: ${mediaType}`
              );
            } catch (e) {
              // 忽略发送消息错误
            }
          }
          return;
        }

        logger.info(`准备添加到下载队列 - 消息ID: ${messageId}, 媒体类型: ${mediaType}, file_id: ${fileId}`);

        // 获取文件名
        const fileName = this.getFileNameFromMessage(msg, mediaType) || '文件';
        const taskId = `${chatId}_${messageId}`;
        
        // 添加到下载队列
        try {
          await this.downloadManager.addDownloadTask({
            message: msg,
            chatId,
            chatTitle,
            mediaType,
            fileId: fileId,
            fileName: fileName, // 传递文件名
          });

          // 如果是转发的消息，添加到转发队列
          if (isForwarded) {
            const forwardInfo = {
              forwardFrom: msg.forward_from_chat?.title || msg.forward_from?.first_name || msg.forward_from?.username || '未知来源',
              forwardFromChatId: msg.forward_from_chat?.id || null,
              forwardDate: msg.forward_date || null
            };

            this.forwardedQueue.addToQueue(chatId, messageId, fileName, mediaType, fileId, forwardInfo);
            this.forwardedQueue.updateStatus(chatId, messageId, 'downloading');
          }

          const sourceType = isForwarded ? '转发' : (isPrivateChat ? '私聊' : chatType);
          logger.info(`✅ 新消息已添加到下载队列: ${chatTitle} (${sourceType}) - ${messageId}`);

          // 保存任务信息，用于完成后发送消息
          this.downloadTasks.set(taskId, {
            chatId,
            messageId,
            fileName,
            isPrivateChat,
            chatTitle,
            mediaType
          });
          
          // 如果是私聊，发送接收消息（使用限流器，异步发送避免阻塞）
          if (isPrivateChat) {
            // 异步发送，不阻塞主流程，让限流器按顺序处理
            (async () => {
              try {
                const forwardNote = isForwarded ? '\n📤 这是一条转发的消息' : '';
                await this.messageRateLimiter.sendMessage(
                  bot,
                  chatId, 
                  `📥 已收到文件：${fileName}\n正在下载中...${forwardNote}`
                );
              } catch (e) {
                // 429 错误已经在限流器中处理并重试，这里只记录其他严重错误
                if (e.response?.error_code !== 429 && 
                    !(e.code === 'ETELEGRAM' && e.response?.statusCode === 429)) {
                  logger.error('发送接收消息失败:', e.message);
                }
              }
            })();
          }
        } catch (error) {
          logger.error(`添加下载任务失败 - 消息ID: ${messageId}:`, error);
          if (isPrivateChat) {
            try {
              await this.messageRateLimiter.sendMessage(
                bot,
                chatId, 
                `❌ 添加下载任务失败: ${error.message}`
              );
            } catch (e) {
              // 忽略发送消息错误
            }
          }
        }
      } catch (error) {
        logger.error('处理消息时出错:', error);
      }
    });

    logger.info('消息监听器已启动（支持私聊和配置的聊天）');
  }

  /**
   * 从消息中获取文件名（用于提示）
   */
  getFileNameFromMessage(msg, mediaType) {
    let fileName = null;
    
    switch (mediaType) {
      case 'video':
        fileName = msg.video?.file_name || msg.document?.file_name;
        break;
      case 'audio':
        fileName = msg.audio?.file_name || msg.document?.file_name;
        break;
      case 'document':
        fileName = msg.document?.file_name;
        break;
      case 'animation':
        fileName = msg.animation?.file_name || msg.document?.file_name;
        break;
    }

    if (fileName) return fileName;

    // 如果没有文件名，尝试从说明文字或文本中提取关键词
    const text = msg.caption || msg.text || '';
    if (text) {
      // 提取前 30 个字符并清理非法字符
      const cleanText = text.replace(/[\n\r\s]+/g, ' ').trim();
      if (cleanText) {
        const truncated = cleanText.substring(0, 30);
        const extension = this.getDefaultExtension(mediaType);
        return `${truncated}${extension}`;
      }
    }

    // 最后的保底名称
    const defaultNames = {
      'video': '视频',
      'audio': '音频',
      'document': '文档',
      'photo': '图片',
      'voice': '语音',
      'animation': '动画'
    };
    
    return defaultNames[mediaType] || '文件';
  }

  /**
   * 根据媒体类型获取默认扩展名
   */
  getDefaultExtension(mediaType) {
    const extensions = {
      'video': '.mp4',
      'audio': '.mp3',
      'photo': '.jpg',
      'voice': '.ogg',
      'document': '',
      'animation': '.mp4'
    };
    return extensions[mediaType] || '';
  }

  /**
   * 从 Bot API 消息对象中提取媒体类型
   */
  getMediaTypeFromMessage(msg) {
    if (msg.photo) return 'photo';
    if (msg.video) return 'video';
    if (msg.audio) return 'audio';
    if (msg.voice) return 'voice';
    if (msg.document) {
      const mimeType = msg.document.mime_type || '';
      if (mimeType.startsWith('video/')) return 'video';
      if (mimeType.startsWith('audio/')) return 'audio';
      if (mimeType === 'image/gif' || msg.document.thumb) {
        // 检查是否是动画
        if (msg.document.file_name && msg.document.file_name.endsWith('.gif')) {
          return 'animation';
        }
      }
      return 'document';
    }
    if (msg.animation) return 'animation';
    return null;
  }

  /**
   * 从消息中获取 file_id
   */
  getFileIdFromMessage(msg, mediaType) {
    switch (mediaType) {
      case 'photo':
        // 照片可能有多个尺寸，取最大的
        return msg.photo ? msg.photo[msg.photo.length - 1].file_id : null;
      case 'video':
        return msg.video?.file_id || msg.document?.file_id;
      case 'audio':
        return msg.audio?.file_id || msg.document?.file_id;
      case 'voice':
        return msg.voice?.file_id;
      case 'document':
        return msg.document?.file_id;
      case 'animation':
        return msg.animation?.file_id || msg.document?.file_id;
      default:
        return null;
    }
  }

  /**
   * 检查是否应该下载文件（从 Bot API 消息对象）
   */
  shouldDownloadFileFromMessage(msg, mediaType) {
    const fileFormats = this.config.file_formats[mediaType];
    if (!fileFormats || fileFormats.includes('all')) {
      return true;
    }

    // 对于 video 类型，检查 msg.video 或 msg.document
    if (mediaType === 'video') {
      if (msg.video) {
        // 如果配置了格式过滤，检查视频格式
        const fileName = msg.video.file_name || '';
        const mimeType = msg.video.mime_type || '';
        const extension = fileName.split('.').pop() || '';

        // 如果没有文件名和 MIME 类型信息，默认允许下载（可能是转发的消息）
        if (!fileName && !mimeType) {
          logger.info(`视频消息缺少格式信息，默认允许下载 - file_id: ${msg.video.file_id}`);
          return true;
        }

        for (const format of fileFormats) {
          if (mimeType.includes(format) || extension === format) {
            return true;
          }
        }
        // 如果没有匹配的格式，返回 false
        logger.warn(`视频格式不匹配 - file_name: ${fileName}, mime_type: ${mimeType}, 配置格式: ${JSON.stringify(fileFormats)}`);
        return false;
      }
      // 如果 video 是通过 document 发送的
      if (msg.document) {
        const mimeType = msg.document.mime_type || '';
        const fileName = msg.document.file_name || '';
        const extension = fileName.split('.').pop() || '';

        // 如果没有文件名和 MIME 类型信息，默认允许下载
        if (!fileName && !mimeType) {
          logger.info(`视频文档缺少格式信息，默认允许下载 - file_id: ${msg.document.file_id}`);
          return true;
        }

        for (const format of fileFormats) {
          if (mimeType.includes(format) || extension === format) {
            return true;
          }
        }
        logger.warn(`视频文档格式不匹配 - file_name: ${fileName}, mime_type: ${mimeType}, 配置格式: ${JSON.stringify(fileFormats)}`);
        return false;
      }
    }

    // 对于其他类型，检查 document
    if (msg.document) {
      const mimeType = msg.document.mime_type || '';
      const fileName = msg.document.file_name || '';
      const extension = fileName.split('.').pop() || '';

      for (const format of fileFormats) {
        if (mimeType.includes(format) || extension === format) {
          return true;
        }
      }
    }

    // 对于 photo、voice、audio 等，如果没有格式限制或格式匹配，允许下载
    if (mediaType === 'photo' || mediaType === 'voice' || mediaType === 'audio') {
      // 这些类型通常不需要格式过滤，或者已经在 media_types 中过滤了
      return true;
    }

    return false;
  }

  async startDownloadTasks() {
    // Bot API 模式下，历史消息需要通过其他方式获取
    // 这里保留接口以便未来扩展
    logger.info('Bot API 模式：历史消息需要通过消息监听或远程服务器接口获取');
  }

  /**
   * 恢复未完成的下载任务（断点续传）
   * 在程序启动时调用，检查历史记录中状态为 'in_progress' 的任务
   */
  async restoreIncompleteDownloads() {
    // 首先清理可能无效的记录（文件不存在或记录过期）
    await this.downloadHistory.cleanInvalidRecords();

    const incomplete = this.downloadHistory.getIncompleteDownloads();

    if (incomplete.length === 0) {
      logger.info('没有未完成的下载任务');
    } else {
      logger.info(`发现 ${incomplete.length} 个未完成的下载任务，开始恢复...`);

      // Check the download manager's current queue and active downloads to avoid duplicates
      const currentQueueIds = new Set(this.downloadManager.downloadQueue.map(task => `${task.chatId}_${task.messageId}`));
      const currentActiveIds = new Set(Array.from(this.downloadManager.activeDownloads.keys()));

      for (const task of incomplete) {
        try {
          // Check if the task already exists in queue or active downloads
          const taskId = `${task.chatId}_${task.messageId || Date.now()}`;
          const duplicateInQueue = this.downloadManager.downloadQueue.some(t =>
            t.chatId === task.chatId &&
            (t.messageId === task.messageId ||
             (t.fileId && task.fileId && t.fileId === task.fileId))
          );
          const duplicateInActive = this.downloadManager.activeDownloads.has(taskId) ||
            Array.from(this.downloadManager.activeDownloads.values()).some(t =>
              t.chatId === task.chatId &&
              (t.messageId === task.messageId ||
               (t.fileId && task.fileId && t.fileId === task.fileId))
            );

          if (duplicateInQueue || duplicateInActive) {
            logger.warn(`恢复任务已存在，跳过重复添加: ${task.fileName || '文件'} (chatId: ${task.chatId}, messageId: ${task.messageId})`);
            continue;
          }

          // 检查本地文件是否存在
          const fs = await import('fs');
          if (!task.filePath || !fs.existsSync(task.filePath)) {
            logger.warn(`文件不存在，无法恢复: ${task.filePath}`);
            // 从历史记录中移除这个无效记录
            const key = `file_${task.fileId}`;
            delete this.downloadHistory.history[key];
            this.downloadHistory.saveHistory();
            continue;
          }

          const fileStats = fs.statSync(task.filePath);
          const localFileSize = fileStats.size;

          logger.info(`恢复下载任务: ${task.fileName || '文件'} (本地: ${this.formatBytes(localFileSize)}, 历史记录: ${this.formatBytes(task.downloadedBytes)})`);

          // 创建模拟消息对象（只需要 fileId 和基本信息）
          const mockMessage = {
            message_id: task.messageId,
            document: task.fileId ? { file_id: task.fileId } : undefined,
            video: task.fileId ? { file_id: task.fileId } : undefined,
            date: Math.floor(Date.now() / 1000) // 使用当前时间作为近似值
          };

          // 添加到下载队列
          await this.downloadManager.addDownloadTask({
            message: mockMessage,
            chatId: task.chatId,
            chatTitle: `恢复任务_${task.chatId}`,
            mediaType: 'video', // 默认使用 video，实际下载时会从消息中获取
            fileId: task.fileId,
            fileName: task.fileName || '恢复下载',
            needRefreshFileId: true // 标记需要刷新 file_id（因为服务器可能已删除原文件）
          });

          logger.info(`已加入恢复队列: ${task.fileName || task.filePath} (将尝试刷新 file_id)`);
        } catch (error) {
          logger.error(`恢复下载任务失败: ${task.filePath}`, error);
          // 如果是 file_id 无效错误，清理该记录
          if (error.message?.includes('wrong file_id') || error.message?.includes('temporarily unavailable')) {
            const key = `file_${task.fileId}`;
            delete this.downloadHistory.history[key];
            this.downloadHistory.saveHistory();
            logger.warn(`已清理无效的 file_id 记录: ${task.fileId?.substring(0, 30)}...`);
          }
        }
      }

      logger.info(`已尝试恢复 ${incomplete.length} 个未完成的下载任务`);
    }

    // Also restore any pending forwarded downloads from the queue
    const forwardedQueueStatus = this.getForwardedQueueStatus();
    if (forwardedQueueStatus.pending > 0) {
      logger.info(`发现 ${forwardedQueueStatus.pending} 个转发待下载任务，开始恢复...`);

      for (const item of forwardedQueueStatus.items) {
        if (item.status === 'pending') {
          logger.info(`检测到待处理的转发消息: ${item.fileName} (Chat: ${item.chatId}, Message: ${item.messageId})`);

          // In a real scenario, we'd want to somehow re-fetch the original message
          // and process it again. For now, we'll mark it as downloading to indicate
          // it's being processed.
          this.forwardedQueue.updateStatus(item.chatId, item.messageId, 'downloading');
          logger.info(`已标记转发任务为下载中: ${item.fileName} (ID: ${item.messageId})`);
        }
      }
    }
  }

  /**
   * Process pending forwarded downloads (check periodically)
   */
  async processPendingForwardedDownloads() {
    logger.info('开始处理转发队列...');
    const forwardedQueueStatus = this.getForwardedQueueStatus();
    logger.info(`转发队列状态: 总共 ${forwardedQueueStatus.total}, pending: ${forwardedQueueStatus.pending}, downloading: ${forwardedQueueStatus.downloading}`);

    // Check the download manager's current queue and active downloads to avoid duplicates
    const currentQueueIds = new Set(this.downloadManager.downloadQueue.map(task => `${task.chatId}_${task.messageId}`));
    const currentActiveIds = new Set(Array.from(this.downloadManager.activeDownloads.keys()));

    for (const item of forwardedQueueStatus.items) {
      const taskId = `${item.chatId}_${item.messageId}`;
      logger.debug(`检查转发项: ${item.fileName} (ID: ${item.messageId}), 状态: ${item.status}, 任务ID: ${taskId}`);

      // Check if already in download manager's current queue or active downloads
      const isCurrentlyInQueue = this.downloadManager.downloadQueue.some(task =>
        `${task.chatId}_${task.messageId}` === taskId
      );
      const isCurrentlyDownloading = this.downloadManager.activeDownloads.has(taskId);

      // Process 'pending' items normally
      if (item.status === 'pending') {
        logger.info(`准备处理: ${item.fileName} (状态: ${item.status})`);

        try {
          this.forwardedQueue.updateStatus(item.chatId, item.messageId, 'downloading');
          logger.info(`状态更新为 downloading: ${item.fileName}`);

          // Create synthetic message object
          const syntheticMessage = {
            message_id: item.messageId,
            date: Math.floor(Date.now() / 1000),
          };

          // Determine media type
          let mediaType = item.mediaType || 'document';

          // Add media-specific object based on media type
          if (mediaType === 'video' || mediaType === 'document') {
            syntheticMessage.document = { file_id: item.fileId };
          } else if (mediaType === 'photo') {
            syntheticMessage.document = { file_id: item.fileId };
          } else if (mediaType === 'audio') {
            syntheticMessage.audio = { file_id: item.fileId };
          } else if (mediaType === 'voice') {
            syntheticMessage.voice = { file_id: item.fileId };
          } else if (mediaType === 'animation') {
            syntheticMessage.animation = { file_id: item.fileId };
          } else {
            syntheticMessage.document = { file_id: item.fileId };
          }

          // Use simpler chat title to avoid API call issues
          const simpleChatTitle = `Chat_${item.chatId}`;
          const forwardFrom = item.forwardInfo?.forwardFrom || 'Unknown Source';
          const augmentedChatTitle = `${simpleChatTitle} [转发自: ${forwardFrom}]`;

          logger.info(`尝试添加下载任务: ${item.fileName}, 文件ID: ${item.fileId.substring(0, 20)}...`);

          // Generate taskId to store in downloadTasks map for notification purposes
          const taskId = `${item.chatId}_${item.messageId}`;

          // Store task info for notification purposes - assume it's a private chat for forwarded items
          // Check if this chat is configured in the config
          const isConfiguredChat = this.config.chat?.some(c => {
            const configChatId = c.chat_id.toString();
            return configChatId === item.chatId || configChatId === `-${item.chatId}`;
          }) || false;

          // For forwarded items, consider as private if not configured in chat list
          const isPrivateChat = !isConfiguredChat;

          this.downloadTasks.set(taskId, {
            chatId: item.chatId,
            messageId: item.messageId,
            fileName: item.fileName,
            isPrivateChat: isPrivateChat,
            chatTitle: augmentedChatTitle,
            mediaType: mediaType
          });

          // Add to download manager without awaiting to avoid blocking
          // Mark this as a forced fresh download from queue to achieve "immediate start"
          this.downloadManager.addDownloadTask({
            message: syntheticMessage,
            chatId: item.chatId,
            chatTitle: augmentedChatTitle,
            mediaType: mediaType,
            fileId: item.fileId,
            fileName: item.fileName,
            // Mark this as a forced fresh download from queue
            forceFreshDownload: true
          }).then(() => {
            logger.info(`成功添加下载任务: ${item.fileName}`);
          }).catch(error => {
            logger.error(`添加下载任务失败: ${item.fileName}`, error);
            // If failed, set back to pending and remove from downloadTasks if added
            this.forwardedQueue.updateStatus(item.chatId, item.messageId, 'pending');
            this.downloadTasks.delete(taskId);
          });

          logger.info(`已发送下载请求: ${item.fileName}`);

        } catch (error) {
          logger.error(`处理转发下载异常: ${item.fileName}`, error);
          // If failed, we might want to set it back to pending or mark as failed
          this.forwardedQueue.updateStatus(item.chatId, item.messageId, 'pending');
        }
      }
      // For 'downloading' items, check if they need restart based on timeout OR user intention to "start immediately"
      else if (item.status === 'downloading' && (!isCurrentlyDownloading && !isCurrentlyInQueue)) {
        // If it's marked as downloading but not actually downloading, restart it
        logger.info(`重启下载任务: ${item.fileName} (状态为downloading但不在活动下载中)`);

        try {
          // Create synthetic message object
          const syntheticMessage = {
            message_id: item.messageId,
            date: Math.floor(Date.now() / 1000),
          };

          // Determine media type
          let mediaType = item.mediaType || 'document';

          // Add media-specific object based on media type
          if (mediaType === 'video' || mediaType === 'document') {
            syntheticMessage.document = { file_id: item.fileId };
          } else if (mediaType === 'photo') {
            syntheticMessage.document = { file_id: item.fileId };
          } else if (mediaType === 'audio') {
            syntheticMessage.audio = { file_id: item.fileId };
          } else if (mediaType === 'voice') {
            syntheticMessage.voice = { file_id: item.fileId };
          } else if (mediaType === 'animation') {
            syntheticMessage.animation = { file_id: item.fileId };
          } else {
            syntheticMessage.document = { file_id: item.fileId };
          }

          // Use simpler chat title to avoid API call issues
          const simpleChatTitle = `Chat_${item.chatId}`;
          const forwardFrom = item.forwardInfo?.forwardFrom || 'Unknown Source';
          const augmentedChatTitle = `${simpleChatTitle} [转发自: ${forwardFrom}]`;

          logger.info(`尝试重启下载任务: ${item.fileName}, 文件ID: ${item.fileId.substring(0, 20)}...`);

          // Generate taskId to store in downloadTasks map for notification purposes
          const taskId = `${item.chatId}_${item.messageId}`;

          // Store task info for notification purposes - assume it's a private chat for forwarded items
          // Check if this chat is configured in the config
          const isConfiguredChat = this.config.chat?.some(c => {
            const configChatId = c.chat_id.toString();
            return configChatId === item.chatId || configChatId === `-${item.chatId}`;
          }) || false;

          // For forwarded items, consider as private if not configured in chat list
          const isPrivateChat = !isConfiguredChat;

          // Remove any existing task info to avoid conflicts
          this.downloadTasks.delete(taskId);

          this.downloadTasks.set(taskId, {
            chatId: item.chatId,
            messageId: item.messageId,
            fileName: item.fileName,
            isPrivateChat: isPrivateChat,
            chatTitle: augmentedChatTitle,
            mediaType: mediaType
          });

          // Add to download manager without awaiting to avoid blocking
          // Mark this as a forced fresh download from queue
          this.downloadManager.addDownloadTask({
            message: syntheticMessage,
            chatId: item.chatId,
            chatTitle: augmentedChatTitle,
            mediaType: mediaType,
            fileId: item.fileId,
            fileName: item.fileName,
            // Mark this as a forced fresh download from queue
            forceFreshDownload: true
          }).then(() => {
            logger.info(`成功重启下载任务: ${item.fileName}`);
          }).catch(error => {
            logger.error(`重启下载任务失败: ${item.fileName}`, error);
            // If failed, set back to pending and remove from downloadTasks if added
            this.forwardedQueue.updateStatus(item.chatId, item.messageId, 'pending');
            this.downloadTasks.delete(taskId);
          });

          logger.info(`已发送重启下载请求: ${item.fileName}`);

        } catch (error) {
          logger.error(`重启转发下载异常: ${item.fileName}`, error);
          this.forwardedQueue.updateStatus(item.chatId, item.messageId, 'pending');
        }
      } else {
        logger.debug(`跳过项目: ${item.fileName} (已在处理中或状态正常)`);
      }
    }
    logger.info('转发队列处理完成');
  }

  /**
   * Continuous processor for pending forwarded downloads (runs until queue is empty)
   * This processes the forwarded queue continuously until there are no more pending items,
   * rather than running periodically at intervals
   */
  async continuousProcessPendingForwardedDownloads() {
    logger.info('开始连续处理转发队列...');

    // Process until there are no more pending items
    let hasPendingItems = true;
    let cycleCount = 0;

    while (hasPendingItems) {
      cycleCount++;
      logger.debug(`连续处理循环 #${cycleCount}`);

      const forwardedQueueStatus = this.getForwardedQueueStatus();
      logger.debug(`转发队列状态 (循环#${cycleCount}): 总共 ${forwardedQueueStatus.total}, pending: ${forwardedQueueStatus.pending}, downloading: ${forwardedQueueStatus.downloading}`);

      // Check if there are any pending items to process
      const pendingItems = forwardedQueueStatus.items.filter(item => item.status === 'pending');
      const downloadingItems = forwardedQueueStatus.items.filter(item => item.status === 'downloading');

      logger.debug(`循环#${cycleCount}发现: ${pendingItems.length} 个待处理项目, ${downloadingItems.length} 个下载中项目`);

      if (pendingItems.length === 0 && downloadingItems.length === 0) {
        logger.info(`连续处理完成，共执行 ${cycleCount} 个循环`);
        break;
      }

      // Process all pending items in this iteration
      await this.processPendingForwardedDownloads();

      // Brief pause to avoid excessive CPU usage
      await new Promise(resolve => setTimeout(resolve, 2000)); // 2秒间隔

      // Refresh the queue status to see if we still have items to process
      const updatedQueueStatus = this.getForwardedQueueStatus();
      const hasRemainingPending = updatedQueueStatus.items.some(item => item.status === 'pending');
      const hasRemainingDownloading = updatedQueueStatus.items.some(item => item.status === 'downloading');

      // Continue if there are still items to process (either pending or downloading)
      const hasMoreItems = hasRemainingPending || hasRemainingDownloading;

      if (hasMoreItems) {
        logger.debug(`循环#${cycleCount}后仍有 ${updatedQueueStatus.pending} 个待处理和 ${updatedQueueStatus.downloading} 个下载中项目，继续处理...`);
      } else {
        logger.info(`转发队列中没有更多待处理项目，连续处理结束，总共执行了 ${cycleCount} 个循环`);
      }

      hasPendingItems = hasMoreItems;
    }
  }

  /**
   * Check if an abandoned download should be restarted
   * (if it's been in 'downloading' status for more than 2 hours)
   */
  shouldRestartAbandonedDownload(item) {
    if (item.status === 'downloading' && item.startedAt) {
      try {
        // Parse the startedAt time - this should be in ISO format
        const startedDate = new Date(item.startedAt);
        const startedTime = startedDate.getTime();

        // Get current time
        const currentDate = new Date();
        const currentTime = currentDate.getTime();

        // Calculate difference in milliseconds
        const diffInMillis = currentTime - startedTime;
        const diffInMinutes = diffInMillis / (1000 * 60);
        const diffInHours = diffInMillis / (1000 * 60 * 60);

        logger.info(`检查重启 ${item.fileName}, 开始于: ${item.startedAt}, 当前时间: ${currentDate.toISOString()}, 时差: ${diffInMinutes.toFixed(2)} 分钟 (${diffInHours.toFixed(2)} 小时)`);

        // Increase the threshold to 2 hours instead of 30 minutes to avoid frequent restarts
        const THRESHOLD_HOURS = 2.0; // 2 hours instead of 0.5 hours
        const shouldRestart = diffInHours > THRESHOLD_HOURS;

        logger.info(`应重启: ${shouldRestart}, 阈值: ${THRESHOLD_HOURS}小时, 实际: ${diffInHours.toFixed(2)} 小时`);

        return shouldRestart;
      } catch (error) {
        logger.error(`检查重启下载时出错: ${error.message}`);
        return false; // Default to false on error
      }
    }
    return false;
  }

  /**
   * 格式化字节大小
   */
  formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return (bytes / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i];
  }

  async processChat(chatConfig) {
    const chatId = chatConfig.chat_id;
    const lastReadMessageId = chatConfig.last_read_message_id || 0;

    logger.info(`开始处理聊天: ${chatId}`);

    try {
      // 从远程 API 获取聊天信息
      const chatInfo = await this.apiClient.getChat(chatId);
      const chatTitle = chatInfo.title || `Chat_${chatId}`;

      let offsetId = lastReadMessageId;
      let hasMore = true;
      let processedCount = 0;

      while (hasMore) {
        // 从远程 API 获取消息列表
        const response = await this.apiClient.getMessages(chatId, {
          limit: 100,
          offsetId: offsetId,
        });

        const messages = response.messages || response || [];
        
        if (messages.length === 0) {
          hasMore = false;
          break;
        }

        for (const message of messages) {
          if (message.id <= lastReadMessageId) {
            continue;
          }

          // 应用下载过滤器
          if (chatConfig.download_filter) {
            if (!this.evaluateFilter(message, chatConfig.download_filter)) {
              continue;
            }
          }

          // 检查媒体类型
          const mediaType = this.getMediaType(message);
          if (mediaType && this.config.media_types.includes(mediaType)) {
            // 检查文件格式
            if (this.shouldDownloadFile(message, mediaType)) {
              await this.downloadManager.addDownloadTask({
                message,
                chatId,
                chatTitle,
                mediaType,
              });
              processedCount++;
            }
          }

          offsetId = message.id;
        }

        // 更新最后读取的消息 ID
        chatConfig.last_read_message_id = offsetId;
        ConfigManager.updateLastReadMessageId(chatId, offsetId);

        if (messages.length < 100) {
          hasMore = false;
        }
      }

      logger.info(
        `聊天 ${chatTitle} 处理完成，新增 ${processedCount} 个下载任务`
      );
    } catch (error) {
      logger.error(`处理聊天 ${chatId} 失败:`, error);
    }
  }

  getMediaType(message) {
    // 从远程 API 返回的消息对象中提取媒体类型
    if (message.media) {
      if (message.media_type) {
        return message.media_type; // 远程 API 可能直接提供 media_type
      }
      
      // 根据消息结构判断
      if (message.media.photo) {
        return 'photo';
      } else if (message.media.document) {
        const doc = message.media.document;
        if (doc.mime_type) {
          if (doc.mime_type.startsWith('video/')) {
            return 'video';
          } else if (doc.mime_type.startsWith('audio/')) {
            if (doc.attributes?.some(attr => attr.voice)) {
              return 'voice';
            }
            return 'audio';
          } else if (doc.mime_type === 'image/gif' || doc.attributes?.some(attr => attr.animated)) {
            return 'animation';
          }
          return 'document';
        }
      }
    }
    return null;
  }

  shouldDownloadFile(message, mediaType) {
    const fileFormats = this.config.file_formats[mediaType];
    if (!fileFormats || fileFormats.includes('all')) {
      return true;
    }

    if (message.media?.document) {
      const mimeType = message.media.document.mime_type || '';
      const extension = mimeType.split('/')[1] || '';

      for (const format of fileFormats) {
        if (mimeType.includes(format) || extension === format) {
          return true;
        }
      }
    }

    return false;
  }

  evaluateFilter(message, filter) {
    // 简单的过滤器实现
    // 支持: message_date >= 2022-12-01 00:00:00 and message_date <= 2023-01-17 00:00:00
    if (filter.includes('message_date')) {
      const dateMatch = filter.match(/message_date\s*([><=]+)\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})/);
      if (dateMatch) {
        const operator = dateMatch[1].trim();
        const filterDate = new Date(dateMatch[2].trim());
        const messageDate = new Date(message.date * 1000);

        if (operator.includes('>=')) {
          return messageDate >= filterDate;
        } else if (operator.includes('<=')) {
          return messageDate <= filterDate;
        } else if (operator.includes('>')) {
          return messageDate > filterDate;
        } else if (operator.includes('<')) {
          return messageDate < filterDate;
        }
      }
    }
    return true;
  }

  /**
   * 获取转发待下载队列状态
   */
  getForwardedQueueStatus() {
    if (this.forwardedQueue) {
      return this.forwardedQueue.getQueueStatus();
    }
    return {
      total: 0,
      pending: 0,
      downloading: 0,
      items: []
    };
  }

  /**
   * 恢复未完成的下载任务（重构版）
   * 在程序启动时调用，使用新的未完成下载管理器
   */
  async restoreUnfinishedDownloads() {
    if (!this.unfinishedDownloadManager) {
      logger.info('未完成下载管理器未初始化，跳过恢复');
      return;
    }

    logger.info('开始恢复未完成的下载任务...');

    try {
      // Check the download manager's current queue and active downloads to avoid duplicates
      const currentQueueIds = new Set(this.downloadManager.downloadQueue.map(task => `${task.chatId}_${task.messageId}`));
      const currentActiveIds = new Set(Array.from(this.downloadManager.activeDownloads.keys()));

      // 使用新的管理器来恢复所有未完成的下载
      const restoreResult = await this.unfinishedDownloadManager.restoreAllUnfinished(async (task) => {
        try {
          // Check if the task already exists in queue or active downloads
          const taskId = `${task.chatId}_${task.messageId || Date.now()}`;
          const duplicateInQueue = this.downloadManager.downloadQueue.some(t =>
            t.chatId === task.chatId &&
            (t.messageId === task.messageId ||
             (t.fileId && task.fileId && t.fileId === task.fileId))
          );
          const duplicateInActive = this.downloadManager.activeDownloads.has(taskId) ||
            Array.from(this.downloadManager.activeDownloads.values()).some(t =>
              t.chatId === task.chatId &&
              (t.messageId === task.messageId ||
               (t.fileId && task.fileId && t.fileId === task.fileId))
            );

          if (duplicateInQueue || duplicateInActive) {
            logger.warn(`恢复任务已存在，跳过重复添加: ${task.fileName || '文件'} (chatId: ${task.chatId}, messageId: ${task.messageId})`);
            return { success: true, skipped: true }; // Return success but indicate it was skipped
          }

          // 根据任务类型创建相应的下载任务
          let downloadTask;

          switch (task.type) {
            case 'history_incomplete':
            case 'file_incomplete':
              // Create task to resume from specific byte offset
              downloadTask = {
                message: {
                  message_id: task.messageId,
                  date: Math.floor(Date.now() / 1000),
                  [task.mediaType]: {
                    file_id: task.fileId,
                    file_name: task.fileName
                  }
                },
                chatId: task.chatId,
                chatTitle: `恢复任务_${task.chatId}`,
                mediaType: task.mediaType,
                fileId: task.fileId,
                fileName: task.fileName || '恢复下载',
                startBytes: task.startBytes || 0, // Start from specific byte offset for resume
                originalTask: task.originalTask
              };
              break;

            case 'forwarded_pending':
              // Create task for forwarded item
              downloadTask = {
                message: {
                  message_id: task.messageId,
                  date: Math.floor(Date.now() / 1000),
                  [task.mediaType]: {
                    file_id: task.fileId,
                    file_name: task.fileName
                  }
                },
                chatId: task.chatId,
                chatTitle: `转发恢复_${task.chatId}`,
                mediaType: task.mediaType,
                fileId: task.fileId,
                fileName: task.fileName || '转发恢复',
                originalTask: task.originalTask
              };
              break;

            case 'orphaned_missing':
            case 'orphaned_incomplete':
              // Create task for orphaned file
              downloadTask = {
                message: {
                  message_id: task.messageId,
                  date: Math.floor(Date.now() / 1000),
                  [task.mediaType]: {
                    file_id: task.fileId,
                    file_name: task.fileName
                  }
                },
                chatId: task.chatId,
                chatTitle: `孤儿恢复_${task.chatId}`,
                mediaType: task.mediaType,
                fileId: task.fileId,
                fileName: task.fileName || '孤儿恢复',
                startBytes: task.startBytes || 0,
                originalTask: task.originalTask
              };
              break;

            default:
              logger.warn(`未知的任务类型: ${task.type}`, task);
              return { success: false, error: `Unknown task type: ${task.type}` };
          }

          // Add to download manager with proper start byte offset support
          await this.downloadManager.addDownloadTask(downloadTask);

          logger.info(`已添加恢复任务到下载队列: ${task.fileName} (类型: ${task.type})`);
          return { success: true };

        } catch (error) {
          logger.error(`添加恢复任务失败: ${task.fileName}`, error);
          return { success: false, error: error.message };
        }
      });

      logger.info(`完成未完成下载任务恢复: 成功 ${restoreResult} 个`);
    } catch (error) {
      logger.error('恢复未完成下载任务时出错:', error);
    }
  }
}

// 启动应用
const app = new TelegramMediaDownloader();
app.init().catch((error) => {
  logger.error('应用启动失败:', error);
  process.exit(1);
});

// 优雅关闭
process.on('SIGINT', async () => {
  logger.info('正在关闭应用...');
  if (app.client) {
    await app.client.disconnect();
  }

  // Stop forwarded queue processor
  if (app.stopForwardedQueueProcessor) {
    app.stopForwardedQueueProcessor();
  }

  process.exit(0);
});
