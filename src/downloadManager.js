import { EventEmitter } from 'events';
import { existsSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// 格式化字节大小（辅助函数）
function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0.0B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return (bytes / Math.pow(k, i)).toFixed(2) + sizes[i];
}

export class DownloadManager extends EventEmitter {
  constructor(config, logger, apiClient, downloadHistory = null) {
    super();
    this.config = config;
    this.logger = logger;
    this.apiClient = apiClient;
    this.downloadHistory = downloadHistory;
    this.downloadQueue = [];
    this.activeDownloads = new Map();
    this.downloadStats = {
      total: 0,
      completed: 0,
      failed: 0,
      active: 0,
      skipped: 0,
    };
    this.maxConcurrent = config.max_download_task || 5;
  }

  async init() {
    // 确保下载目录存在
    const savePath = this.config.save_path || './downloads';
    if (!existsSync(savePath)) {
      mkdirSync(savePath, { recursive: true });
    }
  }

  async addDownloadTask(task) {
    this.downloadQueue.push(task);
    this.downloadStats.total++;
    
    const queueStatus = {
      队列中: this.downloadQueue.length,
      正在下载: this.activeDownloads.size,
      最大并发: this.maxConcurrent
    };
    this.logger.info(`任务已加入下载队列: ${task.fileName || '文件'} (${JSON.stringify(queueStatus)})`);
    
    this.emit('status', this.getStatus());
    this.processQueue();
  }

  async processQueue() {
    while (
      this.downloadQueue.length > 0 &&
      this.activeDownloads.size < this.maxConcurrent
    ) {
      const task = this.downloadQueue.shift();
      const queueStatus = {
        队列剩余: this.downloadQueue.length,
        正在下载: this.activeDownloads.size + 1,
        最大并发: this.maxConcurrent
      };
      this.logger.debug(`开始处理下载任务: ${task.fileName || '文件'} (${JSON.stringify(queueStatus)})`);
      
      this.downloadFile(task).catch((error) => {
        this.logger.error(`下载失败: ${error.message}`, error);
        this.downloadStats.failed++;
        this.emit('status', this.getStatus());
        // 下载失败后继续处理队列
        this.processQueue();
      });
    }
  }

