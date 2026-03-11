import Database from 'better-sqlite3';
import { join } from 'path';
import { existsSync, mkdirSync } from 'fs';

/**
 * SQLite 数据库管理器
 * 用于替代 JSON 文件持久化队列和下载历史
 */
export class DatabaseManager {
  constructor(config, logger) {
    this.config = config;
    this.logger = logger;
    this.dbPath = join(process.cwd(), 'data', 'telegram_downloader.db');

    // 确保数据目录存在
    const dataDir = join(process.cwd(), 'data');
    if (!existsSync(dataDir)) {
      mkdirSync(dataDir, { recursive: true });
    }

    this.initDatabase();
  }

  /**
   * 初始化数据库和表结构
   */
  initDatabase() {
    try {
      this.db = new Database(this.dbPath);

      // 启用 WAL 模式以提高并发性能
      this.db.pragma('journal_mode = WAL');
      this.db.pragma('synchronous = NORMAL');
      this.db.pragma('cache_size = 1000000'); // 1GB cache
      this.db.pragma('temp_store = memory');

      // 创建下载队列表
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS download_queue (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id TEXT UNIQUE NOT NULL,
          chat_id TEXT NOT NULL,
          message_id TEXT,
          file_name TEXT NOT NULL,
          media_type TEXT,
          file_id TEXT,
          file_size INTEGER,
          channel_id TEXT,
          channel_username TEXT,
          channel_title TEXT,
          status TEXT DEFAULT 'pending', -- pending, downloading, completed, failed
          priority INTEGER DEFAULT 0,
          retry_count INTEGER DEFAULT 0,
          max_retries INTEGER DEFAULT 3,
          error_message TEXT,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          started_at DATETIME,
          completed_at DATETIME,
          task_data TEXT -- JSON string for additional task data
        );

        CREATE INDEX IF NOT EXISTS idx_download_queue_status ON download_queue(status);
        CREATE INDEX IF NOT EXISTS idx_download_queue_task_id ON download_queue(task_id);
        CREATE INDEX IF NOT EXISTS idx_download_queue_chat_id ON download_queue(chat_id);
        CREATE INDEX IF NOT EXISTS idx_download_queue_channel ON download_queue(channel_id, channel_username);
      `);

      // 创建转发队列表
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS forwarded_queue (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          queue_key TEXT UNIQUE NOT NULL,
          chat_id TEXT NOT NULL,
          message_id TEXT NOT NULL,
          file_name TEXT NOT NULL,
          media_type TEXT,
          file_id TEXT,
          forward_info TEXT, -- JSON string
          status TEXT DEFAULT 'pending', -- pending, downloading, completed
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          started_at DATETIME,
          completed_at DATETIME
        );

        CREATE INDEX IF NOT EXISTS idx_forwarded_queue_status ON forwarded_queue(status);
        CREATE INDEX IF NOT EXISTS idx_forwarded_queue_key ON forwarded_queue(queue_key);
      `);

      // 创建下载历史表
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS download_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          file_id TEXT UNIQUE NOT NULL,
          file_name TEXT NOT NULL,
          media_type TEXT,
          file_size INTEGER,
          chat_id TEXT,
          message_id TEXT,
          channel_id TEXT,
          channel_username TEXT,
          channel_title TEXT,
          download_path TEXT,
          download_url TEXT,
          status TEXT DEFAULT 'completed', -- completed, failed
          error_message TEXT,
          downloaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          task_data TEXT -- JSON string for additional data
        );

