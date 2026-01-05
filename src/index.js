import { fileURLToPath } from 'url';
import { dirname } from 'path';
import chalk from 'chalk';
import { createLogger, format, transports } from 'winston';
import { DownloadManager } from './downloadManager.js';
import { BotHandler } from './botHandler.js';
import { ConfigManager } from './configManager.js';
import { TelegramApiClient } from './telegramApiClient.js';
import { DownloadHistory } from './downloadHistory.js';
import { MessageRateLimiter } from './messageRateLimiter.js';

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
    // 存储每个下载任务的消息信息，用于完成后发送消息
    this.downloadTasks = new Map(); // taskId -> { chatId, messageId, fileName, isPrivateChat }
  }

  async init() {
    try {
      // 加载配置
      this.config = ConfigManager.loadConfig();
      logger.info('配置加载成功');

      // 初始化远程 Telegram Bot API 客户端
      this.apiClient = new TelegramApiClient(this.config, logger);
      await this.apiClient.checkConnection();
      logger.info(chalk.green('✓ 远程 Telegram Bot API 连接成功'));

      // 初始化下载历史记录
      this.downloadHistory = new DownloadHistory(this.config, logger);
      logger.info(chalk.green('✓ 下载历史记录已初始化'));

      // 初始化消息发送限流器（更保守的配置）
      this.messageRateLimiter = new MessageRateLimiter(logger, {
        maxPerSecond: 5,        // 全局每秒最多 5 条
        maxPerMinute: 15,        // 全局每分钟最多 15 条
        maxPerChatPerSecond: 1   // 同一聊天每秒最多 1 条
      });
      logger.info(chalk.green('✓ 消息限流器已初始化（保守模式）'));

      // 设置消息监听（用于接收新消息）
      this.setupMessageListener();

      // 初始化下载管理器（传入下载历史记录）
      this.downloadManager = new DownloadManager(this.config, logger, this.apiClient, this.downloadHistory);
      await this.downloadManager.init();

      // 设置下载完成监听
      this.setupDownloadCompleteListener();

      // 初始化 Bot（如果配置了 bot_token）
      if (this.config.bot_token && this.config.bot_token !== 'your_bot_token') {
        this.botHandler = new BotHandler(
          this.config,
          this.downloadManager,
          logger,
          this.apiClient,
          this.messageRateLimiter
        );
        await this.botHandler.init();
      }

      // 注意：Bot API 不支持直接获取历史消息
      // 如果需要处理历史消息，需要通过远程服务器提供的其他接口
      // 或者通过消息监听来处理新消息
      logger.info('Bot API 模式：将通过消息监听处理新消息');

      logger.info(chalk.green('✓ Telegram Media Downloader 启动成功'));
    } catch (error) {
      logger.error('初始化失败:', error);
      process.exit(1);
    }
  }

  /**
   * 设置下载完成监听器
   */
  setupDownloadCompleteListener() {
    this.downloadManager.on('complete', async (data) => {
      const { taskId, filePath, status, chatId, fileName } = data;
      const taskInfo = this.downloadTasks.get(taskId);
      
      // 使用 taskInfo 或 data 中的信息
      const finalChatId = taskInfo?.chatId || chatId;
      const finalFileName = taskInfo?.fileName || fileName || '文件';
      const isPrivateChat = taskInfo?.isPrivateChat || false;
      
      // 如果没有 chatId，无法发送消息
      if (!finalChatId) {
        logger.warn(`无法发送完成消息：缺少 chatId (taskId: ${taskId})`);
        if (taskInfo) {
          this.downloadTasks.delete(taskId);
        }
        return;
      }
      
      const bot = this.apiClient.getBot();
      
      // 只在私聊时发送完成消息（使用限流器）
      if (isPrivateChat) {
        try {
          if (status === 'completed') {
            await this.messageRateLimiter.sendMessage(
              bot,
              finalChatId,
              `✅ 下载完成：${finalFileName}\n📁 保存路径：${filePath || '未知'}`
            );
          } else if (status === 'skipped') {
            await this.messageRateLimiter.sendMessage(
              bot,
              finalChatId,
              `⏭️ 文件已存在，已跳过：${finalFileName}`
            );
          } else if (status === 'failed') {
            await this.messageRateLimiter.sendMessage(
              bot,
              finalChatId,
              `❌ 下载失败：${finalFileName}`
            );
          }
        } catch (error) {
          logger.error('发送完成消息失败:', error);
        }
      }
      
      // 清理任务信息
      if (taskInfo) {
        this.downloadTasks.delete(taskId);
      }
    });
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
        const chatId = msg.chat.id.toString();
        const messageId = msg.message_id;
        const chatType = msg.chat.type; // 'private', 'group', 'supergroup', 'channel'
        
        // 检查是否是转发的消息
        const isForwarded = !!(msg.forward_from || msg.forward_from_chat || msg.forward_from_message_id);
        
        // 检查是否是私聊消息（用户直接发给 Bot）
        const isPrivateChat = chatType === 'private';
        
        // 检查是否启用私聊处理
        const enablePrivateChat = this.config.enable_private_chat !== false; // 默认 true
        
        // 检查是否在配置的聊天列表中
        const chatConfig = this.config.chat?.find(c => {
          const configChatId = c.chat_id.toString();
          return configChatId === chatId || configChatId === `-${chatId}`;
        });
        
        // 如果不在配置列表中，且（不是私聊 或 私聊未启用），则忽略
        // 但如果是转发的消息，即使不在配置列表中，如果是私聊也处理
        if (!chatConfig && (!isPrivateChat || !enablePrivateChat)) {
          return;
        }

        // 检查媒体类型（转发的消息和普通消息一样处理）
        const mediaType = this.getMediaTypeFromMessage(msg);
        
        // 调试日志：记录收到的消息信息（特别是转发消息）
        if (isForwarded) {
          logger.info(`收到转发消息 - 类型: ${chatType}, 媒体类型: ${mediaType || '无'}, 消息ID: ${messageId}`);
          logger.info(`转发消息字段: photo=${!!msg.photo}, video=${!!msg.video}, document=${!!msg.document}, audio=${!!msg.audio}, voice=${!!msg.voice}, animation=${!!msg.animation}`);
          logger.info(`转发来源: forward_from=${!!msg.forward_from}, forward_from_chat=${!!msg.forward_from_chat}, forward_from_message_id=${msg.forward_from_message_id || '无'}`);
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
          logger.warn(`视频信息: file_name=${msg.video?.file_name || '无'}, mime_type=${msg.video?.mime_type || '无'}, document=${!!msg.document}`);
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
    switch (mediaType) {
      case 'video':
        return msg.video?.file_name || msg.document?.file_name || '视频';
      case 'audio':
        return msg.audio?.file_name || msg.document?.file_name || '音频';
      case 'document':
        return msg.document?.file_name || '文档';
      case 'photo':
        return '图片';
      case 'voice':
        return '语音';
      case 'animation':
        return msg.animation?.file_name || msg.document?.file_name || '动画';
      default:
        return '文件';
    }
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
  process.exit(0);
});
