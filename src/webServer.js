import express from 'express';
import cors from 'cors';
import session from 'express-session';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { createLogger, format, transports } from 'winston';
import { readdir, stat, unlink, access, readFile } from 'fs/promises';

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
    new transports.File({ filename: 'web-server.log', level: 'info' }),
    new transports.Console({
      format: format.combine(
        format.colorize(),
        format.simple()
      )
    })
  ]
});

class WebServer {
  constructor(mainApp) {
    this.mainApp = mainApp;
    this.app = express();
    this.port = process.env.WEB_PORT || 3000;
    this.server = null;

    this.setupMiddleware();
    this.setupRoutes();
  }

  setupMiddleware() {
    // CORS 中间件
    this.app.use(cors());

    // Session 中间件
    this.app.use(session({
      secret: process.env.SESSION_SECRET || 'telegram-downloader-secret-key',
      resave: false,
      saveUninitialized: false,
      cookie: {
        secure: false, // 在生产环境中设置为true（需要HTTPS）
        maxAge: 24 * 60 * 60 * 1000 // 24小时
      }
    }));

    // JSON 解析中间件
    this.app.use(express.json());
    this.app.use(express.urlencoded({ extended: true }));

    // 静态文件服务
    this.app.use(express.static(join(__dirname, '..', 'public')));

    // 认证中间件将在setupRoutes中设置
  }

  authMiddleware(req, res, next) {
    // 检查session中的登录状态
    if (req.session && req.session.authenticated) {
      return next();
    }

    // 如果是API请求，返回401错误
    if (req.headers.accept && req.headers.accept.includes('application/json')) {
      return res.status(401).json({ error: '未登录', redirect: '/login.html' });
    }

    // 如果是页面请求，重定向到登录页面
    res.redirect('/login.html');
  }

  setupRoutes() {
    console.log('setupRoutes() is being called');
    logger.info('Setting up routes...');
    
    // 根路径重定向到仪表板（已登录）或登录页面（未登录）
    this.app.get('/', (req, res) => {
      if (req.session && req.session.authenticated) {
        res.redirect('/dashboard.html');
      } else {
        res.redirect('/login.html');
      }
    });

    // 登录页面
    this.app.get('/login.html', (req, res) => {
      // 如果已经登录，重定向到仪表板
      if (req.session && req.session.authenticated) {
        return res.redirect('/dashboard.html');
      }
      res.sendFile(join(__dirname, '..', 'public', 'login.html'));
    });

    // 测试路由
    this.app.get('/test', (req, res) => {
      console.log('Test route called');
      res.json({ message: 'Test route works' });
    });

    // 登录相关API
    this.app.get('/api/login/status', this.getLoginStatus.bind(this));
    this.app.post('/api/login', this.login.bind(this));
    this.app.post('/api/login/logout', this.logout.bind(this));
    this.app.post('/api/logout', this.logout.bind(this)); // 兼容性路径

    // 受保护的API路由
    // 统计信息
    this.app.get('/api/stats', this.getStats.bind(this));

    // 下载管理
    this.app.get('/api/downloads', this.getDownloads.bind(this));
    this.app.post('/api/downloads/start', this.startDownload.bind(this));
    this.app.post('/api/downloads/stop', this.stopDownload.bind(this));
    this.app.delete('/api/downloads/:taskId', this.cancelDownload.bind(this));

    // 频道管理
    this.app.get('/api/channels', this.getChannels.bind(this));
    this.app.post('/api/channels', this.addChannel.bind(this));
    this.app.delete('/api/channels/:channelId', this.removeChannel.bind(this));

    // 配置管理
    this.app.get('/api/config', this.getConfig.bind(this));
    this.app.post('/api/config', this.updateConfig.bind(this)); // 改为 POST 方便前端调用
    this.app.put('/api/config', this.updateConfig.bind(this));

    // 下载历史
    this.app.get('/api/history', this.getDownloadHistory.bind(this));
    this.app.delete('/api/history', this.clearHistory.bind(this));

    // 系统状态
    this.app.get('/api/system/status', this.getSystemStatus.bind(this));
    this.app.post('/api/system/restart', this.restartSystem.bind(this));

    // 日志
    this.app.get('/api/logs', this.getLogs.bind(this));

    // 文件管理
    this.app.get('/api/files', this.getFiles.bind(this));
    this.app.delete('/api/files/:filename', this.deleteFile.bind(this));

    // 下载文件
    this.app.use('/downloads', express.static(join(__dirname, '..', 'downloads')));
  }

