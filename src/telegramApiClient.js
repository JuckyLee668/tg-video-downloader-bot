import TelegramBot from 'node-telegram-bot-api';
import https from 'https';
import { createWriteStream } from 'fs';
import { existsSync } from 'fs';
import { PassThrough } from 'stream';
import { ApiRequestQueue } from './apiRequestQueue.js';

export class TelegramApiClient {
  constructor(config, logger) {
    this.config = config;
    this.logger = logger;
    this.botToken = config.bot_token || '';
    this.botApiHost = config.remote_api?.bot_api_host || 'http://127.0.0.1:8081';
    this.publicFileBaseUrl = config.remote_api?.public_file_base_url || '';
    this.tgBaseDir = config.remote_api?.tg_base_dir || '/media/TGbot';
    
    if (!this.botToken || this.botToken === 'your_bot_token') {
      throw new Error('Bot Token 未配置');
    }

    if (!this.publicFileBaseUrl) {
      throw new Error('公网文件访问 URL 未配置');
    }

    // 创建 Telegram Bot 实例，指向远程 Bot API 服务器
    // 启用 polling 模式以接收消息（包括用户直接发给 Bot 的消息）
    this.bot = new TelegramBot(this.botToken, {
      polling: true, // 启用轮询模式以接收消息
      baseApiUrl: this.botApiHost
    });

    // 初始化 API 请求队列（限制同时获取文件信息的请求数）
    // 默认最多同时 2 个请求，可以通过配置调整（降低并发数避免服务器压力过大）
    const maxApiConcurrent = config.remote_api?.max_api_concurrent || 2;
    this.apiRequestQueue = new ApiRequestQueue(logger, maxApiConcurrent);
    this.logger.info(`Telegram Bot API 客户端初始化: ${this.botApiHost} (polling 已启用, API 并发限制: ${maxApiConcurrent})`);
  }

  /**
   * 获取聊天信息
   */
  async getChat(chatId) {
    try {
      const chat = await this.bot.getChat(chatId);
      return {
        id: chat.id.toString(),
        title: chat.title || chat.first_name || `Chat_${chatId}`,
        type: chat.type
      };
    } catch (error) {
      this.logger.error(`获取聊天信息失败 (${chatId}):`, error.message);
      throw error;
    }
  }

  /**
   * 获取消息列表
   * 注意：Telegram Bot API 不直接支持获取历史消息
   * 这里需要通过其他方式实现，或者需要远程服务器提供此功能
   */
  async getMessages(chatId, options = {}) {
    try {
      // Bot API 不直接支持获取历史消息
      // 如果远程服务器提供了自定义接口，可以调用
      // 否则返回空数组或抛出错误
      this.logger.warn('Bot API 不支持直接获取历史消息，需要远程服务器提供自定义接口');
      
      // 这里可以尝试调用远程服务器的自定义接口
      // 或者返回空数组，等待通过其他方式（如 webhook）接收消息
      return {
        messages: []
      };
    } catch (error) {
      this.logger.error(`获取消息列表失败 (${chatId}):`, error.message);
      throw error;
    }
  }

  /**
   * 获取单个消息（通过消息 ID）
   * Bot API 不直接支持，需要通过其他方式
   */
  async getMessage(chatId, messageId) {
    try {
      // Bot API 不直接支持通过消息 ID 获取消息
      // 需要通过其他方式实现
      throw new Error('Bot API 不支持通过消息 ID 获取消息');
    } catch (error) {
      this.logger.error(`获取消息失败 (${chatId}/${messageId}):`, error.message);
      throw error;
    }
  }

  /**
   * 下载媒体文件
   * 使用公网文件 URL 下载
   */
  async downloadMedia(chatId, messageId, savePath, progressCallback) {
    try {
      // 首先获取文件信息
      const fileInfo = await this.getFileInfo(chatId, messageId);
      
      if (!fileInfo || !fileInfo.file_path) {
        throw new Error('无法获取文件路径');
      }

      // 构造公网可访问的 URL
      const publicUrl = this.buildPublicFileUrl(fileInfo.file_path);
      
      this.logger.info(`开始下载文件: ${publicUrl}`);

      // 使用 https 下载文件
      return await this.downloadFileFromUrl(publicUrl, savePath, progressCallback);
    } catch (error) {
      this.logger.error(`下载媒体失败 (${chatId}/${messageId}):`, error.message);
      throw error;
    }
  }

  /**
   * 获取文件信息
   */
  async getFileInfo(chatId, messageId) {
    try {
      // 通过 Bot API 获取消息
      // 注意：这需要消息在当前会话中可用
      // 如果消息不在当前会话，需要通过其他方式获取 file_id
      throw new Error('需要通过 file_id 获取文件信息，请使用 getFileByFileId 方法');
    } catch (error) {
      this.logger.error(`获取文件信息失败:`, error.message);
      throw error;
    }
  }

