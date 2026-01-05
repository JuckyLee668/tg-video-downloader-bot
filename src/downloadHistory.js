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
   * 检查文件是否已下载
   */
  isDownloaded(fileId, chatId, messageId, filePath = null) {
    const key = this.generateFileKey(fileId, chatId, messageId);
    const record = this.history[key];
    
    if (!record) {
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
   */
  recordDownload(fileId, chatId, messageId, filePath, fileName, fileSize = null) {
    const key = this.generateFileKey(fileId, chatId, messageId);
    this.history[key] = {
      fileId,
      chatId,
      messageId,
      filePath,
      fileName,
      fileSize,
      downloadedAt: new Date().toISOString(),
      timestamp: Date.now()
    };
    this.saveHistory();
  }

  /**
   * 获取下载记录
   */
  getRecord(fileId, chatId, messageId) {
    const key = this.generateFileKey(fileId, chatId, messageId);
    return this.history[key] || null;
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