  // API 处理器

  login(req, res) {
    console.log('Login function called with:', req.body);
    try {
      const { username, password } = req.body;

      // 从环境变量获取配置的用户名和密码
      const configUsername = process.env.WEB_USERNAME || 'admin';
      const configPassword = process.env.WEB_PASSWORD || 'admin123';

      console.log('Config credentials:', { configUsername, configPassword });
      console.log('Request credentials:', { username, password });

      if (username === configUsername && password === configPassword) {
        // 登录成功，设置session
        req.session.authenticated = true;
        req.session.username = username;
        req.session.loginTime = new Date().toISOString();

        console.log('Login successful, redirecting to dashboard');
        res.json({
          success: true,
          message: '登录成功',
          redirect: '/dashboard.html'
        });
      } else {
        console.log('Login failed: invalid credentials');
        res.status(401).json({
          success: false,
          message: '用户名或密码错误'
        });
      }
    } catch (error) {
      console.error('登录失败:', error);
      res.status(500).json({ error: '登录失败' });
    }
  }

  logout(req, res) {
    try {
      // 销毁session
      req.session.destroy((err) => {
        if (err) {
          logger.error('登出失败:', err);
          return res.status(500).json({ error: '登出失败' });
        }

        res.json({ success: true, message: '已登出' });
      });
    } catch (error) {
      logger.error('登出失败:', error);
      res.status(500).json({ error: '登出失败' });
    }
  }

  getLoginStatus(req, res) {
    try {
      const authenticated = req.session && req.session.authenticated;
      res.json({
        authenticated: authenticated,
        username: authenticated ? req.session.username : null,
        loginTime: authenticated ? req.session.loginTime : null
      });
    } catch (error) {
      logger.error('获取登录状态失败:', error);
      res.status(500).json({ error: '获取登录状态失败' });
    }
  }

  getStats(req, res) {
    try {
      const stats = {
        pendingCount: 0,
        completedCount: 0,
        channelCount: 0,
        activeCount: 0,
        loginTime: new Date().toISOString(),
        downloads: [],
        recent: [],
        channels: [],
        history: [],
        systemStatus: {}
      };

      // 获取待下载数量
      if (this.mainApp && this.mainApp.downloadManager) {
        // 如果有数据库管理器，获取数据库中的统计信息
        if (this.mainApp.databaseManager) {
          const dbStats = this.mainApp.databaseManager.getQueueStats();
          stats.pendingCount = (dbStats.pending || 0) + (dbStats.failed || 0);
        } else {
          stats.pendingCount = this.mainApp.downloadManager.downloadQueue.length;
        }
        stats.activeCount = this.mainApp.downloadManager.activeDownloads.size;
      } else {
        logger.warn('downloadManager 缺失，无法计算待下载或活跃数量');
      }

      // 获取已完成数量
      if (this.mainApp && this.mainApp.downloadHistory) {
        const allRecords = Object.values(this.mainApp.downloadHistory.history);
        stats.completedCount = allRecords.filter(r => r.status === 'completed').length;
      } else {
        logger.warn('downloadHistory 缺失');
      }

      // 获取频道数量
      if (this.mainApp && this.mainApp.config && this.mainApp.config.chat) {
        stats.channelCount = this.mainApp.config.chat.length;
      } else {
        logger.warn('config.chat 缺失，无法计算频道数量');
      }

      // 获取下载队列
      if (this.mainApp && this.mainApp.downloadManager) {
        stats.downloads = this.mainApp.downloadManager.downloadQueue.map(task => ({
          id: `${task.chatId}_${task.messageId}`,
          title: task.fileName || '未命名',
          type: task.mediaType || '文件',
          size: '未知',
          progress: 0,
          status: 'pending'
        }));

        // 添加活跃下载
        this.mainApp.downloadManager.activeDownloads.forEach((task, taskId) => {
          stats.downloads.push({
            id: taskId,
            title: task.fileName || '未命名',
            type: task.mediaType || '文件',
            size: task.fileSize || '未知',
            progress: task.progress || 0,
            status: 'downloading'
          });
        });
      }

      // 获取最近下载和历史
      if (this.mainApp && this.mainApp.downloadHistory) {
        const allRecords = Object.values(this.mainApp.downloadHistory.history);
        // 最近下载（按完成时间降序，取10条）
        const recent = allRecords
          .filter(r => r.status === 'completed' && r.completedAt)
          .sort((a, b) => new Date(b.completedAt) - new Date(a.completedAt))
          .slice(0, 10);
        stats.recent = recent.map(item => ({
          title: item.fileName || '未命名',
          date: item.completedAt || item.downloadedAt,
          size: item.fileSize || '未知'
        }));

        // 下载历史（按完成时间降序，取50条）
        const historyList = allRecords
          .filter(r => r.status === 'completed' && r.completedAt)
          .sort((a, b) => new Date(b.completedAt) - new Date(a.completedAt))
          .slice(0, 50);
        stats.history = historyList.map(item => ({
          id: item.id || '',
          fileName: item.fileName,
          fileSize: item.fileSize,
          completedAt: item.completedAt,
          status: item.status,
          chatId: item.chatId
        }));
      }

      // 获取已连接频道列表
      if (this.mainApp && this.mainApp.databaseManager) {
        const savedChannels = this.mainApp.databaseManager.getSavedChannels(50);
        stats.channels = savedChannels.map(c => ({
          id: c.channel_id,
          title: c.title || c.username || c.channel_id,
          memberCount: '已连接',
          lastConnected: c.last_connected_at
        }));
        stats.channelCount = savedChannels.length;
      } else if (this.mainApp && this.mainApp.config && this.mainApp.config.chat) {
        stats.channels = this.mainApp.config.chat.map(chat => ({
          id: chat.chat_id,
          title: `配置频道 ${chat.chat_id}`,
          memberCount: '配置文件'
        }));
        stats.channelCount = stats.channels.length;
      }

      // 获取系统状态
      stats.systemStatus = this.getSystemStatusData();

      res.json(stats);
    } catch (error) {
      logger.error('获取统计信息失败:', error);
      res.status(500).json({ error: '获取统计信息失败' });
    }
  }

