/**
 * 转发文件队列管理器
 * 使用 SQLite 数据库替代 JSON 文件持久化
 */
export class ForwardedQueue {
  constructor(config, logger, databaseManager) {
    this.config = config;
    this.logger = logger;
    this.db = databaseManager;
  }

  /**
   * 添加转发消息到待下载队列
   */
  addToQueue(chatId, messageId, fileName, mediaType, fileId, forwardInfo = null) {
    try {
      // 检查是否已在队列中
      const existing = this.getFromQueue(chatId, messageId);
      if (existing) {
        this.logger.info(`转发文件已在队列中，更新信息: ${fileName}`);
        return;
      }

      this.db.addForwardedTask(chatId, messageId, fileName, mediaType, fileId, forwardInfo);
      this.logger.info(`转发文件已添加到待下载队列: ${fileName} (ID: ${messageId})`);
    } catch (error) {
      this.logger.error('添加转发任务失败:', error);
    }
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
    try {
      this.db.removeForwardedTask(chatId, messageId);
      this.logger.info(`转发文件已从待下载队列移除: ${chatId}_${messageId}`);
      return true;
    } catch (error) {
      this.logger.error('移除转发任务失败:', error);
      return false;
    }
  }

  /**
   * 检查文件是否在待下载队列中
   */
  isInQueue(chatId, messageId) {
    const task = this.getFromQueue(chatId, messageId);
    return !!task;
  }

  /**
   * 从队列获取任务
   */
  getFromQueue(chatId, messageId) {
    return this.db.getForwardedTask(chatId, messageId);
  }

  /**
   * 获取队列状态
   */
  getQueueStatus() {
    const stats = this.db.getForwardedQueueStats();
    const items = this.db.getPendingForwardedTasks();

    return {
      total: stats.total,
      pending: stats.pending,
      downloading: stats.downloading,
      items: items
    };
  }

  /**
   * 更新队列项状态
   */
  updateStatus(chatId, messageId, status) {
    try {
      this.db.updateForwardedStatus(chatId, messageId, status);
    } catch (error) {
      this.logger.error('更新转发任务状态失败:', error);
    }
  }

  /**
   * 获取所有待下载项目
   */
  getAllPending() {
    return this.db.getPendingForwardedTasks();
  }
}