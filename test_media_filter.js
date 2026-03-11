// 测试频道消息媒体类型检测和过滤逻辑
import { ConfigManager } from './src/configManager.js';
import { createLogger, format, transports } from 'winston';

const logger = createLogger({
  level: 'info',
  format: format.combine(
    format.timestamp({ format: 'YYYY-MM-DD HH:mm:ss' }),
    format.errors({ stack: true }),
    format.simple()
  ),
  transports: [
    new transports.Console({
      format: format.combine(
        format.colorize(),
        format.simple()
      )
    })
  ]
});

class TestMediaDownloader {
  constructor() {
    this.config = ConfigManager.loadConfig();
  }

  // 复制 getMediaTypeFromMessage 方法
  getMediaTypeFromMessage(msg) {
    if (msg.photo) return 'photo';
    if (msg.video) return 'video';
    if (msg.audio) return 'audio';
    if (msg.voice) return 'voice';
    if (msg.document) {
      const mimeType = msg.document.mime_type || '';
      if (mimeType.startsWith('video/')) return 'video';
      if (mimeType.startsWith('audio/')) return 'audio';
      if (mimeType === 'image/gif' || msg.document.thumb) {
        // 检查是否是动画
        if (msg.document.file_name && msg.document.file_name.endsWith('.gif')) {
          return 'animation';
        }
      }
      return 'document';
    }
    if (msg.animation) return 'animation';
    return null;
  }

  // 复制 shouldDownloadFileFromMessage 方法
  shouldDownloadFileFromMessage(msg, mediaType) {
    const fileFormats = this.config.file_formats[mediaType];
    if (!fileFormats || fileFormats.includes('all')) {
      return true;
    }

    // 对于 video 类型，检查 msg.video 或 msg.document
    if (mediaType === 'video') {
      if (msg.video) {
        // 如果配置了格式过滤，检查视频格式
        const fileName = msg.video.file_name || '';
        const mimeType = msg.video.mime_type || '';
        const extension = fileName.split('.').pop() || '';

        // 如果没有文件名和 MIME 类型信息，默认允许下载（可能是转发的消息）
        if (!fileName && !mimeType) {
          logger.info(`视频消息缺少格式信息，默认允许下载 - file_id: ${msg.video.file_id}`);
          return true;
        }

        for (const format of fileFormats) {
          if (mimeType.includes(format) || extension === format) {
            return true;
          }
        }
        // 如果没有匹配的格式，返回 false
        logger.warn(`视频格式不匹配 - file_name: ${fileName}, mime_type: ${mimeType}, 配置格式: ${JSON.stringify(fileFormats)}`);
        return false;
      }
      // 如果 video 是通过 document 发送的
      if (msg.document) {
        const mimeType = msg.document.mime_type || '';
        const fileName = msg.document.file_name || '';
        const extension = fileName.split('.').pop() || '';

        // 如果没有文件名和 MIME 类型信息，默认允许下载
        if (!fileName && !mimeType) {
          logger.info(`视频文档缺少格式信息，默认允许下载 - file_id: ${msg.document.file_id}`);
          return true;
        }

        for (const format of fileFormats) {
          if (mimeType.includes(format) || extension === format) {
            return true;
          }
        }
        logger.warn(`视频文档格式不匹配 - file_name: ${fileName}, mime_type: ${mimeType}, 配置格式: ${JSON.stringify(fileFormats)}`);
        return false;
      }
    }

    // 对于其他类型，检查 document
    if (msg.document) {
      const mimeType = msg.document.mime_type || '';
      const fileName = msg.document.file_name || '';
      const extension = fileName.split('.').pop() || '';

      for (const format of fileFormats) {
        if (mimeType.includes(format) || extension === format) {
          return true;
        }
      }
    }

    // 对于 photo、voice、audio 等，如果没有格式限制或格式匹配，允许下载
    if (mediaType === 'photo' || mediaType === 'voice' || mediaType === 'audio') {
      // 这些类型通常不需要格式过滤，或者已经在 media_types 中过滤了
      return true;
    }

    return false;
  }

  // 测试不同类型的消息
  testMessage(msg, description) {
    console.log(`\n=== 测试: ${description} ===`);
    console.log('消息对象:', JSON.stringify(msg, null, 2));

    const mediaType = this.getMediaTypeFromMessage(msg);
    console.log('检测到的媒体类型:', mediaType);

    if (mediaType && this.config.media_types.includes(mediaType)) {
      const shouldDownload = this.shouldDownloadFileFromMessage(msg, mediaType);
      console.log('是否应该下载:', shouldDownload);
      if (!shouldDownload) {
        console.log('原因: 格式过滤失败');
      }
    } else {
      console.log('跳过: 媒体类型不在配置中或未检测到媒体');
    }
  }
}

// 测试用例
const tester = new TestMediaDownloader();

// 1. 照片消息（应该下载）
tester.testMessage({
  photo: [{ file_id: 'photo123', width: 100, height: 100 }]
}, '照片消息');

// 2. 视频消息 - 带文件名和MIME类型（应该下载，因为是mp4）
tester.testMessage({
  video: {
    file_id: 'video123',
    file_name: 'test.mp4',
    mime_type: 'video/mp4',
    width: 1920,
    height: 1080
  }
}, 'MP4视频消息');

// 3. 视频消息 - 不带文件名和MIME类型（应该下载，默认允许）
tester.testMessage({
  video: {
    file_id: 'video456',
    width: 1920,
    height: 1080
  }
}, '无格式信息的视频消息');

// 4. 视频消息 - 其他格式（不应该下载）
tester.testMessage({
  video: {
    file_id: 'video789',
    file_name: 'test.avi',
    mime_type: 'video/avi',
    width: 1920,
    height: 1080
  }
}, 'AVI视频消息（不匹配格式）');

// 5. 通过document发送的视频（应该下载）
tester.testMessage({
  document: {
    file_id: 'doc123',
    file_name: 'movie.mp4',
    mime_type: 'video/mp4'
  }
}, '通过document发送的MP4视频');

// 6. 通过document发送的视频 - 其他格式（不应该下载）
tester.testMessage({
  document: {
    file_id: 'doc456',
    file_name: 'movie.mkv',
    mime_type: 'video/x-matroska'
  }
}, '通过document发送的MKV视频（不匹配格式）');

// 7. 音频消息（应该下载）
tester.testMessage({
  audio: {
    file_id: 'audio123',
    title: 'Test Song',
    performer: 'Test Artist'
  }
}, '音频消息');

// 8. 文档消息 - PDF（应该下载）
tester.testMessage({
  document: {
    file_id: 'doc789',
    file_name: 'document.pdf',
    mime_type: 'application/pdf'
  }
}, 'PDF文档');

// 9. 文档消息 - 其他格式（不应该下载）
tester.testMessage({
  document: {
    file_id: 'doc999',
    file_name: 'document.docx',
    mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  }
}, 'Word文档（不匹配格式）');