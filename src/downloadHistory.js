import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { createHash } from 'crypto';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

/**
 * 下载历史记录管理器
 * 用于记录已下载的文件，避免重复下载
 */
export class DownloadHistory {
  constructor(config, logger) {
    this.config = config;
    this.logger = logger;
    this.historyFile = join(process.cwd(), 'download_history.json');
    this.history = this.loadHistory();
  }

  /**
   * 加载历史记录
   */
  loadHistory() {
    try {
      if (existsSync(this.historyFile)) {
        const content = readFileSync(this.historyFile, 'utf-8');
        const history = JSON.parse(content);
        this.logger.info(`加载下载历史记录: ${Object.keys(history).length} 条记录`);
        return history;
      }
    } catch (error) {
      this.logger.warn('加载下载历史记录失败，将创建新记录:', error.message);
    }
    return {};
  }

  /**
   * 保存历史记录
   */
  saveHistory() {
    try {
      writeFileSync(this.historyFile, JSON.stringify(this.history, null, 2), 'utf-8');
    } catch (error) {
      this.logger.error('保存下载历史记录失败:', error.message);
    }
  }

  /**
   * 生成文件唯一标识
   * 使用 file_id 或消息唯一标识
   */
  generateFileKey(fileId, chatId, messageId) {
    if (fileId) {
      // 使用 file_id 作为主要标识（同一文件在不同消息中共享 file_id）
      return `file_${fileId}`;
    }
    // 如果没有 file_id，使用 chatId + messageId
    return `msg_${chatId}_${messageId}`;
  }

  /**
   * 检查文件是否已下载完成
   */
  isDownloaded(fileId, chatId, messageId, filePath = null) {
    const key = this.generateFileKey(fileId, chatId, messageId);
    const record = this.history[key];

    if (!record) {
      return false;
    }

    // 如果下载未完成，返回 false 以便继续下载
    if (record.status !== 'completed') {
      return false;
    }

    // 如果提供了文件路径，检查文件是否仍然存在
    if (filePath && record.filePath) {
      if (!existsSync(record.filePath)) {
        // 文件已删除，从历史记录中移除
        delete this.history[key];
        this.saveHistory();
        return false;
      }
    }

    return true;
  }

  /**
   * 记录已下载的文件
   * @param {string} fileId - 文件 ID
   * @param {string} chatId - 聊天 ID
   * @param {number|string} messageId - 消息 ID
   * @param {string} filePath - 文件路径
   * @param {string} fileName - 文件名
   * @param {number|null} fileSize - 文件大小
   * @param {string} status - 下载状态 ('completed', 'in_progress')
   * @param {number} downloadedBytes - 已下载字节数
   */
  recordDownload(fileId, chatId, messageId, filePath, fileName, fileSize = null, status = 'completed', downloadedBytes = null) {
    const key = this.generateFileKey(fileId, chatId, messageId);
    this.history[key] = {
      fileId,
      chatId,
      messageId,
      filePath,
      fileName,
      fileSize,
      status,
      downloadedBytes: downloadedBytes || (status === 'completed' ? fileSize : 0),
      downloadedAt: new Date().toISOString(),
      timestamp: Date.now()
    };
    this.saveHistory();
  }

  /**
   * 更新下载进度
   */
  updateProgress(fileId, chatId, messageId, downloadedBytes, fileSize = null) {
    const key = this.generateFileKey(fileId, chatId, messageId);
    const record = this.history[key];
    if (record) {
      record.downloadedBytes = downloadedBytes;
      if (fileSize) {
        record.fileSize = fileSize;
      }
      record.timestamp = Date.now();
      this.saveHistory();
    }
  }

  /**
   * 获取下载进度
   */
  getProgress(fileId, chatId, messageId) {
    const key = this.generateFileKey(fileId, chatId, messageId);
    const record = this.history[key];
    if (record) {
      return {
        downloadedBytes: record.downloadedBytes || 0,
        fileSize: record.fileSize || 0,
        filePath: record.filePath,
        status: record.status
      };
    }
    return null;
  }

