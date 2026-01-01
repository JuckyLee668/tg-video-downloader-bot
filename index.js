const TelegramBot = require('node-telegram-bot-api');
const fs = require('fs');
const axios = require('axios');
const path = require('path');
const dotenv = require('dotenv');
dotenv.config();
const token = process.env.BOT_TOKEN;
const DOWNLOAD_DIR = process.env.DOWNLOAD_DIR || './downloads/';
const BOT_API_HOST = process.env.BOT_API_HOST || 'http://127.0.0.1:8081';

// 配置基础API地址为你的本地服务器
const bot = new TelegramBot(token, {
    polling: true,
    baseApiUrl: BOT_API_HOST // 指向你的本地API服务器
});

// 确保下载目录存在
if (!fs.existsSync(DOWNLOAD_DIR)) {
    fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });
}

// 监听所有消息
bot.on('message', async (msg) => {
    const video = msg.video || (msg.document?.mime_type?.startsWith('video/') ? msg.document : null);
    if (!video) return;

    const chatId = msg.chat.id;
    const fileId = video.file_id;
    const fileName = video.file_name || `video_${Date.now()}.mp4`;

    try {
        await bot.sendMessage(chatId, `开始处理视频: ${fileName}...`);

        // 获取文件元信息（含本地路径）
        const file = await bot.getFile(fileId);
        const srcPath = file.file_path; // 例如: /root/.../videos/file_0

        // 确保目标目录存在
        const destDir = './downloads';
        await fs.promises.mkdir(destDir, { recursive: true });
        const destPath = `${destDir}/${fileName}`;

        // 检查源文件是否存在
        if (!fs.existsSync(srcPath)) {
            throw new Error(`源文件不存在: ${srcPath}`);
        }

        // 复制文件
        await fs.promises.copyFile(srcPath, destPath);

        console.log(`✅ 保存成功: ${destPath}`);
        await bot.sendMessage(chatId, `✅ 视频已保存: ${fileName}`);
    } catch (error) {
        console.error('❌ 处理失败:', error.message);
        await bot.sendMessage(chatId, `❌ 失败: ${error.message}`);
    }
});

// 处理 /start 命令
bot.onText(/\/start/, (msg) => {
    const chatId = msg.chat.id;
    bot.sendMessage(chatId, '欢迎使用视频下载Bot！\n\n只需发送视频文件给我，我会尝试下载它。\n注意：需要本地Bot API服务器支持大文件下载。');
});

console.log('🤖 Bot已启动，正在监听消息...');