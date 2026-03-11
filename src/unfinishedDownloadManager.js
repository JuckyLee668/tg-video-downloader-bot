import { readFileSync, writeFileSync, existsSync, statSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

/**
 * 重构的未完成下载处理器
 * 更好地管理未完成和中断的下载
 */
export class UnfinishedDownloadManager {
  constructor(config, logger, downloadHistory, forwardedQueue, databaseManager) {
    this.config = config;
    this.logger = logger;
    this.downloadHistory = downloadHistory;
    this.forwardedQueue = forwardedQueue;
    this.databaseManager = databaseManager;
    this.restoreQueueFile = join(process.cwd(), 'unfinished_downloads.json');
    this.unfinishedTasks = this.loadRestoreQueue();
  }

  /**
   * 加载未完成下载队列
   */
  loadRestoreQueue() {
    try {
      if (existsSync(this.restoreQueueFile)) {
        const content = readFileSync(this.restoreQueueFile, 'utf-8');
        const queue = JSON.parse(content);
        this.logger.info(`加载未完成下载队列: ${Object.keys(queue).length} 条记录`);
        return queue;
      }
    } catch (error) {
      this.logger.warn('加载未完成下载队列失败:', error.message);
    }
    return {};
  }

  /**
   * 保存未完成下载队列
   */
  saveRestoreQueue() {
    try {
      writeFileSync(this.restoreQueueFile, JSON.stringify(this.unfinishedTasks, null, 2), 'utf-8');
    } catch (error) {
      this.logger.error('保存未完成下载队列失败:', error.message);
    }
  }

  /**
   * 扫描并识别所有未完成的下载
   */
  scanUnfinishedDownloads() {
    const unfinished = {
      historyIncomplete: [],
      fileIncomplete: [],
      forwardedPending: [],
      orphanedFiles: [],
      databaseQueue: []
    };

    // 1. 检查历史记录中的未完成下载
    unfinished.historyIncomplete = this.downloadHistory.getIncompleteDownloads();

    // 2. 检查转发队列中的待处理项目
    if (this.forwardedQueue) {
      unfinished.forwardedPending = this.forwardedQueue.getAllPending();
    }

    // 3. 检查数据库下载队列
    if (this.databaseManager) {
      try {
        const dbStats = this.databaseManager.getQueueStats();
        if (dbStats && (dbStats.pending > 0 || dbStats.failed > 0)) {
          // 获取所有待处理和可重试的任务
          const pendingTasks = this.databaseManager.getPendingTasks(1000);
          unfinished.databaseQueue = pendingTasks;
        }
      } catch (error) {
        this.logger.error('扫描数据库队列失败:', error.message);
      }
    }

    // 4. 检查实际文件系统中的不完整文件
    unfinished.fileIncomplete = this.scanIncompleteFiles();

    // 5. 检查孤立文件（历史上有记录但可能有问题的）
    unfinished.orphanedFiles = this.findOrphanedFiles();

    this.logger.info(`扫描完成: 历史未完成=${unfinished.historyIncomplete.length}, 数据库队列=${unfinished.databaseQueue.length}, 文件不完整=${unfinished.fileIncomplete.length}, 转发待处理=${unfinished.forwardedPending.length}, 孤立文件=${unfinished.orphanedFiles.length}`);

    return unfinished;
  }

  /**
   * 扫描文件系统中的不完整文件
   */
  scanIncompleteFiles() {
    const incompleteFiles = [];

    // Scan through download history for files that exist but are incomplete
    for (const [key, record] of Object.entries(this.downloadHistory.history)) {
      if (record.filePath && record.fileSize && existsSync(record.filePath)) {
        try {
          const stats = statSync(record.filePath);
          // If file exists but size doesn't match expected size, it's incomplete
          if (stats.size < record.fileSize) {
            incompleteFiles.push({
              ...record,
              actualSize: stats.size,
              expectedSize: record.fileSize,
              status: 'file_incomplete',
              key
            });
          }
          // If file exists but is 0 bytes and record says it should have content
          else if (stats.size === 0 && record.fileSize > 0) {
            incompleteFiles.push({
              ...record,
              actualSize: 0,
              status: 'file_empty',
              key
            });
          }
        } catch (error) {
          this.logger.warn(`检查文件完整性失败: ${record.filePath}`, error.message);
        }
      }
    }

    return incompleteFiles;
  }

  /**
   * 查找孤立文件（可能有历史记录但文件已不存在，或状态异常）
   */
  findOrphanedFiles() {
    const orphaned = [];

    for (const [key, record] of Object.entries(this.downloadHistory.history)) {
      // Check if file exists
      if (record.filePath) {
        const fileExists = existsSync(record.filePath);

        if (!fileExists && record.status === 'completed') {
          // Completed record but file doesn't exist - might be orphaned
          orphaned.push({
            ...record,
            status: 'orphaned_missing_file',
            key,
            fileExists
          });
        } else if (fileExists && record.status === 'in_progress') {
          // File exists but marked as in-progress, might need resumption
          try {
            const stats = statSync(record.filePath);
            if (stats.size < record.fileSize) {
              orphaned.push({
                ...record,
                actualSize: stats.size,
                status: 'orphaned_incomplete',
                key,
                fileExists
              });
            }
          } catch (error) {
            this.logger.warn(`检查孤儿文件失败: ${record.filePath}`, error.message);
          }
        }
      }
    }

    return orphaned;
  }

  /**
   * 恢复所有未完成的下载
   */
  async restoreAllUnfinished(taskProcessor) {
    const unfinished = this.scanUnfinishedDownloads();

    let restoredCount = 0;

    // 1. 恢复历史记录中的未完成下载
    for (const task of unfinished.historyIncomplete) {
      const restored = await this.restoreHistoryIncomplete(task, taskProcessor);
      if (restored) restoredCount++;
    }

    // 2. 恢复转发队列中的待处理项目
    for (const task of unfinished.forwardedPending) {
      const restored = await this.restoreForwardedPending(task, taskProcessor);
      if (restored) restoredCount++;
    }

    // 3. 恢复文件系统中的不完整文件
    for (const task of unfinished.fileIncomplete) {
      const restored = await this.restoreFileIncomplete(task, taskProcessor);
      if (restored) restoredCount++;
    }

    // 4. 处理孤立文件
    for (const task of unfinished.orphanedFiles) {
      const restored = await this.handleOrphanedFile(task, taskProcessor);
      if (restored) restoredCount++;
    }

    this.logger.info(`总共恢复了 ${restoredCount} 个未完成的下载任务`);
    return restoredCount;
  }

  /**
   * 恢复历史记录中的未完成下载
   */
  async restoreHistoryIncomplete(task, taskProcessor) {
    try {
      // Validate that we have necessary information
      if (!task.fileId || !task.chatId || !task.messageId) {
        this.logger.warn(`跳过无效的历史任务: 缺少必要信息`, { taskId: task.key });
        return false;
      }

      // Check if file exists and get current size
      let startBytes = 0;
      if (task.filePath && existsSync(task.filePath)) {
        try {
          const stats = statSync(task.filePath);
          startBytes = stats.size;
          this.logger.info(`历史任务文件存在: ${task.filePath}, 当前大小: ${this.formatBytes(startBytes)}`);
        } catch (error) {
          this.logger.warn(`无法获取历史任务文件大小: ${task.filePath}`, error.message);
        }
      }

      // Create restore task
      const restoreTask = {
        type: 'history_incomplete',
        fileId: task.fileId,
        chatId: task.chatId,
        messageId: task.messageId,
        filePath: task.filePath,
        fileName: task.fileName,
        mediaType: task.mediaType || 'document',
        startBytes,
        expectedSize: task.fileSize || 0,
        originalTask: task
      };

      // Process the task
      const result = await taskProcessor(restoreTask);

      if (result.success) {
        this.logger.info(`历史未完成任务恢复成功: ${task.fileName}`);
        return true;
      } else {
        this.logger.warn(`历史未完成任务恢复失败: ${task.fileName}`, result.error);
      }
    } catch (error) {
      this.logger.error(`恢复历史未完成任务异常: ${task.fileName}`, error);
    }

    return false;
  }

  /**
   * 恢复转发队列中的待处理项目
   */
  async restoreForwardedPending(task, taskProcessor) {
    try {
      if (!task.fileId || !task.chatId || !task.messageId) {
        this.logger.warn(`跳过无效的转发任务: 缺少必要信息`, { taskId: `${task.chatId}_${task.messageId}` });
        return false;
      }

      // Create restore task for forwarded item
      const restoreTask = {
        type: 'forwarded_pending',
        fileId: task.fileId,
        chatId: task.chatId,
        messageId: task.messageId,
        fileName: task.fileName,
        mediaType: task.mediaType || 'document',
        forwardInfo: task.forwardInfo,
        startBytes: 0, // Start from beginning for forwarded items
        originalTask: task
      };

      // Process the task
      const result = await taskProcessor(restoreTask);

      if (result.success) {
        this.logger.info(`转发待处理任务恢复成功: ${task.fileName}`);
        // Update forwarded queue status
        if (this.forwardedQueue) {
          this.forwardedQueue.updateStatus(task.chatId, task.messageId, 'downloading');
        }
        return true;
      } else {
        this.logger.warn(`转发待处理任务恢复失败: ${task.fileName}`, result.error);
      }
    } catch (error) {
      this.logger.error(`恢复转发待处理任务异常: ${task.fileName}`, error);
    }

    return false;
  }

  /**
   * 恢复文件系统中的不完整文件
   */
  async restoreFileIncomplete(task, taskProcessor) {
    try {
      if (!task.fileId || !task.filePath) {
        this.logger.warn(`跳过无效的文件不完整任务: 缺少必要信息`, { taskId: task.key });
        return false;
      }

      // Create restore task for incomplete file
      const restoreTask = {
        type: 'file_incomplete',
        fileId: task.fileId,
        filePath: task.filePath,
        fileName: task.fileName,
        mediaType: task.mediaType || 'document',
        chatId: task.chatId,
        messageId: task.messageId,
        startBytes: task.actualSize || 0,
        expectedSize: task.expectedSize || task.fileSize || 0,
        originalTask: task
      };

      // Process the task
      const result = await taskProcessor(restoreTask);

      if (result.success) {
        this.logger.info(`文件不完整任务恢复成功: ${task.fileName} (从 ${this.formatBytes(restoreTask.startBytes)} 继续)`);
        return true;
      } else {
        this.logger.warn(`文件不完整任务恢复失败: ${task.fileName}`, result.error);
      }
    } catch (error) {
      this.logger.error(`恢复文件不完整任务异常: ${task.fileName}`, error);
    }

    return false;
  }

  /**
   * 处理孤立文件
   */
  async handleOrphanedFile(task, taskProcessor) {
    try {
      // Handle different types of orphaned files
      if (task.status === 'orphaned_missing_file') {
        // File is marked as completed but doesn't exist - should we redownload?
        this.logger.info(`发现缺失文件: ${task.fileName}, 考虑重新下载`);
        // Optionally re-initiate download
        const restoreTask = {
          type: 'orphaned_missing',
          fileId: task.fileId,
          chatId: task.chatId,
          messageId: task.messageId,
          fileName: task.fileName,
          mediaType: task.mediaType || 'document',
          startBytes: 0,
          originalTask: task
        };

        const result = await taskProcessor(restoreTask);
        if (result.success) {
          this.logger.info(`孤儿缺失文件处理成功: ${task.fileName}`);
          return true;
        }
      } else if (task.status === 'orphaned_incomplete') {
        // File exists but is incomplete despite being marked complete
        this.logger.info(`发现孤儿不完整文件: ${task.fileName}, 从 ${this.formatBytes(task.actualSize)} 继续`);
        const restoreTask = {
          type: 'orphaned_incomplete',
          fileId: task.fileId,
          filePath: task.filePath,
          fileName: task.fileName,
          mediaType: task.mediaType || 'document',
          chatId: task.chatId,
          messageId: task.messageId,
          startBytes: task.actualSize || 0,
          originalTask: task
        };

        const result = await taskProcessor(restoreTask);
        if (result.success) {
          this.logger.info(`孤儿不完整文件处理成功: ${task.fileName}`);
          return true;
        }
      }
    } catch (error) {
      this.logger.error(`处理孤儿文件异常: ${task.fileName}`, error);
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

  /**
   * 获取未完成任务状态
   */
  getStatus() {
    const unfinished = this.scanUnfinishedDownloads();
    return {
      total: Object.values(unfinished).reduce((sum, arr) => sum + arr.length, 0),
      ...unfinished
    };
  }
}