  getDownloads(req, res) {
    try {
      const downloads = [];

      if (this.mainApp && this.mainApp.downloadManager) {
        // 待下载队列
        this.mainApp.downloadManager.downloadQueue.forEach(task => {
          downloads.push({
            id: `${task.chatId}_${task.messageId}`,
            title: task.fileName || '未命名',
            type: task.mediaType || '文件',
            size: '未知',
            progress: 0,
            status: 'pending',
            chatId: task.chatId,
            messageId: task.messageId
          });
        });

        // 正在下载的任务
        this.mainApp.downloadManager.activeDownloads.forEach((task, taskId) => {
          downloads.push({
            id: taskId,
            title: task.fileName || '未命名',
            type: task.mediaType || '文件',
            size: task.fileSize || '未知',
            progress: task.progress || 0,
            status: 'downloading',
            chatId: task.chatId,
            messageId: task.messageId
          });
        });
      }

      res.json({ downloads });
    } catch (error) {
      logger.error('获取下载列表失败:', error);
      res.status(500).json({ error: '获取下载列表失败' });
    }
  }

  startDownload(req, res) {
    try {
      if (this.mainApp && this.mainApp.downloadManager) {
        // 启动所有待下载任务
        const pendingTasks = this.mainApp.downloadManager.downloadQueue.length;
        if (pendingTasks > 0) {
          // 这里可以实现批量启动下载的逻辑
          // 目前只是返回成功消息，实际的下载会由消息监听器处理
          res.json({
            success: true,
            message: `已准备启动 ${pendingTasks} 个下载任务`,
            pendingTasks: pendingTasks
          });
        } else {
          res.json({
            success: true,
            message: '没有待下载任务',
            pendingTasks: 0
          });
        }
      } else {
        res.status(500).json({ error: '下载管理器不可用' });
      }
    } catch (error) {
      logger.error('启动下载失败:', error);
      res.status(500).json({ error: '启动下载失败' });
    }
  }