  /**
   * 通过 file_id 获取文件信息（带重试和超时，使用请求队列）
   * 针对自建 Bot Server：如果文件路径为空，说明文件还在下载中，需要轮询等待
   */
  async getFileByFileId(fileId, retries = 3) {
    // 单次请求超时时间（降低到30秒，允许快速失败后继续轮询）
    const SINGLE_REQUEST_TIMEOUT = this.config.remote_api?.single_request_timeout || 30000; // 30秒
    // 轮询间隔（自建服务器需要等待文件下载完成）
    const POLL_INTERVAL = this.config.remote_api?.file_poll_interval || 3000; // 3秒
    // 最大轮询时间（避免无限等待）
    const MAX_POLL_TIME = this.config.remote_api?.max_poll_time || 300000; // 5分钟（用户已改为30分钟）
    
    // 单次获取文件信息（带超时，超时时间较短）
    const getFileOnce = (fileId) => {
      let timeoutId = null;
      
      const timeoutPromise = new Promise((_, reject) => {
        timeoutId = setTimeout(() => {
          reject(new Error(`单次请求超时（${SINGLE_REQUEST_TIMEOUT / 1000}秒）`));
        }, SINGLE_REQUEST_TIMEOUT);
      });
      
      const filePromise = this.bot.getFile(fileId).then(
        (result) => {
          if (timeoutId) {
            clearTimeout(timeoutId);
            timeoutId = null;
          }
          return result;
        },
        (error) => {
          if (timeoutId) {
            clearTimeout(timeoutId);
            timeoutId = null;
          }
          throw error;
        }
      );
      
      return Promise.race([filePromise, timeoutPromise]);
    };

    // 轮询获取文件信息（等待文件下载完成）
    const pollFileInfo = async (fileId, startTime) => {
      let pollAttempt = 0;
      let lastError = null;
      
      while (Date.now() - startTime < MAX_POLL_TIME) {
        pollAttempt++;
        const elapsed = Date.now() - startTime;
        
        try {
          // 使用请求队列，限制并发数
          const file = await this.apiRequestQueue.addRequest(
            () => getFileOnce(fileId),
            `getFile_${fileId.substring(0, 20)}_poll_${pollAttempt}`
          );
          
          // 检查文件路径是否可用（自建服务器可能返回空路径，表示文件还在下载中）
          if (file && file.file_path && file.file_path.trim() !== '') {
            if (pollAttempt > 1) {
              this.logger.info(`文件信息可用（轮询 ${pollAttempt} 次，总耗时 ${elapsed}ms）: ${fileId.substring(0, 30)}...`);
            }
            return file;
          } else {
            // 文件路径为空，说明文件还在下载中，继续轮询
            this.logger.debug(`文件路径为空，等待文件下载完成（轮询 ${pollAttempt} 次，已等待 ${Math.round(elapsed / 1000)}秒）: ${fileId.substring(0, 30)}...`);
            await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL));
            lastError = null; // 重置错误，因为这是正常的等待
          }
        } catch (error) {
          lastError = error;
          
          // 判断是否应该继续轮询（包括单次请求超时）
          const isRetryableError = error.code === 'ECONNRESET' || 
                                  error.code === 'ETIMEDOUT' || 
                                  error.code === 'ENOTFOUND' ||
                                  error.message?.includes('ECONNRESET') ||
                                  error.message?.includes('ETIMEDOUT') ||
                                  error.message?.includes('单次请求超时') ||
                                  error.message?.includes('超时');
          
          if (isRetryableError && Date.now() - startTime < MAX_POLL_TIME) {
            const elapsed = Date.now() - startTime;
            this.logger.info(`获取文件信息失败，继续轮询（轮询 ${pollAttempt} 次，已等待 ${Math.round(elapsed / 1000)}秒）: ${error.message.substring(0, 50)}...`);
            await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL));
          } else {
            // 非可重试错误或超过最大轮询时间，抛出错误
            throw error;
          }
        }
      }
      
      // 如果最后有错误，抛出最后的错误；否则抛出轮询超时
      if (lastError) {
        throw new Error(`轮询失败：${lastError.message}（总耗时 ${MAX_POLL_TIME / 1000}秒）`);
      } else {
        throw new Error(`轮询超时：${MAX_POLL_TIME / 1000}秒内未获取到文件路径`);
      }
    };

    let lastError;
    for (let attempt = 1; attempt <= retries; attempt++) {
      try {
        const startTime = Date.now();
        
        // 轮询获取文件信息
        const file = await pollFileInfo(fileId, startTime);
        
        const duration = Date.now() - startTime;
        
        if (attempt > 1) {
          this.logger.info(`获取文件信息成功（重试 ${attempt - 1} 次后，总耗时 ${duration}ms）: ${fileId.substring(0, 30)}...`);
        } else if (duration > 10000) {
          this.logger.warn(`获取文件信息耗时较长: ${duration}ms (file_id: ${fileId.substring(0, 30)}...)`);
        }
        return file;
      } catch (error) {
        lastError = error;
        const isNetworkError = error.code === 'ECONNRESET' || 
                              error.code === 'ETIMEDOUT' || 
                              error.code === 'ENOTFOUND' ||
                              error.message?.includes('ECONNRESET') ||
                              error.message?.includes('ETIMEDOUT') ||
                              error.message?.includes('超时') ||
                              error.message?.includes('轮询超时');
        
        if (isNetworkError && attempt < retries) {
          const waitTime = Math.min(attempt * 2000, 10000); // 递增等待时间：2s, 4s, 6s，最多10秒
          this.logger.warn(`获取文件信息失败（尝试 ${attempt}/${retries}），${waitTime}ms 后重试: ${fileId.substring(0, 30)}... - ${error.message}`);
          await new Promise(resolve => setTimeout(resolve, waitTime));
        } else {
          this.logger.error(`获取文件信息失败 (file_id: ${fileId.substring(0, 30)}...):`, error.message);
          throw error;
        }
      }
    }
    throw lastError;
  }

  /**
   * 通过 file_id 下载文件（带重试）
   */
  async downloadMediaByFileId(fileId, savePath, progressCallback, retries = 3) {
    let lastError;
    for (let attempt = 1; attempt <= retries; attempt++) {
      try {
        // 获取文件信息（带重试）
        const fileInfo = await this.getFileByFileId(fileId, retries);
        
        if (!fileInfo || !fileInfo.file_path) {
          throw new Error('无法获取文件路径');
        }

        // 构造公网可访问的 URL
        const publicUrl = this.buildPublicFileUrl(fileInfo.file_path);
        
        if (attempt === 1) {
          this.logger.info(`开始下载文件: ${publicUrl}`);
        } else {
          this.logger.info(`重试下载文件（尝试 ${attempt}/${retries}）: ${publicUrl}`);
        }

        // 使用 https 下载文件（带重试）
        return await this.downloadFileFromUrl(publicUrl, savePath, progressCallback, retries);
      } catch (error) {
        lastError = error;
        const isNetworkError = error.code === 'ECONNRESET' || 
                              error.code === 'ETIMEDOUT' || 
                              error.code === 'ENOTFOUND' ||
                              error.message?.includes('ECONNRESET') ||
                              error.message?.includes('ETIMEDOUT') ||
                              error.message?.includes('read ECONNRESET');
        
        if (isNetworkError && attempt < retries) {
          const waitTime = attempt * 2000; // 递增等待时间：2s, 4s, 6s
          this.logger.warn(`下载媒体失败（尝试 ${attempt}/${retries}），${waitTime}ms 后重试: ${fileId} - ${error.message}`);
          await new Promise(resolve => setTimeout(resolve, waitTime));
        } else {
          this.logger.error(`下载媒体失败 (file_id: ${fileId}):`, error.message);
          throw error;
        }
      }
    }
    throw lastError;
  }

  /**
   * 构造公网文件 URL
   */
  buildPublicFileUrl(filePath) {
    // filePath 格式: /media/TGbot/<token>/videos/file_xxx
    // 需要移除 tg_base_dir 部分
    let relativePath = filePath;
    if (relativePath.startsWith(this.tgBaseDir)) {
      relativePath = relativePath.substring(this.tgBaseDir.length);
      // 移除开头的斜杠
      if (relativePath.startsWith('/')) {
        relativePath = relativePath.substring(1);
      }
    } else if (relativePath.startsWith('/')) {
      // 如果路径以 / 开头但不是 tg_base_dir，尝试直接使用
      relativePath = relativePath.substring(1);
    }

    // 构造完整 URL，确保没有双斜杠
    const baseUrl = this.publicFileBaseUrl.replace(/\/$/, '');
    return `${baseUrl}/${relativePath}`;
  }

  /**
   * 从 URL 下载文件（带重试和超时，优化性能）
   */
  downloadFileFromUrl(url, savePath, progressCallback, retries = 3) {
    return new Promise((resolve, reject) => {
      // 使用更大的缓冲区提高写入性能
      const file = createWriteStream(savePath, { 
        highWaterMark: 16 * 1024 * 1024 // 16MB 缓冲区
      });
      
      let downloadedBytes = 0;
      let totalBytes = 0;
      let lastProgressUpdate = 0;
      const PROGRESS_UPDATE_INTERVAL = 500; // 每500ms更新一次进度，减少开销
      const DOWNLOAD_TIMEOUT = 300000; // 5分钟超时
      
      let timeoutId;
      let hasResolved = false;
      let req;

      const cleanup = () => {
        if (timeoutId) {
          clearTimeout(timeoutId);
          timeoutId = null;
        }
      };

      // 设置超时
      timeoutId = setTimeout(() => {
        if (!hasResolved) {
          hasResolved = true;
          cleanup();
          file.destroy();
          if (req) {
            req.destroy();
          }
          reject(new Error('下载超时'));
        }
      }, DOWNLOAD_TIMEOUT);

      req = https.get(url, {
        timeout: DOWNLOAD_TIMEOUT,
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
          'Connection': 'keep-alive',
          'Accept-Encoding': 'gzip, deflate, br' // 支持压缩
        }
      }, (res) => {
        cleanup(); // 连接已建立，清除连接超时
        
        if (res.statusCode !== 200) {
          file.destroy();
          if (!hasResolved) {
            hasResolved = true;
            reject(new Error(`HTTP ${res.statusCode}: ${res.statusMessage}`));
          }
          return;
        }

        totalBytes = parseInt(res.headers['content-length'] || '0', 10);

        // 使用 PassThrough 流来高效处理数据，同时跟踪进度
        const progressStream = new PassThrough({
          highWaterMark: 16 * 1024 * 1024 // 16MB 缓冲区
        });

        // 监听数据流，更新进度
        progressStream.on('data', (chunk) => {
          downloadedBytes += chunk.length;
          
          // 减少进度更新频率，提高性能
          const now = Date.now();
          if (progressCallback && (now - lastProgressUpdate >= PROGRESS_UPDATE_INTERVAL || downloadedBytes === totalBytes)) {
            if (totalBytes > 0) {
              const progress = downloadedBytes / totalBytes;
              progressCallback(progress, downloadedBytes, totalBytes);
            } else {
              // 如果没有总大小信息，传递已下载字节数
              const estimatedProgress = downloadedBytes > 0 ? 0.01 : 0;
              progressCallback(estimatedProgress, downloadedBytes, 0);
            }
            lastProgressUpdate = now;
          }
        });

        // 使用 pipe 高效传输数据
        res.pipe(progressStream).pipe(file);

        res.on('error', (err) => {
          cleanup();
          progressStream.destroy();
          file.destroy();
          if (!hasResolved) {
            hasResolved = true;
            reject(err);
          }
        });

        progressStream.on('error', (err) => {
          cleanup();
          file.destroy();
          if (!hasResolved) {
            hasResolved = true;
            reject(err);
          }
        });
      });

      req.on('error', (err) => {
        cleanup();
        file.destroy();
        if (!hasResolved) {
          hasResolved = true;
          reject(err);
        }
      });

      req.on('timeout', () => {
        cleanup();
        if (req) {
          req.destroy();
        }
        file.destroy();
        if (!hasResolved) {
          hasResolved = true;
          reject(new Error('请求超时'));
        }
      });

      file.on('finish', () => {
        cleanup();
        // 最后一次回调，确保进度为 100%
        if (progressCallback) {
          if (totalBytes > 0) {
            progressCallback(1, totalBytes, totalBytes);
          } else {
            progressCallback(1, downloadedBytes, downloadedBytes);
          }
        }
        if (!hasResolved) {
          hasResolved = true;
          resolve(savePath);
        }
      });

      file.on('error', (error) => {
        cleanup();
        file.destroy();
        if (!hasResolved) {
          hasResolved = true;
          reject(error);
        }
      });
    });
  }

  /**
   * 获取媒体文件信息（不下载）
   */
  async getMediaInfo(chatId, messageId) {
    try {
      const fileInfo = await this.getFileInfo(chatId, messageId);
      return fileInfo;
    } catch (error) {
      this.logger.error(`获取媒体信息失败 (${chatId}/${messageId}):`, error.message);
      throw error;
    }
  }

  /**
   * 检查连接状态
   */
  async checkConnection() {
    try {
      const me = await this.bot.getMe();
      this.logger.info(`Bot 连接成功: @${me.username} (${me.first_name})`);
      return {
        ok: true,
        bot: {
          id: me.id,
          username: me.username,
          first_name: me.first_name
        }
      };
    } catch (error) {
      this.logger.error('检查 Bot API 连接失败:', error.message);
      throw error;
    }
  }

  /**
   * 获取聊天列表
   * Bot API 不直接支持，需要通过其他方式
   */
  async getChats() {
    try {
      // Bot API 不直接支持获取聊天列表
      // 需要通过其他方式实现
      this.logger.warn('Bot API 不支持直接获取聊天列表');
      return [];
    } catch (error) {
      this.logger.error('获取聊天列表失败:', error.message);
      throw error;
    }
  }

  /**
   * 获取 Bot 实例（用于其他操作）
   */
  getBot() {
    return this.bot;
  }
}