  async downloadFile(task) {
    const { message, chatId, chatTitle, mediaType, fileId } = task;
    const messageId = message?.message_id || message?.id || 'unknown';
    const taskId = `${chatId}_${messageId}`;

    // 获取文件名
    const fileName = task.fileName || this.getFileName(task) || '文件';

    this.activeDownloads.set(taskId, {
      chatId,
      chatTitle,
      messageId,
      mediaType,
      fileId, // 保存 fileId 用于历史记录
      message, // 保存 message 用于历史记录
      fileName, // 保存 fileName 用于完成消息
      progress: 0,
      status: 'downloading',
      startTime: Date.now(),
      fileSize: 0,
      downloadedBytes: 0,
      speed: 0,
      filePath: null,
    });

    this.downloadStats.active = this.activeDownloads.size;
    this.emit('status', this.getStatus());

    try {
      const filePath = await this.getFilePath(task);
      const dir = dirname(filePath);

      if (!existsSync(dir)) {
        mkdirSync(dir, { recursive: true });
      }

      // 检查下载历史记录
      if (this.downloadHistory && this.downloadHistory.isDownloaded(fileId, chatId, messageId, filePath)) {
        const record = this.downloadHistory.getRecord(fileId, chatId, messageId);
        this.logger.info(`文件已在历史记录中，跳过: ${record?.filePath || filePath}`);
        this.completeDownload(taskId, record?.filePath || filePath, true);
        return;
      }

      // 如果文件已存在，跳过下载
      if (existsSync(filePath)) {
        this.logger.info(`文件已存在，跳过: ${filePath}`);
        // 记录到历史
        if (this.downloadHistory) {
          const fileName = this.getFileName(task);
          this.downloadHistory.recordDownload(fileId, chatId, messageId, filePath, fileName);
        }
        this.completeDownload(taskId, filePath, true);
        return;
      }

      // 通过远程 API 下载文件
      let lastUpdateTime = Date.now();
      let lastDownloadedBytes = 0;
      let estimatedTotalBytes = 0;
      
      const progressCallback = (progress, downloadedBytes, totalBytes) => {
        const downloadInfo = this.activeDownloads.get(taskId);
        if (!downloadInfo) return;
        
        const now = Date.now();
        const timeDelta = (now - lastUpdateTime) / 1000; // 秒
        
        // 更新已下载字节数
        downloadInfo.downloadedBytes = downloadedBytes || 0;
        
        // 计算下载速度（基于实际下载的字节数变化）
        if (timeDelta > 0 && downloadedBytes > lastDownloadedBytes) {
          const bytesDelta = downloadedBytes - lastDownloadedBytes;
          downloadInfo.speed = bytesDelta / timeDelta; // 字节/秒
        }
        
        // 更新文件大小和进度
        if (totalBytes > 0) {
          // 有总大小信息，直接计算进度
          downloadInfo.fileSize = totalBytes;
          estimatedTotalBytes = totalBytes;
          downloadInfo.progress = Math.round(progress * 100);
        } else if (downloadedBytes > 0) {
          // 没有总大小信息，但已开始下载
          // 基于已下载字节数和下载速度估算总大小
          if (estimatedTotalBytes === 0) {
            // 初始估算：假设文件至少是已下载字节数的1.2倍
            estimatedTotalBytes = Math.round(downloadedBytes * 1.2);
          } else if (downloadInfo.speed > 0) {
            // 基于下载速度动态调整估算
            const elapsedTime = (now - downloadInfo.startTime) / 1000;
            if (elapsedTime > 2) { // 至少下载2秒后才进行估算
              // 估算：已下载 + 当前速度 * 预计剩余时间（假设还需要相同时间）
              const newEstimate = Math.round(downloadedBytes + (downloadInfo.speed * elapsedTime));
              if (newEstimate > estimatedTotalBytes) {
                estimatedTotalBytes = newEstimate;
              }
            }
          }
          
          // 确保估算值不小于已下载字节数
          if (estimatedTotalBytes < downloadedBytes) {
            estimatedTotalBytes = Math.round(downloadedBytes * 1.1);
          }
          
          downloadInfo.fileSize = estimatedTotalBytes;
          // 计算进度，但不超过95%（因为总大小是估算的）
          downloadInfo.progress = Math.min(95, Math.round((downloadedBytes / estimatedTotalBytes) * 100));
        } else {
          // 还没有开始下载
          downloadInfo.progress = 0;
        }
        
        lastUpdateTime = now;
        lastDownloadedBytes = downloadedBytes || 0;
        
        // 触发进度事件（不输出到终端，只通过WebSocket发送）
        this.emit('progress', {
          taskId,
          ...downloadInfo,
        });
      };

      // 优先使用 file_id 下载（Bot API 模式，带重试）
      if (fileId) {
        await this.apiClient.downloadMediaByFileId(
          fileId,
          filePath,
          progressCallback,
          3 // 重试3次
        );
      } else if (message && message.id) {
        // 回退到使用消息 ID（如果远程 API 支持）
        await this.apiClient.downloadMedia(
          chatId,
          message.id,
          filePath,
          progressCallback
        );
      } else {
        throw new Error('无法获取文件 ID 或消息 ID');
      }

      this.completeDownload(taskId, filePath, false);
    } catch (error) {
      const downloadInfo = this.activeDownloads.get(taskId);
      
      // 记录详细错误信息
      const errorDetails = {
        message: error.message,
        stack: error.stack,
        code: error.code,
        response: error.response?.error_code || error.response?.statusCode,
        chatId: downloadInfo?.chatId,
        messageId: downloadInfo?.messageId,
        fileId: downloadInfo?.fileId,
        mediaType: downloadInfo?.mediaType,
      };
      
      this.logger.error(`下载文件失败 (${taskId}):`, errorDetails);
      this.downloadStats.failed++;
      
      this.activeDownloads.delete(taskId);
      this.downloadStats.active = this.activeDownloads.size;
      
      // 发送失败事件
      this.emit('complete', {
        taskId,
        filePath: null,
        status: 'failed',
        chatId: downloadInfo?.chatId,
        fileName: downloadInfo?.fileName,
        error: error.message,
        errorDetails,
        ...downloadInfo,
      });
      
      this.emit('status', this.getStatus());
    } finally {
      // 继续处理队列
      this.processQueue();
    }
  }