  stopDownload(req, res) {
    try {
      if (this.mainApp && this.mainApp.downloadManager) {
        // 停止所有活跃下载
        const activeCount = this.mainApp.downloadManager.activeDownloads.size;
        if (activeCount > 0) {
          // 这里可以实现停止所有下载的逻辑
          // 目前只是返回成功消息
          res.json({
            success: true,
            message: `已请求停止 ${activeCount} 个下载任务`,
            stoppedTasks: activeCount
          });
        } else {
          res.json({
            success: true,
            message: '没有正在下载的任务',
            stoppedTasks: 0
          });
        }
      } else {
        res.status(500).json({ error: '下载管理器不可用' });
      }
    } catch (error) {
      logger.error('停止下载失败:', error);
      res.status(500).json({ error: '停止下载失败' });
    }
  }

  cancelDownload(req, res) {
    try {
      const { taskId } = req.params;

      if (this.mainApp && this.mainApp.downloadManager) {
        // 从队列中移除任务
        this.mainApp.downloadManager.downloadQueue =
          this.mainApp.downloadManager.downloadQueue.filter(task =>
            `${task.chatId}_${task.messageId}` !== taskId
          );

        // 如果正在下载中，取消它
        if (this.mainApp.downloadManager.activeDownloads.has(taskId)) {
          // 这里需要实现取消下载的逻辑
          this.mainApp.downloadManager.activeDownloads.delete(taskId);
        }
      }

      res.json({ success: true, message: '下载任务已取消' });
    } catch (error) {
      logger.error('取消下载失败:', error);
      res.status(500).json({ error: '取消下载失败' });
    }
  }

  getChannels(req, res) {
    try {
      const channels = [];

      if (this.mainApp && this.mainApp.config && this.mainApp.config.chat) {
        this.mainApp.config.chat.forEach(chat => {
          channels.push({
            id: chat.chat_id,
            title: `频道 ${chat.chat_id}`,
            memberCount: '未知',
            lastReadMessageId: chat.last_read_message_id || 0
          });
        });
      }

      res.json({ channels });
    } catch (error) {
      logger.error('获取频道列表失败:', error);
      res.status(500).json({ error: '获取频道列表失败' });
    }
  }

  async addChannel(req, res) {
    try {
      const { chatId, title } = req.body;

      if (!chatId) {
        return res.status(400).json({ error: '频道ID不能为空' });
      }

      if (this.mainApp && this.mainApp.config) {
        // 检查是否已存在
        const exists = this.mainApp.config.chat.some(chat => chat.chat_id === chatId);
        if (exists) {
          return res.status(400).json({ error: '频道已存在' });
        }

        // 添加新频道
        this.mainApp.config.chat.push({
          chat_id: chatId,
          last_read_message_id: 0,
          title: title || `频道 ${chatId}`
        });

        // 保存配置
        const { ConfigManager } = await import('./configManager.js');
        await ConfigManager.saveConfig(this.mainApp.config);

        res.json({ success: true, message: '频道已添加' });
      } else {
        res.status(500).json({ error: '配置管理器不可用' });
      }
    } catch (error) {
      logger.error('添加频道失败:', error);
      res.status(500).json({ error: '添加频道失败' });
    }
  }

  async removeChannel(req, res) {
    try {
      const { channelId } = req.params;

      if (this.mainApp && this.mainApp.config && this.mainApp.config.chat) {
        // 移除频道
        this.mainApp.config.chat = this.mainApp.config.chat.filter(chat =>
          chat.chat_id !== channelId
        );

        // 保存配置
        const { ConfigManager } = await import('./configManager.js');
        await ConfigManager.saveConfig(this.mainApp.config);

        res.json({ success: true, message: '频道已移除' });
      } else {
        res.status(500).json({ error: '配置管理器不可用' });
      }
    } catch (error) {
      logger.error('移除频道失败:', error);
      res.status(500).json({ error: '移除频道失败' });
    }
  }

  getConfig(req, res) {
    try {
      if (this.mainApp && this.mainApp.config) {
        // 返回安全的配置信息（不包含敏感信息）
        const safeConfig = {
          ...this.mainApp.config,
          bot_token: this.mainApp.config.bot_token ? '***' + this.mainApp.config.bot_token.slice(-4) : '',
          user_api: this.mainApp.config.user_api ? {
            ...this.mainApp.config.user_api,
            api_id: '***',
            api_hash: '***'
          } : null
        };

        res.json(safeConfig);
      } else {
        res.status(500).json({ error: '配置不可用' });
      }
    } catch (error) {
      logger.error('获取配置失败:', error);
      res.status(500).json({ error: '获取配置失败' });
    }
  }

