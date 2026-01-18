import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

/**
 * 转发文件队列管理器
 * 用于记录转发过来的待下载文件
 */
export class ForwardedQueue {
  constructor(config, logger) {
    this.config = config;
    this.logger = logger;
    this.queueFile = join(process.cwd(), 'forwarded_queue.json');
    this.queue = this.loadQueue();
  }

  /**
   * 加载待下载队列
   */
  loadQueue() {
    try {
      if (existsSync(this.queueFile)) {
        const content = readFileSync(this.queueFile, 'utf-8');
        const queue = JSON.parse(content);
        this.logger.info(`加载转发待下载队列: ${Object.keys(queue).length} 条记录`);
        return queue;
      }
    } catch (error) {
      this.logger.warn('加载转发待下载队列失败，将创建新队列:', error.message);
    }
    return {};
  }

  /**
   * 保存待下载队列
   */
  saveQueue() {
    try {
      writeFileSync(this.queueFile, JSON.stringify(this.queue, null, 2), 'utf-8');
    } catch (error) {
      this.logger.error('保存转发待下载队列失败:', error.message);
    }
  }

  /**
   * 添加转发消息到待下载队列
   */
  addToQueue(chatId, messageId, fileName, mediaType, fileId, forwardInfo = null) {
    const key = this.generateQueueKey(chatId, messageId);

    // 如果已经有这个文件，只是更新信息
    if (this.queue[key]) {
      this.logger.info(`转发文件已在队列中，更新信息: ${fileName}`);
    }

    this.queue[key] = {
      chatId,
      messageId,
      fileName,
      mediaType,
      fileId,
      forwardInfo: forwardInfo || {},
      addedAt: new Date().toISOString(),
      timestamp: Date.now(),
      status: 'pending' // pending, downloading, completed
    };

    this.saveQueue();
    this.logger.info(`转发文件已添加到待下载队列: ${fileName} (ID: ${messageId})`);
  }

  /**
   * 生成队列唯一键
   */
  generateQueueKey(chatId, messageId) {
    return `forward_${chatId}_${messageId}`;
  }

  /**
   * 从队列中移除已完成的下载
   */
  removeFromQueue(chatId, messageId) {
    const key = this.generateQueueKey(chatId, messageId);
    if (this.queue[key]) {
      delete this.queue[key];
      this.saveQueue();
      this.logger.info(`转发文件已从待下载队列移除: ${chatId}_${messageId}`);
      return true;
    }
    return false;
  }

  /**
   * 检查文件是否在待下载队列中
   */
  isInQueue(chatId, messageId) {
    const key = this.generateQueueKey(chatId, messageId);
    return !!this.queue[key];
  }

  /**
   * 获取队列状态
   */
  getQueueStatus() {
    const pending = Object.values(this.queue).filter(item => item.status === 'pending').length;
    const downloading = Object.values(this.queue).filter(item => item.status === 'downloading').length;
    const total = Object.keys(this.queue).length;

    return {
      total,
      pending,
      downloading,
      items: Object.values(this.queue)
    };
  }

  /**
   * 更新队列项状态
   */
  updateStatus(chatId, messageId, status) {
    const key = this.generateQueueKey(chatId, messageId);
    if (this.queue[key]) {
      this.queue[key].status = status;
      if (status === 'downloading') {
        this.queue[key].startedAt = new Date().toISOString();
      } else if (status === 'completed') {
        this.queue[key].completedAt = new Date().toISOString();
      }
      this.saveQueue();
    }
  }

  /**
   * 获取所有待下载项目
   */
  getAllPending() {
    return Object.values(this.queue).filter(item => item.status === 'pending');
  }
}