  /**
   * 获取下载记录
   */
  getRecord(fileId, chatId, messageId) {
    const key = this.generateFileKey(fileId, chatId, messageId);
    return this.history[key] || null;
  }

  /**
   * 获取所有未完成的下载任务（用于程序启动时恢复）
   * 返回状态为 'in_progress' 的记录
   */
  getIncompleteDownloads() {
    const incomplete = [];
    for (const key in this.history) {
      const record = this.history[key];
      if (record.status === 'in_progress') {
        incomplete.push({
          fileId: record.fileId,
          chatId: record.chatId,
          messageId: record.messageId,
          filePath: record.filePath,
          fileName: record.fileName,
          downloadedBytes: record.downloadedBytes || 0,
          fileSize: record.fileSize || 0
        });
      }
    }
    return incomplete;
  }

  /**
   * 清理无效的下载记录（file_id 过期或文件不存在）
   * @param {Function} fileIdValidator - 异步函数，用于验证 file_id 是否有效
   */
  async cleanInvalidRecords(fileIdValidator = null) {
    let cleaned = 0;
    const now = Date.now();

    for (const key in { ...this.history }) {
      const record = this.history[key];

      // 清理条件：
      // 1. 状态为 'in_progress' 且记录时间超过 7 天（可能已失效）
      // 2. 文件路径不存在
      // 3. file_id 验证失败（如果提供了验证函数）

      let shouldDelete = false;

      // 检查文件是否存在
      if (record.filePath && !existsSync(record.filePath)) {
        shouldDelete = true;
        this.logger.info(`清理记录：文件不存在 - ${record.filePath}`);
      }

      // 检查记录是否过期（7天前的 in_progress 记录）
      if (!shouldDelete && record.status === 'in_progress' && record.timestamp) {
        const age = now - record.timestamp;
        const sevenDays = 7 * 24 * 60 * 60 * 1000;
        if (age > sevenDays) {
          shouldDelete = true;
          this.logger.info(`清理记录：in_progress 记录过期 - ${record.fileName || record.fileId}`);
        }
      }

      // 如果提供了 file_id 验证函数，检查 file_id 是否有效
      if (!shouldDelete && fileIdValidator && record.fileId) {
        try {
          const isValid = await fileIdValidator(record.fileId);
          if (!isValid) {
            shouldDelete = true;
            this.logger.info(`清理记录：file_id 无效 - ${record.fileId.substring(0, 30)}...`);
          }
        } catch (error) {
          // 验证失败，视为无效
          shouldDelete = true;
          this.logger.info(`清理记录：file_id 验证失败 - ${record.fileId.substring(0, 30)}...`);
        }
      }

      if (shouldDelete) {
        delete this.history[key];
        cleaned++;
      }
    }

    if (cleaned > 0) {
      this.saveHistory();
      this.logger.info(`清理了 ${cleaned} 条无效下载记录`);
    }

    return cleaned;
  }

  /**
   * 清理旧记录（可选，用于防止历史记录文件过大）
   */
  cleanOldRecords(daysToKeep = 30) {
    const cutoffTime = Date.now() - (daysToKeep * 24 * 60 * 60 * 1000);
    let cleaned = 0;
    
    for (const key in this.history) {
      const record = this.history[key];
      if (record.timestamp && record.timestamp < cutoffTime) {
        delete this.history[key];
        cleaned++;
      }
    }

    if (cleaned > 0) {
      this.saveHistory();
      this.logger.info(`清理了 ${cleaned} 条旧下载记录（保留 ${daysToKeep} 天内的记录）`);
    }

    return cleaned;
  }

  /**
   * 获取统计信息
   */
  getStats() {
    const total = Object.keys(this.history).length;
    const records = Object.values(this.history);
    
    let totalSize = 0;
    records.forEach(record => {
      if (record.fileSize) {
        totalSize += record.fileSize;
      }
    });

    return {
      total,
      totalSize,
      records: records.length
    };
  }
}