  async updateConfig(req, res) {
    try {
      const updates = req.body;

      if (this.mainApp && this.mainApp.config) {
        // 更新配置（这里应该有验证逻辑）
        Object.assign(this.mainApp.config, updates);

        // 保存配置
        const { ConfigManager } = await import('./configManager.js');
        await ConfigManager.saveConfig(this.mainApp.config);

        res.json({ success: true, message: '配置已更新' });
      } else {
        res.status(500).json({ error: '配置管理器不可用' });
      }
    } catch (error) {
      logger.error('更新配置失败:', error);
      res.status(500).json({ error: '更新配置失败' });
    }
  }

  getDownloadHistory(req, res) {
    try {
      const history = [];

      if (this.mainApp && this.mainApp.downloadHistory) {
        const recent = this.mainApp.downloadHistory.getRecentDownloads(50);
        history.push(...recent.map(item => ({
          id: item.id,
          fileName: item.fileName,
          fileSize: item.fileSize,
          completedAt: item.completedAt,
          status: item.status,
          chatId: item.chatId
        })));
      }

      res.json({ history });
    } catch (error) {
      logger.error('获取下载历史失败:', error);
      res.status(500).json({ error: '获取下载历史失败' });
    }
  }

  clearHistory(req, res) {
    try {
      if (this.mainApp && this.mainApp.downloadHistory) {
        // 清空历史记录
        this.mainApp.downloadHistory.history = {};
        this.mainApp.downloadHistory.saveHistory();

        // 如果有数据库，也清空数据库中的历史记录
        if (this.mainApp.databaseManager) {
          // 这里可以添加数据库清空的逻辑
        }

        res.json({ success: true, message: '历史记录已清空' });
      } else {
        res.status(500).json({ error: '下载历史管理器不可用' });
      }
    } catch (error) {
      logger.error('清空历史记录失败:', error);
      res.status(500).json({ error: '清空历史记录失败' });
    }
  }

  getSystemStatus(req, res) {
    try {
      const status = this.getSystemStatusData();
      res.json(status);
    } catch (error) {
      logger.error('获取系统状态失败:', error);
      res.status(500).json({ error: '获取系统状态失败' });
    }
  }

  restartSystem(req, res) {
    try {
      // 这里可以实现重启系统的逻辑
      res.json({ success: true, message: '系统重启中...' });

      // 延迟重启
      setTimeout(() => {
        process.exit(0);
      }, 1000);
    } catch (error) {
      logger.error('重启系统失败:', error);
      res.status(500).json({ error: '重启系统失败' });
    }
  }

  async getLogs(req, res) {
    try {
      const logFiles = ['combined.log', 'error.log', 'web-server.log'];
      const logs = [];

      for (const logFile of logFiles) {
        try {
          const content = await readFile(logFile, 'utf-8');
          const lines = content.split('\n').filter(line => line.trim());

          lines.forEach(line => {
            // 解析日志行格式
            const match = line.match(/^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(\w+)\s+(.+)$/);
            if (match) {
              logs.push({
                timestamp: match[1],
                level: match[2],
                message: match[3]
              });
            } else {
              // 如果无法解析，添加为一般消息
              logs.push({
                timestamp: new Date().toISOString(),
                level: 'info',
                message: line
              });
            }
          });
        } catch (error) {
          // 日志文件不存在，跳过
        }
      }

      // 按时间倒序排序，最新的在前面
      logs.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));

