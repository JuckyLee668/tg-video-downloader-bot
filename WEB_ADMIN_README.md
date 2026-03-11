# Telegram 媒体下载器 - 后台管理页面

## 功能概述

本项目为Telegram媒体下载机器人提供了一个完整的后台管理界面，可以通过Web浏览器管理所有功能。

## 主要功能

### 📊 统计面板
- 待下载任务数量
- 已完成下载数量
- 已连接频道数量
- 活跃下载任务数量

### ⚡ 快速操作
- 📺 频道管理：添加/移除监控的频道
- 📥 开始下载：手动触发下载任务
- 🔄 刷新状态：实时更新统计信息
- ⚙️ 系统设置：配置下载参数
- 📋 查看日志：系统运行日志
- 📁 文件管理：下载文件的管理

### 🖥️ 系统状态监控
- 🤖 Bot连接状态
- 👤 用户客户端状态
- 💾 数据库状态
- ⏱️ 系统运行时间

### 📚 下载历史
- 查看所有下载记录
- 清空历史记录
- 导出历史数据

### 📁 文件管理
- 浏览下载的文件
- 下载文件到本地
- 删除不需要的文件

### 📋 系统日志
- 实时查看系统日志
- 按时间排序
- 支持日志筛选

## 安装和运行

1. 安装依赖：
```bash
npm install
```

2. 配置环境变量（.env文件）：
```
BOT_TOKEN=your_bot_token
BOT_API_HOST=your_bot_api_host
PUBLIC_FILE_BASE_URL=your_file_base_url
TG_BASE_DIR=your_tg_base_dir
WEB_PORT=3000
```

3. 启动应用程序：
```bash
npm start
```

4. 访问后台管理页面：
打开浏览器访问 `http://localhost:3000`

## API 接口

### 统计信息
- `GET /api/stats` - 获取系统统计信息

### 下载管理
- `GET /api/downloads` - 获取下载队列
- `POST /api/downloads/start` - 启动下载
- `POST /api/downloads/stop` - 停止下载
- `DELETE /api/downloads/:taskId` - 取消下载任务

### 频道管理
- `GET /api/channels` - 获取频道列表
- `POST /api/channels` - 添加频道
- `DELETE /api/channels/:channelId` - 移除频道

### 系统管理
- `GET /api/system/status` - 获取系统状态
- `POST /api/system/restart` - 重启系统

### 文件管理
- `GET /api/files` - 获取下载文件列表
- `DELETE /api/files/:filename` - 删除文件

### 日志管理
- `GET /api/logs` - 获取系统日志

## 安全注意事项

- 默认情况下，Web界面没有认证保护
- 在生产环境中，请添加适当的认证机制
- 敏感配置信息已过滤，不会在界面中显示

## 技术栈

- **后端**: Node.js + Express
- **前端**: HTML5 + CSS3 + JavaScript
- **数据库**: SQLite
- **日志**: Winston

## 开发说明

项目结构：
```
src/
├── index.js              # 主入口文件
├── webServer.js          # Web服务器
├── botHandler.js         # Bot处理器
├── downloadManager.js    # 下载管理器
├── configManager.js      # 配置管理器
├── databaseManager.js    # 数据库管理器
└── ...

public/
├── dashboard.html        # 后台管理页面
└── ...

downloads/                # 下载文件目录
data/                     # 数据文件目录
```

## 许可证

MIT License