        CREATE INDEX IF NOT EXISTS idx_download_history_file_id ON download_history(file_id);
        CREATE INDEX IF NOT EXISTS idx_download_history_chat_id ON download_history(chat_id);
        CREATE INDEX IF NOT EXISTS idx_download_history_channel ON download_history(channel_id, channel_username);
        CREATE INDEX IF NOT EXISTS idx_download_history_downloaded_at ON download_history(downloaded_at);
      `);

      // 创建下载统计表
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS download_stats (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          date DATE NOT NULL,
          total_downloads INTEGER DEFAULT 0,
          successful_downloads INTEGER DEFAULT 0,
          failed_downloads INTEGER DEFAULT 0,
          total_size INTEGER DEFAULT 0, -- bytes
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(date)
        );

        CREATE INDEX IF NOT EXISTS idx_download_stats_date ON download_stats(date);
      `);

      // 创建已连接频道表
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS connected_channels (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          channel_id TEXT UNIQUE NOT NULL,
          username TEXT,
          title TEXT,
          last_connected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          status TEXT DEFAULT 'active'
        );
        CREATE INDEX IF NOT EXISTS idx_connected_channels_id ON connected_channels(channel_id);
      `);

      // 准备常用语句
      this.prepareStatements();

      // 重置卡住的任务（将上一次运行中处于 downloading 状态的任务重置为 pending）
      this.resetStuckTasks();

      this.logger.info('SQLite 数据库初始化完成');
    } catch (error) {
      this.logger.error('SQLite 数据库初始化失败:', error);
      throw error;
    }
  }

  /**
   * 重置卡住的任务
   * 将所有 'downloading' 状态的任务重置为 'pending'
   */
  resetStuckTasks() {
    try {
      // 重置下载队列
      const resetDownloadQueue = this.db.prepare("UPDATE download_queue SET status = 'pending' WHERE status = 'downloading'");
      const resultDownload = resetDownloadQueue.run();
      if (resultDownload.changes > 0) {
        this.logger.info(`重置了 ${resultDownload.changes} 个卡住的下载队列任务`);
      }

      // 重置转发队列
      const resetForwardedQueue = this.db.prepare("UPDATE forwarded_queue SET status = 'pending' WHERE status = 'downloading'");
      const resultForwarded = resetForwardedQueue.run();
      if (resultForwarded.changes > 0) {
        this.logger.info(`重置了 ${resultForwarded.changes} 个卡住的转发队列任务`);
      }
    } catch (error) {
      this.logger.error('重置卡住任务失败:', error);
    }
  }

  /**
   * 准备常用 SQL 语句
   */
  prepareStatements() {
    // 下载队列相关
    this.stmt = {
      // 下载队列
      insertDownloadTask: this.db.prepare(`
        INSERT OR REPLACE INTO download_queue
        (task_id, chat_id, message_id, file_name, media_type, file_id, file_size,
         channel_id, channel_username, channel_title, status, priority, task_data, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
      `),

      getDownloadTask: this.db.prepare('SELECT * FROM download_queue WHERE task_id = ?'),
      getPendingTasks: this.db.prepare("SELECT * FROM download_queue WHERE status = 'pending' OR (status = 'failed' AND retry_count < max_retries) ORDER BY priority DESC, created_at ASC LIMIT ?"),
      updateTaskStatus: this.db.prepare('UPDATE download_queue SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?'),
      updateTaskStarted: this.db.prepare('UPDATE download_queue SET status = ?, started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?'),
      updateTaskCompleted: this.db.prepare('UPDATE download_queue SET status = ?, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?'),
      updateTaskFailed: this.db.prepare('UPDATE download_queue SET status = ?, error_message = ?, retry_count = retry_count + 1, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?'),
      deleteTask: this.db.prepare('DELETE FROM download_queue WHERE task_id = ?'),
      getQueueStats: this.db.prepare(`
        SELECT
          COUNT(*) as total,
          SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
          SUM(CASE WHEN status = 'downloading' THEN 1 ELSE 0 END) as downloading,
          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed
        FROM download_queue
      `),

      // 转发队列
      insertForwardedTask: this.db.prepare(`
        INSERT OR REPLACE INTO forwarded_queue
        (queue_key, chat_id, message_id, file_name, media_type, file_id, forward_info, status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
      `),

      getForwardedTask: this.db.prepare('SELECT * FROM forwarded_queue WHERE queue_key = ?'),
      getPendingForwardedTasks: this.db.prepare("SELECT * FROM forwarded_queue WHERE status = 'pending' ORDER BY created_at ASC"),
      updateForwardedStatus: this.db.prepare('UPDATE forwarded_queue SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE queue_key = ?'),
      updateForwardedStarted: this.db.prepare('UPDATE forwarded_queue SET status = ?, started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE queue_key = ?'),
      updateForwardedCompleted: this.db.prepare('UPDATE forwarded_queue SET status = ?, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE queue_key = ?'),
      deleteForwardedTask: this.db.prepare('DELETE FROM forwarded_queue WHERE queue_key = ?'),
      getForwardedQueueStats: this.db.prepare(`
        SELECT
          COUNT(*) as total,
          SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
          SUM(CASE WHEN status = 'downloading' THEN 1 ELSE 0 END) as downloading,
          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
        FROM forwarded_queue
      `),

      // 下载历史
      insertDownloadHistory: this.db.prepare(`
        INSERT OR IGNORE INTO download_history
        (file_id, file_name, media_type, file_size, chat_id, message_id,
         channel_id, channel_username, channel_title, download_path, download_url, status, task_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `),

      getDownloadHistory: this.db.prepare('SELECT * FROM download_history WHERE file_id = ?'),
      getDownloadHistoryByChat: this.db.prepare('SELECT * FROM download_history WHERE chat_id = ? ORDER BY downloaded_at DESC LIMIT ?'),
      getDownloadHistoryStats: this.db.prepare(`
        SELECT
          COUNT(*) as total,
          SUM(file_size) as total_size,
          AVG(file_size) as avg_size,
          MAX(file_size) as max_size,
          MIN(file_size) as min_size
        FROM download_history
        WHERE status = 'completed'
      `),

      // 统计
      upsertDownloadStats: this.db.prepare(`
        INSERT INTO download_stats (date, total_downloads, successful_downloads, failed_downloads, total_size, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(date) DO UPDATE SET
          total_downloads = total_downloads + excluded.total_downloads,
          successful_downloads = successful_downloads + excluded.successful_downloads,
          failed_downloads = failed_downloads + excluded.failed_downloads,
          total_size = total_size + excluded.total_size,
          updated_at = CURRENT_TIMESTAMP
      `),

      getDownloadStats: this.db.prepare('SELECT * FROM download_stats WHERE date >= ? ORDER BY date DESC LIMIT ?'),

      // 已连接频道相关
      insertConnectedChannel: this.db.prepare(`
        INSERT OR REPLACE INTO connected_channels (channel_id, username, title, last_connected_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
      `),
      getConnectedChannels: this.db.prepare("SELECT * FROM connected_channels WHERE status = 'active' ORDER BY last_connected_at DESC LIMIT ?"),
      deleteOldestChannels: this.db.prepare(`
        DELETE FROM connected_channels 
        WHERE id IN (
          SELECT id FROM connected_channels 
          ORDER BY last_connected_at ASC 
          LIMIT (SELECT MAX(0, COUNT(*) - ?) FROM connected_channels)
        )
      `),
      updateChannelStatus: this.db.prepare('UPDATE connected_channels SET status = ?, last_connected_at = CURRENT_TIMESTAMP WHERE channel_id = ?')
    };
  }

  /**
   * 频道管理方法
   */
  saveConnectedChannel(channel, maxLimit = 10) {
    try {
      this.stmt.insertConnectedChannel.run(
        channel.id.toString(),
        channel.username || '',
        channel.title || ''
      );
      // 清理多余频道
      this.stmt.deleteOldestChannels.run(maxLimit);
    } catch (error) {
      this.logger.error('保存频道信息失败:', error);
    }
  }

  getSavedChannels(limit = 10) {
    try {
      return this.stmt.getConnectedChannels.all(limit);
    } catch (error) {
      this.logger.error('获取保存频道失败:', error);
      return [];
    }
  }

  /**
   * 下载队列管理方法
   */
  addDownloadTask(task) {
    try {
      const taskId = this.generateTaskId(task);
      
      // 使用 replacer 过滤掉可能导致循环引用的对象
      const taskData = JSON.stringify(task, (key, value) => {
        // 过滤掉已知的导致循环引用的对象实例
        if (key === 'userClient' || key === 'apiClient' || key === 'bot') return undefined;
        return value;
      });

      this.stmt.insertDownloadTask.run(
        taskId,
        task.chatId?.toString() || '',
        task.messageId?.toString() || '',
        task.fileName || '未知文件',
        task.mediaType || 'unknown',
        task.fileId || '',
        task.fileSize || 0,
        task.channel?.id?.toString() || '',
        task.channel?.username || '',
        task.channel?.title || '',
        task.status || 'pending',
        task.priority || 0,
        taskData
      );

      return taskId;
    } catch (error) {
      this.logger.error('添加下载任务失败:', error);
      throw error;
    }
  }

  getDownloadTask(taskId) {
    try {
      const row = this.stmt.getDownloadTask.get(taskId);
      if (row) {
        return {
          ...row,
          taskData: row.task_data ? JSON.parse(row.task_data) : {}
        };
      }
      return null;
    } catch (error) {
      this.logger.error('获取下载任务失败:', error);
      return null;
    }
  }

  getPendingTasks(limit = 100) {
    try {
      const rows = this.stmt.getPendingTasks.all(limit);
      return rows.map(row => ({
        ...row,
        taskData: row.task_data ? JSON.parse(row.task_data) : {}
      }));
    } catch (error) {
      this.logger.error('获取待处理任务失败:', error);
      return [];
    }
  }

  updateTaskStatus(taskId, status, errorMessage = null) {
    try {
      if (status === 'downloading') {
        this.stmt.updateTaskStarted.run(status, taskId);
      } else if (status === 'completed') {
        this.stmt.updateTaskCompleted.run(status, taskId);
      } else if (status === 'failed') {
        this.stmt.updateTaskFailed.run(status, errorMessage, taskId);
      } else {
        this.stmt.updateTaskStatus.run(status, taskId);
      }
    } catch (error) {
      this.logger.error('更新任务状态失败:', error);
    }
  }

  removeTask(taskId) {
    try {
      this.stmt.deleteTask.run(taskId);
    } catch (error) {
      this.logger.error('删除任务失败:', error);
    }
  }

  getQueueStats() {
    try {
      return this.stmt.getQueueStats.get();
    } catch (error) {
      this.logger.error('获取队列统计失败:', error);
      return { total: 0, pending: 0, downloading: 0, completed: 0, failed: 0 };
    }
  }

  /**
   * 获取下载队列列表（分页）
   */
  getDownloadQueueList(limit = 20, offset = 0) {
    try {
      const stmt = this.db.prepare(`
        SELECT task_id, chat_id, message_id, file_name, media_type, file_size, status, created_at, updated_at
        FROM download_queue
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
      `);
      return stmt.all(limit, offset);
    } catch (error) {
      this.logger.error('获取下载队列列表失败:', error);
      return [];
    }
  }

  /**
   * 获取下载历史列表（分页）
   */
  getDownloadHistoryList(limit = 20, offset = 0) {
    try {
      const stmt = this.db.prepare(`
        SELECT file_id, file_name, media_type, file_size, chat_id, message_id, channel_username, channel_title, downloaded_at, status
        FROM download_history
        ORDER BY downloaded_at DESC
        LIMIT ? OFFSET ?
      `);
      return stmt.all(limit, offset);
    } catch (error) {
      this.logger.error('获取下载历史列表失败:', error);
      return [];
    }
  }

  /**
   * 搜索下载历史
   */
  searchDownloadHistory(keyword, limit = 20) {
    try {
      const stmt = this.db.prepare(`
        SELECT file_id, file_name, media_type, file_size, chat_id, message_id, channel_username, channel_title, downloaded_at, status, task_data
        FROM download_history
        WHERE file_name LIKE ? OR channel_title LIKE ? OR channel_username LIKE ? OR task_data LIKE ?
        ORDER BY downloaded_at DESC
        LIMIT ?
      `);
      const searchPattern = `%${keyword}%`;
      return stmt.all(searchPattern, searchPattern, searchPattern, searchPattern, limit);
    } catch (error) {
      this.logger.error('搜索下载历史失败:', error);
      return [];
    }
  }

  /**
   * 转发队列管理方法
   */
  addForwardedTask(chatId, messageId, fileName, mediaType, fileId, forwardInfo = null) {
    try {
      const queueKey = `forward_${chatId}_${messageId}`;
      const forwardInfoStr = JSON.stringify(forwardInfo || {});

      this.stmt.insertForwardedTask.run(
        queueKey,
        chatId.toString(),
        messageId.toString(),
        fileName,
        mediaType,
        fileId,
        forwardInfoStr,
        'pending'
      );

      return queueKey;
    } catch (error) {
      this.logger.error('添加转发任务失败:', error);
      throw error;
    }
  }

  getForwardedTask(chatId, messageId) {
    try {
      const queueKey = `forward_${chatId}_${messageId}`;
      const row = this.stmt.getForwardedTask.get(queueKey);
      if (row) {
        return {
          ...row,
          forwardInfo: row.forward_info ? JSON.parse(row.forward_info) : {}
        };
      }
      return null;
    } catch (error) {
      this.logger.error('获取转发任务失败:', error);
      return null;
    }
  }

  getPendingForwardedTasks() {
    try {
      const rows = this.stmt.getPendingForwardedTasks.all();
      return rows.map(row => ({
        ...row,
        forwardInfo: row.forward_info ? JSON.parse(row.forward_info) : {}
      }));
    } catch (error) {
      this.logger.error('获取待处理转发任务失败:', error);
      return [];
    }
  }

  updateForwardedStatus(chatId, messageId, status) {
    try {
      const queueKey = `forward_${chatId}_${messageId}`;
      if (status === 'downloading') {
        this.stmt.updateForwardedStarted.run(status, queueKey);
      } else if (status === 'completed') {
        this.stmt.updateForwardedCompleted.run(status, queueKey);
      } else {
        this.stmt.updateForwardedStatus.run(status, queueKey);
      }
    } catch (error) {
      this.logger.error('更新转发任务状态失败:', error);
    }
  }

  removeForwardedTask(chatId, messageId) {
    try {
      const queueKey = `forward_${chatId}_${messageId}`;
      this.stmt.deleteForwardedTask.run(queueKey);
    } catch (error) {
      this.logger.error('删除转发任务失败:', error);
    }
  }

  getForwardedQueueStats() {
    try {
      return this.stmt.getForwardedQueueStats.get();
    } catch (error) {
      this.logger.error('获取转发队列统计失败:', error);
      return { total: 0, pending: 0, downloading: 0, completed: 0 };
    }
  }

  /**
   * 下载历史管理方法
   */
  addDownloadHistory(record) {
    try {
      const taskData = JSON.stringify(record.taskData || {});

      this.stmt.insertDownloadHistory.run(
        record.fileId,
        record.fileName,
        record.mediaType,
        record.fileSize || 0,
        record.chatId?.toString() || '',
        record.messageId?.toString() || '',
        record.channelId?.toString() || '',
        record.channelUsername || '',
        record.channelTitle || '',
        record.downloadPath || '',
        record.downloadUrl || '',
        record.status || 'completed',
        taskData
      );
    } catch (error) {
      this.logger.error('添加下载历史失败:', error);
    }
  }

  getDownloadHistory(fileId) {
    try {
      const row = this.stmt.getDownloadHistory.get(fileId);
      if (row) {
        return {
          ...row,
          taskData: row.task_data ? JSON.parse(row.task_data) : {}
        };
      }
      return null;
    } catch (error) {
      this.logger.error('获取下载历史失败:', error);
      return null;
    }
  }

  getDownloadHistoryByChat(chatId, limit = 50) {
    try {
      const rows = this.stmt.getDownloadHistoryByChat.all(chatId.toString(), limit);
      return rows.map(row => ({
        ...row,
        taskData: row.task_data ? JSON.parse(row.task_data) : {}
      }));
    } catch (error) {
      this.logger.error('获取聊天下载历史失败:', error);
      return [];
    }
  }

  getDownloadHistoryStats() {
    try {
      return this.stmt.getDownloadHistoryStats.get();
    } catch (error) {
      this.logger.error('获取下载历史统计失败:', error);
      return { total: 0, total_size: 0, avg_size: 0, max_size: 0, min_size: 0 };
    }
  }

  /**
   * 统计管理方法
   */
  recordDownloadStats(success, fileSize = 0) {
    try {
      const today = new Date().toISOString().split('T')[0];
      this.stmt.upsertDownloadStats.run(
        today,
        1, // total_downloads
        success ? 1 : 0, // successful_downloads
        success ? 0 : 1, // failed_downloads
        fileSize
      );
    } catch (error) {
      this.logger.error('记录下载统计失败:', error);
    }
  }

  getDownloadStats(days = 30) {
    try {
      const startDate = new Date();
      startDate.setDate(startDate.getDate() - days);
      const startDateStr = startDate.toISOString().split('T')[0];

      return this.stmt.getDownloadStats.all(startDateStr, days);
    } catch (error) {
      this.logger.error('获取下载统计失败:', error);
      return [];
    }
  }

  /**
   * 工具方法
   * 生成任务 ID，必须是确定性的以便于查重
   */
  generateTaskId(task) {
    const idPart = task.userClient && task.channel
      ? (task.channel.id || task.channel.username || 'unknown')
      : (task.chatId || 'unknown');
    const messageId = task.messageId || 'unknown';
    // 移除 Date.now()，保证同一个消息生成的 ID 始终一致
    return `${idPart}_${messageId}`;
  }

  /**
   * 关闭数据库连接
   */
  close() {
    if (this.db) {
      this.db.close();
      this.logger.info('SQLite 数据库连接已关闭');
    }
  }

  /**
   * 数据库维护方法
   */
  vacuum() {
    try {
      this.db.exec('VACUUM');
      this.logger.info('数据库清理完成');
    } catch (error) {
      this.logger.error('数据库清理失败:', error);
    }
  }

  /**
   * 获取数据库信息
   */
  getDatabaseInfo() {
    try {
      const info = {
        downloadQueue: this.getQueueStats(),
        forwardedQueue: this.getForwardedQueueStats(),
        downloadHistory: this.getDownloadHistoryStats(),
        downloadStats: this.getDownloadStats(7) // 最近7天
      };
      return info;
    } catch (error) {
      this.logger.error('获取数据库信息失败:', error);
      return null;
    }
  }
}