  completeDownload(taskId, filePath, skipped) {
    const downloadInfo = this.activeDownloads.get(taskId);
    if (downloadInfo) {
      downloadInfo.status = skipped ? 'skipped' : 'completed';
      downloadInfo.progress = 100;
      downloadInfo.filePath = filePath;
      downloadInfo.duration = Date.now() - downloadInfo.startTime;
      
      // 如果是跳过，更新跳过计数
      if (skipped) {
        this.downloadStats.skipped = (this.downloadStats.skipped || 0) + 1;
      }
    }

    if (!skipped) {
      this.downloadStats.completed++;
    }
    this.activeDownloads.delete(taskId);
    this.downloadStats.active = this.activeDownloads.size;

    this.logger.info(
      `${skipped ? '跳过' : '完成'}下载: ${filePath} (${taskId})`
    );

    // 记录到下载历史（仅成功下载）
    if (this.downloadHistory && filePath && !skipped && downloadInfo) {
      try {
        const fileId = downloadInfo.fileId;
        const chatId = downloadInfo.chatId;
        const messageId = downloadInfo.messageId;
        const message = downloadInfo.message;
        
        // 获取文件名
        let fileName = downloadInfo.fileName || '文件';
        if (message && !fileName) {
          const task = { message, chatId, mediaType: downloadInfo.mediaType };
          fileName = this.getFileName(task) || '文件';
        }
        
        const fileSize = downloadInfo.fileSize || null;
        
        if (fileId && chatId && messageId) {
          this.downloadHistory.recordDownload(fileId, chatId, messageId, filePath, fileName, fileSize);
        }
      } catch (error) {
        this.logger.warn('记录下载历史失败:', error.message);
      }
    }

    // 获取文件名（优先使用 downloadInfo.fileName，否则从 message 中获取）
    let fileName = downloadInfo?.fileName;
    if (!fileName && downloadInfo?.message) {
      const task = { message: downloadInfo.message, chatId: downloadInfo.chatId, mediaType: downloadInfo.mediaType };
      fileName = this.getFileName(task) || '文件';
    }
    fileName = fileName || '文件';

    // 发送完成事件
    this.emit('complete', {
      taskId,
      filePath,
      status: skipped ? 'skipped' : 'completed',
      chatId: downloadInfo?.chatId,
      fileName: fileName,
      ...downloadInfo,
    });

    this.emit('status', this.getStatus());
    
    // 继续处理队列中的下一个任务
    this.processQueue();

    // 处理云盘上传（如果启用）
    if (
      this.config.upload_drive &&
      this.config.upload_drive.enable_upload_file &&
      !skipped
    ) {
      this.handleUpload(filePath, downloadInfo).catch((error) => {
        this.logger.error(`上传失败: ${error.message}`, error);
      });
    }
  }