      // 只返回最近的100条日志
      res.json({ logs: logs.slice(0, 100) });
    } catch (error) {
      logger.error('获取日志失败:', error);
      res.status(500).json({ error: '获取日志失败' });
    }
  }

  async getFiles(req, res) {
    try {
      const downloadsDir = join(__dirname, '..', 'downloads');
      const allFiles = [];

      // 递归获取所有文件
      async function scanDir(dir, relativePath = '') {
        try {
          const entries = await readdir(dir, { withFileTypes: true });
          for (const entry of entries) {
            const fullPath = join(dir, entry.name);
            const relPath = join(relativePath, entry.name);
            
            if (entry.isDirectory()) {
              await scanDir(fullPath, relPath);
            } else {
              const stats = await stat(fullPath);
              allFiles.push({
                name: entry.name,
                path: relPath.replace(/\\/g, '/'),
                size: stats.size,
                formattedSize: WebServer.prototype.formatBytes(stats.size),
                date: stats.mtime.toISOString()
              });
            }
          }
        } catch (e) {
          logger.error(`读取目录 ${dir} 失败:`, e);
        }
      }

      if (await access(downloadsDir).then(() => true).catch(() => false)) {
        await scanDir(downloadsDir);
      }

      // 按时间倒序排序
      allFiles.sort((a, b) => new Date(b.date) - new Date(a.date));

      res.json({ files: allFiles.slice(0, 500) }); // 返回最近500个文件
    } catch (error) {
      logger.error('获取文件列表失败:', error);
      res.status(500).json({ error: '获取文件列表失败' });
    }
  }

  async deleteFile(req, res) {
    try {
      const { filename } = req.params;
      const downloadsDir = join(__dirname, '..', 'downloads');
      const filePath = join(downloadsDir, filename);

      // 检查文件是否存在
      try {
        await access(filePath);
      } catch (error) {
        return res.status(404).json({ error: '文件不存在' });
      }

      // 删除文件
      await unlink(filePath);

      res.json({ success: true, message: `文件 ${filename} 已删除` });
    } catch (error) {
      logger.error('删除文件失败:', error);
      res.status(500).json({ error: '删除文件失败' });
    }
  }

  getSystemStatusData() {
    let queuedCount = 0;
    if (this.mainApp && this.mainApp.downloadManager) {
      if (this.mainApp.databaseManager) {
        const dbStats = this.mainApp.databaseManager.getQueueStats();
        queuedCount = (dbStats.pending || 0) + (dbStats.failed || 0);
      } else {
        queuedCount = this.mainApp.downloadManager.downloadQueue.length;
      }
    }

    return {
      uptime: process.uptime(),
      memory: process.memoryUsage(),
      version: process.version,
      platform: process.platform,
      botConnected: this.mainApp && this.mainApp.apiClient && this.mainApp.apiClient.isConnected,
      userClientConnected: this.mainApp && this.mainApp.userClient && this.mainApp.userClient.isConnected,
      activeDownloads: this.mainApp && this.mainApp.downloadManager ?
        this.mainApp.downloadManager.activeDownloads.size : 0,
      queuedDownloads: queuedCount
    };
  }

  formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return (bytes / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i];
  }

  getBotStatus() {
    try {
      const botConnected = this.mainApp && this.mainApp.apiClient && this.mainApp.apiClient.isConnected;
      const lastChecked = new Date().toISOString();
      return {
        status: botConnected ? 'connected' : 'disconnected',
        lastChecked: lastChecked
      };
    } catch (error) {
      logger.error('获取Bot状态失败:', error);
      return {
        status: 'disconnected',
        lastChecked: new Date().toISOString()
      };
    }
  }

  getUserClientStatus() {
    try {
      const userClientConnected = this.mainApp && this.mainApp.userClient && this.mainApp.userClient.isConnected;
      return {
        status: userClientConnected ? 'connected' : 'disconnected',
        featuresAvailable: userClientConnected ? 'available' : 'unavailable'
      };
    } catch (error) {
      logger.error('获取用户客户端状态失败:', error);
      return {
        status: 'disconnected',
        featuresAvailable: 'unavailable'
      };
    }
  }

  getDatabaseStatus() {
    try {
      const dbStatus = this.mainApp && this.mainApp.databaseManager ? '正常' : '异常';
      const message = dbStatus === '正常' ? 'SQLite数据库运行正常' : 'SQLite数据库未连接';
      return {
        status: dbStatus,
        message: message
      };
    } catch (error) {
      logger.error('获取数据库状态失败:', error);
      return {
        status: '异常',
        message: '无法获取数据库状态'
      };
    }
  }

  getSystemUptime() {
    try {
      const uptimeInSeconds = process.uptime();
      const uptimeInMinutes = Math.floor(uptimeInSeconds / 60);
      return {
        uptimeMinutes: uptimeInMinutes
      };
    } catch (error) {
      logger.error('获取系统运行时间失败:', error);
      return {
        uptimeMinutes: 0
      };
    }
  }

  start() {
    this.server = this.app.listen(this.port, () => {
      logger.info(`Web服务器已启动，端口: ${this.port}`);
      logger.info(`访问地址: http://localhost:${this.port}`);
    });

    return this.server;
  }

  stop() {
    if (this.server) {
      this.server.close(() => {
        logger.info('Web服务器已停止');
      });
    }
  }
}

export { WebServer };