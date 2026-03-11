import { EventEmitter } from 'events';
import { existsSync, mkdirSync, statSync } from 'fs';
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
  constructor(config, logger, apiClient, downloadHistory = null, userClient = null, databaseManager = null) {
    super();
    this.config = config;
    this.logger = logger;
    this.apiClient = apiClient;
    this.userClient = userClient; // optional Telegram user client (for channel downloads)
    this.downloadHistory = downloadHistory;
    this.db = databaseManager; // SQLite database manager
    this.downloadQueue = []; // Keep in-memory queue for active processing
    this.activeDownloads = new Map();
    this.downloadStats = {
      total: 0,
      completed: 0,
      failed: 0,
      active: 0,
      skipped: 0,
    };
    this.maxConcurrent = config.max_download_task || 5;

    // Always start fresh download instead of resume (new feature)
    this.alwaysFreshDownload = config.always_fresh_download || false;

    // Batch processing optimizations
    this.batchSize = config.batch_size || 10;  // Number of tasks to process in batch
    this.batchInterval = config.batch_interval || 1000;  // Interval between batches in ms
    this.processingBatch = false;
    this.batchStartTime = null;
    this.lastBatchProcessed = 0;

    // Adaptive concurrency adjustment
    this.adaptiveConcurrency = config.adaptive_concurrency || false;
    this.concurrencyAdjustmentInterval = config.concurrency_adjustment_interval || 30000; // 30 seconds
    this.successfulDownloads = 0;
    this.failedDownloads = 0;
    this.adjustmentTimer = null;

    // Initialize adaptive concurrency if enabled
    if (this.adaptiveConcurrency) {
      this.startConcurrencyAdjustment();
    }

    // Always start fresh download instead of resume (new feature)
    this.alwaysFreshDownload = config.always_fresh_download || false;

    // Batch processing optimizations
    this.batchSize = config.batch_size || 10;  // Number of tasks to process in batch
    this.batchInterval = config.batch_interval || 1000;  // Interval between batches in ms
    this.processingBatch = false;
    this.batchStartTime = null;
    this.lastBatchProcessed = 0;

    // Adaptive concurrency adjustment
    this.adaptiveConcurrency = config.adaptive_concurrency || false;
    this.concurrencyAdjustmentInterval = config.concurrency_adjustment_interval || 30000; // 30 seconds
    this.successfulDownloads = 0;
    this.failedDownloads = 0;
    this.adjustmentTimer = null;

    // Initialize adaptive concurrency if enabled
    if (this.adaptiveConcurrency) {
      this.startConcurrencyAdjustment();
    }
  }

  startConcurrencyAdjustment() {
    this.adjustmentTimer = setInterval(() => {
      const totalAttempts = this.successfulDownloads + this.failedDownloads;
      if (totalAttempts > 10) { // Only adjust if we have enough data
        const successRate = this.successfulDownloads / totalAttempts;

        if (successRate > 0.9 && this.maxConcurrent < 20) {
          // High success rate, can increase concurrency
          this.maxConcurrent = Math.min(this.maxConcurrent + 1, 20);
          this.logger.info(`自适应并发调整: 增加到 ${this.maxConcurrent} (成功率: ${(successRate * 100).toFixed(1)}%)`);
        } else if (successRate < 0.8 && this.maxConcurrent > 2) {
          // Low success rate, decrease concurrency
          this.maxConcurrent = Math.max(this.maxConcurrent - 1, 2);
          this.logger.info(`自适应并发调整: 减少到 ${this.maxConcurrent} (成功率: ${(successRate * 100).toFixed(1)}%)`);
        }

        // Reset counters
        this.successfulDownloads = 0;
        this.failedDownloads = 0;
      }
    }, this.concurrencyAdjustmentInterval);
  }

  /**
   * Record download success/failure for adaptive concurrency
   */
  recordDownloadOutcome(success) {
    if (this.adaptiveConcurrency) {
      if (success) {
        this.successfulDownloads++;
      } else {
        this.failedDownloads++;
      }
    }
  }

  /**
   * Clean up resources
   */
  destroy() {
    if (this.adjustmentTimer) {
      clearInterval(this.adjustmentTimer);
      this.adjustmentTimer = null;
    }
  }

  async init() {
    // 确保下载目录存在
    const savePath = this.config.save_path || './downloads';
    if (!existsSync(savePath)) {
      mkdirSync(savePath, { recursive: true });
    }

    // 启动队列处理器以恢复数据库中的挂起任务
    this.processQueue();
  }

  async addDownloadTask(task) {
    // 构建唯一键，必须与 databaseManager.generateTaskId 逻辑一致
    const idPart = task.userClient && task.channel ? (task.channel.id || task.channel.username) : task.chatId;
    const taskId = `${idPart}_${task.messageId || 'unknown'}`;
    task.dbId = taskId; // 统一使用该 ID 作为数据库主键

    // 1. 前置检查：是否已经在下载历史中（成功下载过）
    // 如果没有设置强制重新下载，则执行查重
    if (this.downloadHistory && !task.forceFreshDownload) {
      const fileId = task.fileId || `msg_${task.chatId}_${task.messageId}`;
      if (this.downloadHistory.isDownloaded(fileId, task.chatId, task.messageId)) {
        this.logger.info(`文件已在历史记录中，跳过添加: ${task.fileName || '文件'} (ID: ${taskId})`);
        return 'skipped'; 
      }
    }

    // 2. 检查内存和数据库中的重复任务
    const duplicateInQueue = this.db ? this.checkDuplicateInDatabase(task) : this.checkDuplicateInMemory(task);
    const duplicateInActive = this.activeDownloads.has(taskId);

    if (duplicateInQueue || duplicateInActive) {
      this.logger.warn(`任务已存在，跳过重复添加: ${task.fileName || '文件'} (ID: ${taskId})`);
      return 'duplicate';
    }

    // 3. 添加到数据库
    if (this.db) {
      try {
        this.db.addDownloadTask(task);
      } catch (error) {
        this.logger.error('保存下载任务到数据库失败:', error);
      }
    }

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
    return 'added';
  }
  /**
   * Check for duplicates in database
   */
  checkDuplicateInDatabase(task) {
    if (!this.db) return false;

    try {
      // Get pending tasks from database and check for duplicates
      const pendingTasks = this.db.getPendingTasks(1000); // Check recent tasks

      return pendingTasks.some(t => {
        const taskData = t.taskData || {};
        if (task.userClient && task.channel && taskData.userClient && taskData.channel) {
          const ch1 = taskData.channel.id || taskData.channel.username;
          const ch2 = task.channel.id || task.channel.username;
          return ch1 === ch2 &&
                 (taskData.messageId === task.messageId ||
                  (taskData.fileId && task.fileId && taskData.fileId === task.fileId));
        } else {
          return taskData.chatId === task.chatId &&
                 (taskData.messageId === task.messageId ||
                  (taskData.fileId && task.fileId && taskData.fileId === task.fileId));
        }
      });
    } catch (error) {
      this.logger.error('检查数据库重复任务失败:', error);
      return false;
    }
  }

  /**
   * Check for duplicates in memory (fallback)
   */
  checkDuplicateInMemory(task) {
    return this.downloadQueue.some(t => {
      if (t.userClient && t.channel && task.userClient && task.channel) {
        const ch1 = t.channel.id || t.channel.username;
        const ch2 = task.channel.id || task.channel.username;
        return ch1 === ch2 &&
               (t.messageId === task.messageId ||
                (t.fileId && task.fileId && t.fileId === task.fileId));
      } else {
        return t.chatId === task.chatId &&
               (t.messageId === task.messageId ||
                (t.fileId && task.fileId && t.fileId === task.fileId));
      }
    });
  }

  async processQueue() {
    // Load pending tasks from database if available
    if (this.db && this.downloadQueue.length === 0) {
      try {
        const pendingTasks = this.db.getPendingTasks(this.batchSize);
        for (const dbTask of pendingTasks) {
          const task = dbTask.taskData || {};
          task.dbId = dbTask.task_id; // Store database ID
          this.downloadQueue.push(task);
        }
        this.logger.debug(`从数据库加载了 ${pendingTasks.length} 个待处理任务`);
      } catch (error) {
        this.logger.error('从数据库加载待处理任务失败:', error);
      }
    }

    while (
      this.downloadQueue.length > 0 &&
      this.activeDownloads.size < this.maxConcurrent
    ) {
      const task = this.downloadQueue.shift();

      // Update database status to downloading
      if (this.db && task.dbId) {
        try {
          this.db.updateTaskStatus(task.dbId, 'downloading');
        } catch (error) {
          this.logger.error('更新数据库任务状态失败:', error);
        }
      }

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
    const { message, chatId, chatTitle, mediaType, fileId, needRefreshFileId } = task;
    const messageId = message?.message_id || message?.id || 'unknown';
    const taskId = `${chatId}_${messageId}`;

    // 获取文件名
    const fileName = task.fileName || this.getFileName(task) || '文件';

    this.activeDownloads.set(taskId, {
      dbId: task.dbId, // 保存数据库 ID，用于完成后清理
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

      // 检查是否存在部分下载的文件（用于断点续传）
      let startBytes = 0;
      let resumeFromPartial = false;

      // 检查是否配置为始终重新开始下载或强制重新开始
      if (this.alwaysFreshDownload || task.forceFreshDownload) {
        this.logger.info(`${task.forceFreshDownload ? '强制' : '配置'}重新开始下载，忽略历史进度: ${filePath}`);
        startBytes = 0;
      } else {
        // 首先检查历史记录中的进度
        if (this.downloadHistory && fileId) {
          const progress = this.downloadHistory.getProgress(fileId, chatId, messageId);
          if (progress && progress.filePath === filePath && progress.downloadedBytes > 0) {
            // 从历史记录获取已下载的字节数
            startBytes = progress.downloadedBytes;
            if (existsSync(filePath)) {
              // 验证文件大小是否匹配
              try {
                const fileStats = statSync(filePath);
                if (fileStats.size === startBytes) {
                  resumeFromPartial = true;
                  this.logger.info(`从历史记录恢复下载进度，从 ${formatBytes(startBytes)} 开始: ${filePath}`);
                } else {
                  // 文件大小不匹配，重新开始下载
                  this.logger.warn(`文件大小不匹配（历史: ${startBytes}, 文件: ${fileStats.size}），重新下载`);
                  startBytes = 0;
                }
              } catch (err) {
                this.logger.warn(`无法验证文件大小，将重新下载: ${err.message}`);
                startBytes = 0;
              }
            } else {
              // 文件不存在但历史记录有进度，重新下载
              this.logger.warn(`文件不存在但历史记录有进度，重新下载`);
              startBytes = 0;
            }
          }
        }

        // 如果历史记录中没有进度，检查本地文件
        if (startBytes === 0 && existsSync(filePath)) {
          try {
            const fileStats = statSync(filePath);
            startBytes = fileStats.size;
            if (startBytes > 0) {
              resumeFromPartial = true;
              this.logger.info(`检测到部分下载的文件，继续从 ${formatBytes(startBytes)} 开始下载: ${filePath}`);
            }
          } catch (err) {
            this.logger.warn(`无法获取文件大小，将重新下载: ${filePath}`);
          }
        }
      }

      // 如果需要断点续传，先在历史记录中创建/更新进度记录
      if (startBytes > 0 && this.downloadHistory && fileId) {
        const fileName = this.getFileName(task) || '文件';
        this.downloadHistory.recordDownload(fileId, chatId, messageId, filePath, fileName, null, 'in_progress', startBytes);
      }

      // 通过远程 API 下载文件
      let lastUpdateTime = Date.now();
      let lastDownloadedBytes = startBytes; // 记录起始位置，用于计算实际下载量
      let estimatedTotalBytes = 0;

      const progressCallback = (progress, downloadedBytes, totalBytes) => {
        const downloadInfo = this.activeDownloads.get(taskId);
        if (!downloadInfo) return;

        const now = Date.now();
        const timeDelta = (now - lastUpdateTime) / 1000; // 秒

        // 更新已下载字节数（downloadedBytes 已包含起始位置的字节数）
        downloadInfo.downloadedBytes = downloadedBytes || 0;

        // 计算下载速度（基于实际下载的字节数变化）
        if (timeDelta > 0 && downloadedBytes > lastDownloadedBytes) {
          const bytesDelta = downloadedBytes - lastDownloadedBytes;
          downloadInfo.speed = bytesDelta / timeDelta; // 字节/秒
        }

        // 更新文件大小和进度
        if (totalBytes > 0) {
          // 有总大小信息，直接计算进度
          // 如果是断点续传，totalBytes 已经是包含起始位置的总大小
          downloadInfo.fileSize = totalBytes;
          estimatedTotalBytes = totalBytes;
          downloadInfo.progress = Math.round(progress * 100);
        } else if (downloadedBytes > startBytes) {
          // 没有总大小信息，但已开始下载（下载了超过起始位置的数据）
          // 基于已下载字节数和下载速度估算总大小
          const actualDownloaded = downloadedBytes - startBytes;
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

        // 定期保存下载进度到历史记录（每10秒）
        if (now % 10000 < 500 && this.downloadHistory && fileId) {
          try {
            const actualBytes = downloadInfo.downloadedBytes || downloadedBytes;
            const total = downloadInfo.fileSize || totalBytes;
            this.downloadHistory.updateProgress(fileId, chatId, messageId, actualBytes, total);
          } catch (e) {
            // 忽略历史记录更新错误
          }
        }

        // 触发进度事件（不输出到终端，只通过WebSocket发送）
        this.emit('progress', {
          taskId,
          ...downloadInfo,
        });
      };

      // 调试日志：显示下载策略参数
      this.logger.info(`下载策略: needRefreshFileId=${needRefreshFileId}, chatId=${chatId}, messageId=${messageId}, fileId=${fileId ? fileId.substring(0, 20) + '...' : 'null'}`);

      // 下载策略：
      // 1. 如果任务指定了 useUserClient 和 channel 信息，优先使用 userClient 下载
      // 2. 如果 needRefreshFileId 为 true，使用 chatId + messageId 下载
      // 3. 如果 fileId 有效，使用 fileId 下载
      // 4. 如果有 messageId，使用 messageId 下载
      if (task.useUserClient && task.channel && this.userClient) {
        // 使用 DownloadManager 自身的 userClient
        const channelInfo = task.channel;
        const msgId = messageId || (message && (message.id || message.message_id));
        if (!msgId) {
          throw new Error('频道下载任务缺少消息 ID');
        }
        this.logger.info(`使用 userClient 下载: channel=${channelInfo.username||channelInfo.id}, msgId=${msgId}`);
        await this.userClient.downloadMedia(channelInfo, msgId, filePath, progressCallback);
      } else if (needRefreshFileId && chatId && messageId && String(messageId) !== 'unknown') {
        // 使用 chatId + messageId 下载，让服务器重新从 Telegram 获取文件
        this.logger.info(`需要刷新 file_id，使用 messageId 下载: chatId=${chatId}, messageId=${messageId}`);
        await this.apiClient.downloadMediaByMessageId(
          chatId,
          messageId,
          filePath,
          progressCallback,
          3
        );
      } else if (fileId) {
        // 优先使用 file_id 下载（Bot API 模式，带重试，支持断点续传）
        await this.apiClient.downloadMediaByFileId(
          fileId,
          filePath,
          progressCallback,
          3, // 重试3次
          startBytes // 起始字节数，用于断点续传
        );
      } else if (message && (message.id || message.message_id)) {
        // 回退到使用消息 ID（如果远程 API 支持）
        await this.apiClient.downloadMediaByMessageId(
          chatId,
          message.id || message.message_id,
          filePath,
          progressCallback,
          3 // 重试3次
        );
      } else {
        throw new Error('无法获取文件 ID 或消息 ID');
      }

      this.completeDownload(taskId, filePath, false);
    } catch (error) {
      const downloadInfo = this.activeDownloads.get(taskId);

      // 获取 fileName 用于错误处理
      const fileName = task.fileName || this.getFileName(task) || '文件';

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

      // 记录失败的下载结果，用于自适应并发调整
      this.recordDownloadOutcome(false);

      // 如果是 file_id 无效错误，且有 messageId，尝试使用 chatId + messageId 重试
      const messageIdStr = String(messageId);
      if ((error.message?.includes('wrong file_id') || error.message?.includes('temporarily unavailable')) &&
          chatId && messageId && messageIdStr !== 'unknown' && !needRefreshFileId) {
        this.logger.warn(`file_id 无效，尝试使用 messageId 重试: chatId=${chatId}, messageId=${messageId}`);

        // 重新尝试下载，使用 messageId，设置 needRefreshFileId 为 true 避免再次进入这个分支
        try {
          await this.apiClient.downloadMediaByMessageId(
            chatId,
            messageId,
            filePath, // filePath is already defined above the try/catch
            progressCallback,
            3
          );

          // 下载成功，更新历史记录并完成
          if (this.downloadHistory && fileId) {
            this.downloadHistory.recordDownload(fileId, chatId, messageId, filePath, fileName, null, 'completed', 0);
          }
          this.completeDownload(taskId, filePath, false);
          return;
        } catch (retryError) {
          this.logger.error(`使用 messageId 重试失败:`, retryError.message);
          // 继续执行下面的清理逻辑
        }
      }

      // 如果是 file_id 无效错误，清理无效的历史记录
      if ((error.message?.includes('wrong file_id') || error.message?.includes('temporarily unavailable')) &&
          this.downloadHistory && downloadInfo?.fileId) {
        try {
          const key = this.downloadHistory.generateFileKey(downloadInfo.fileId, downloadInfo.chatId, downloadInfo.messageId);
          delete this.downloadHistory.history[key];
          this.downloadHistory.saveHistory();
          this.logger.warn(`已清理无效的 file_id 历史记录: ${downloadInfo.fileId.substring(0, 30)}...`);
        } catch (e) {
          this.logger.warn('清理无效历史记录失败:', e.message);
        }
      }

      // Update database status to failed
      if (this.db && task.dbId) {
        try {
          // 获取当前重试次数，决定是否从队列移除
          const dbTask = this.db.getDownloadTask(task.dbId);
          const maxRetries = dbTask?.max_retries || 3;
          const currentRetries = dbTask?.retry_count || 0;

          if (currentRetries >= maxRetries) {
            this.logger.warn(`任务已达到最大重试次数 (${maxRetries})，将从队列中移除并记录为失败: ${task.dbId}`);
            this.db.removeTask(task.dbId);
            
            // 记录失败历史
            const fileId = task.fileId || `msg_${task.chatId}_${task.messageId}`;
            const historyRecord = {
              fileId: fileId,
              fileName: fileName,
              mediaType: task.mediaType,
              fileSize: downloadInfo?.fileSize || 0,
              chatId: String(task.chatId),
              messageId: String(task.messageId),
              downloadPath: null,
              status: 'failed',
              error_message: error.message,
              taskData: {
                error: error.message,
                failedAt: new Date().toISOString()
              }
            };
            this.db.addDownloadHistory(historyRecord);
          } else {
            this.db.updateTaskStatus(task.dbId, 'failed', error.message);
          }
        } catch (dbError) {
          this.logger.error('更新数据库任务失败状态或清理失败:', dbError);
        }
      }

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
      // 记录成功的下载结果，用于自适应并发调整
      this.recordDownloadOutcome(true);
    }
    this.activeDownloads.delete(taskId);
    this.downloadStats.active = this.activeDownloads.size;

    this.logger.info(
      `${skipped ? '跳过' : '完成'}下载: ${filePath} (${taskId})`
    );

    // Update database status to completed OR delete it
    if (this.db && downloadInfo && downloadInfo.dbId) {
      try {
        // 策略：任务完成后，从活跃队列中删除，保持 download_queue 表简洁
        this.db.removeTask(downloadInfo.dbId);
        this.logger.debug(`任务已从下载队列中清理: ${downloadInfo.dbId}`);
      } catch (dbError) {
        this.logger.error('清理数据库任务失败:', dbError);
      }
    }

    // 记录到下载历史（仅成功下载）
    if (this.downloadHistory && filePath && !skipped && downloadInfo) {
      try {
        const fileId = downloadInfo.fileId || `msg_${downloadInfo.chatId}_${downloadInfo.messageId}`;
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
        const downloadedBytes = downloadInfo.downloadedBytes || fileSize;

        if (chatId && messageId) {
          // 记录下载完成状态到 JSON 历史
          this.downloadHistory.recordDownload(fileId, chatId, messageId, filePath, fileName, fileSize, 'completed', downloadedBytes);

          // 记录到数据库下载历史
          if (this.db) {
            try {
              const historyRecord = {
                fileId: fileId, // 确保即使没有 Telegram file_id 也有唯一标识
                fileName,
                mediaType: downloadInfo.mediaType,
                fileSize,
                chatId: String(chatId),
                messageId: String(messageId),
                downloadPath: filePath,
                downloadUrl: null,
                status: 'completed',
                taskData: {
                  channelId: downloadInfo.channelId,
                  channelUsername: downloadInfo.channelUsername,
                  channelTitle: downloadInfo.channelTitle,
                  completedAt: new Date().toISOString()
                }
              };
              this.db.addDownloadHistory(historyRecord);
            } catch (dbError) {
              this.logger.error('记录数据库下载历史失败:', dbError);
            }
          }
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
          // 如果启用了同频道文件合并，则跳过日期前缀
          if (this.config.group_same_channel_files !== true) {
            const date = message.date ? new Date(message.date * 1000) : new Date();
            const format = this.config.date_format || '%Y_%m';
            pathParts.push(this.formatDate(date, format));
          }
          // 否则跳过日期前缀，直接处理下一个prefix
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
          const fileName = task.fileName || this.getFileName(message);
          if (fileName) {
            fileNameParts.push(fileName);
          } else {
            // 如果 file_name 为空，尝试使用 caption
            const text = message.text || message.caption || '';
            if (text) {
              const caption = text.replace(/[\n\r\s]+/g, ' ').trim().substring(0, 50);
              if (caption) fileNameParts.push(this.sanitizeFileName(caption));
            }
          }
        } else if (prefix === 'caption') {
          // Bot API 消息使用 text 或 caption 字段
          const text = message.text || message.caption || '';
          if (text) {
            const caption = text.replace(/[\n\r\s]+/g, ' ').trim().substring(0, 50);
            if (caption) fileNameParts.push(this.sanitizeFileName(caption));
          }
        }
      }
    }

    const split = this.config.file_name_prefix_split || ' - ';
    let fileName = fileNameParts.join(split);
    
    // 如果文件名仍然为空，最后的保底方案
    if (!fileName) {
      const text = message.text || message.caption || '';
      if (text) {
        const caption = text.replace(/[\n\r\s]+/g, ' ').trim().substring(0, 50);
        fileName = this.sanitizeFileName(caption);
      }
      if (!fileName) {
        fileName = `file_${messageId}`;
      }
    }

    // 获取文件扩展名
    const extension = this.getFileExtension(message, mediaType);
    if (extension && !fileName.endsWith(extension)) {
      fileName += extension;
    }

    return join(savePath, ...pathParts, fileName);
  }

  getFileName(message) {
    if (!message) return null;
    
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

    // 尝试从说明文字中提取作为文件名
    const text = message.caption || message.text || '';
    if (text) {
      const cleanText = text.replace(/[\n\r\s]+/g, ' ').trim();
      if (cleanText) {
        return cleanText.substring(0, 50);
      }
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