  async getFilePath(task) {
    const { message, chatId, chatTitle, mediaType } = task;
    const savePath = this.config.save_path || './downloads';
    const pathParts = [];

    // 获取消息 ID（Bot API 使用 message_id，兼容其他格式）
    const messageId = message?.message_id || message?.id || Date.now();

    // 构建路径前缀
    if (this.config.file_path_prefix) {
      for (const prefix of this.config.file_path_prefix) {
        if (prefix === 'chat_title') {
          pathParts.push(this.sanitizeFileName(chatTitle));
        } else if (prefix === 'media_datetime') {
          // Bot API 消息使用 date 字段（Unix 时间戳）
          const date = message.date ? new Date(message.date * 1000) : new Date();
          const format = this.config.date_format || '%Y_%m';
          pathParts.push(this.formatDate(date, format));
        } else if (prefix === 'media_type') {
          pathParts.push(mediaType);
        }
      }
    }

    // 构建文件名
    const fileNameParts = [];
    if (this.config.file_name_prefix) {
      for (const prefix of this.config.file_name_prefix) {
        if (prefix === 'message_id') {
          fileNameParts.push(messageId.toString());
        } else if (prefix === 'file_name') {
          const fileName = this.getFileName(message);
          if (fileName) {
            fileNameParts.push(fileName);
          }
        } else if (prefix === 'caption') {
          // Bot API 消息使用 text 或 caption 字段
          const text = message.text || message.caption || '';
          if (text) {
            const caption = text.substring(0, 50);
            fileNameParts.push(this.sanitizeFileName(caption));
          }
        }
      }
    }

    const split = this.config.file_name_prefix_split || ' - ';
    let fileName = fileNameParts.join(split) || `file_${messageId}`;

    // 获取文件扩展名
    const extension = this.getFileExtension(message, mediaType);
    if (extension && !fileName.endsWith(extension)) {
      fileName += extension;
    }

    return join(savePath, ...pathParts, fileName);
  }

  getFileName(message) {
    // 从 Bot API 消息对象中提取文件名
    if (message.document?.file_name) {
      return message.document.file_name;
    }
    if (message.video?.file_name) {
      return message.video.file_name;
    }
    if (message.audio?.file_name) {
      return message.audio.file_name;
    }
    if (message.voice?.file_name) {
      return message.voice.file_name;
    }
    if (message.animation?.file_name) {
      return message.animation.file_name;
    }
    // 兼容旧格式
    if (message.media?.document?.file_name) {
      return message.media.document.file_name;
    }
    if (message.file_name) {
      return message.file_name;
    }
    return null;
  }

  getFileExtension(message, mediaType) {
    const fileName = this.getFileName(message);
    if (fileName) {
      const match = fileName.match(/\.([^.]+)$/);
      if (match) {
        return '.' + match[1];
      }
    }

    // 根据媒体类型返回默认扩展名
    // 优先从 Bot API 消息对象中获取 mime_type
    let mimeType = '';
    if (message.document?.mime_type) {
      mimeType = message.document.mime_type;
    } else if (message.video?.mime_type) {
      mimeType = message.video.mime_type;
    } else if (message.audio?.mime_type) {
      mimeType = message.audio.mime_type;
    } else if (message.media?.document?.mime_type) {
      mimeType = message.media.document.mime_type;
    } else if (message.mime_type) {
      mimeType = message.mime_type;
    }

    if (mimeType) {
      const extMap = {
        'image/jpeg': '.jpg',
        'image/png': '.png',
        'image/gif': '.gif',
        'video/mp4': '.mp4',
        'video/quicktime': '.mov',
        'audio/mpeg': '.mp3',
        'audio/ogg': '.ogg',
        'application/pdf': '.pdf',
        'application/epub+zip': '.epub',
      };

      for (const [mime, ext] of Object.entries(extMap)) {
        if (mimeType.includes(mime)) {
          return ext;
        }
      }
    }

    // 根据媒体类型返回默认扩展名
    const defaultExt = {
      'photo': '.jpg',
      'video': '.mp4',
      'audio': '.mp3',
      'voice': '.ogg',
      'document': '.bin',
      'animation': '.gif'
    };

    return defaultExt[mediaType] || '';
  }

  sanitizeFileName(name) {
    return name.replace(/[<>:"/\\|?*]/g, '_').trim();
  }

  formatDate(date, format) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');

    return format
      .replace('%Y', year)
      .replace('%m', month)
      .replace('%d', day);
  }

  async handleUpload(filePath, downloadInfo) {
    // 云盘上传功能（简化实现）
    this.logger.info(`准备上传文件: ${filePath}`);
    // TODO: 实现 rclone 或 aligo 上传逻辑
  }

  getStatus() {
    return {
      stats: { ...this.downloadStats },
      active: Array.from(this.activeDownloads.values()),
      queue: this.downloadQueue.length,
    };
  }
}
