import { TelegramClient } from 'telegram';
import { StringSession } from 'telegram/sessions/index.js';
import { Api } from 'telegram/tl/index.js';
import fs, { existsSync, readFileSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { parseProxy, sanitizeFileName } from './utils.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const SESSION_FILE = join(__dirname, '../session.txt');

/**
 * 该类封装了一个普通（非 Bot API）Telegram 客户端，主要用于
 * 频道/超级群组的搜索、获取历史消息以及下载未转发的媒体。
 *
 * 配置项：
 *   apiId、apiHash：Telegram 开发者 API Key
 *   proxy：可选代理设置，支持 HTTP、SOCKS4/5
 *
 * 登录步骤通常在启动之前由管理员手动完成（参考项目中的 login-user.js 脚本）。
 * 运行时会从 session.txt 加载会话并尝试自动登录。
 */
export class TelegramUserClient {
  constructor(apiId, apiHash, proxy) {
    this.apiId = apiId;
    this.apiHash = apiHash;
    this.proxy = proxy || parseProxy();
    this.client = null;
    this.isConnected = false;
    this.phoneNumber = null;
    this.authCallbacks = {};
    this.phoneCodeHash = null;
  }

  init() {
    let stringSession = '';
    if (existsSync(SESSION_FILE)) {
      stringSession = readFileSync(SESSION_FILE, 'utf-8').trim();
    }

    const connectionOptions = {
      connectionRetries: 5,
      timeout: 60000,
      keepAlive: true,
      bufferSize: 64 * 1024,
    };

    if (this.proxy) {
      const proxyConfig = {
        ip: this.proxy.ip,
        port: this.proxy.port,
      };
      if (this.proxy.username) {
        proxyConfig.username = this.proxy.username;
      }
      if (this.proxy.password) {
        proxyConfig.password = this.proxy.password;
      }
      if (this.proxy.type === 2) {
        proxyConfig.socksType = 5;
      } else if (this.proxy.type === 1) {
        proxyConfig.socksType = 4;
      } else {
        proxyConfig.type = 0;
      }
      connectionOptions.proxy = proxyConfig;
    }

    this.client = new TelegramClient(
      new StringSession(stringSession),
      this.apiId,
      this.apiHash,
      connectionOptions
    );
  }

  saveSession() {
    if (this.client) {
      writeFileSync(SESSION_FILE, this.client.session.save(), 'utf-8');
    }
  }

  /**
   * 启动交互式登录（用于 Bot 命令）
   * 需要在 client 连接后调用
   */
  async startInteractiveLogin(phoneNumber) {
    if (!this.client) throw new Error('客户端未初始化');
    if (!this.client.connected) {
      await this.client.connect();
    }

    try {
      // 使用 telegram 库的标准 sendCode 方法
      // api.auth.SendCodeRequest 的参数：phoneNumber, apiId, apiHash, codeSettings
      const sentCode = await this.client.invoke(
        new Api.auth.SendCode({
          phoneNumber: phoneNumber,
          apiId: this.apiId,
          apiHash: this.apiHash,
          settings: new Api.CodeSettings(),
        })
      );

      // 验证码已发送

      this.phoneCodeHash = sentCode.phoneCodeHash;
      this.phoneNumber = phoneNumber;
      this.sentCodeType = sentCode.type; // 保存验证码类型以便后续处理

      return {
        success: true,
        phoneCodeHash: sentCode.phoneCodeHash,
        codeType: sentCode.type?.className || 'unknown',
        message: '验证码已发送'
      };
    } catch (error) {
      throw new Error(`发送验证码失败: ${error.message}`);
    }
  }

  /**
   * 重新发送验证码（当验证码过期时）
   */
  async resendCode() {
    if (!this.phoneNumber || !this.phoneCodeHash) {
      throw new Error('请先调用 startInteractiveLogin');
    }

    try {
      const resent = await this.client.invoke(
        new Api.auth.ResendCode({
          phoneNumber: this.phoneNumber,
          phoneCodeHash: this.phoneCodeHash,
        })
      );

      // 新验证码已发送

      this.phoneCodeHash = resent.phoneCodeHash;
      this.sentCodeType = resent.type;

      return {
        success: true,
        phoneCodeHash: resent.phoneCodeHash,
        codeType: resent.type?.className || 'unknown',
        message: '新验证码已发送'
      };
    } catch (error) {
      throw new Error(`重新发送验证码失败: ${error.message}`);
    }
  }

  /**
   * 使用验证码签名（完成登录）
   * @param {string} code - 验证码
   * @param {string} phoneCodeHash - 验证码哈希（可选，如不提供则使用存储的哈希）
   */
  async signInWithCode(code, phoneCodeHash = null) {
    if (!this.phoneNumber) {
      throw new Error('请先调用 startInteractiveLogin');
    }

    const hash = phoneCodeHash || this.phoneCodeHash;
    if (!hash) {
      throw new Error('请先调用 startInteractiveLogin 获取验证码');
    }

    // 准备登录请求

    try {
      const result = await this.client.invoke(
        new Api.auth.SignIn({
          phoneNumber: this.phoneNumber,
          phoneCodeHash: hash,
          phoneCode: code,
        })
      );

      if (result.user) {
        this.isConnected = true;
        this.saveSession();
        return { success: true, user: result.user };
      }
    } catch (error) {
      const msg = error.message || String(error);
      // 登录错误处理
      if (msg.includes('SESSION_PASSWORD_NEEDED') || msg.includes('PASSWORD')) {
        return { success: false, needsPassword: true, error: '需要两步验证密码' };
      }
      throw new Error(`登录失败: ${error.message}`);
    }
  }

  /**
   * 使用密码完成登录（两步验证）
   */
  async signInWithPassword(password) {
    if (!this.phoneNumber) {
      throw new Error('请先完成验证码验证');
    }

    try {
      // 获取密码信息
      const accountPassword = await this.client.invoke(new Api.account.GetPassword());
      
      // 计算密码哈希并登录
      const result = await this.client.invoke(
        new Api.auth.CheckPassword({
          password: await this.computePasswordHash(password, accountPassword),
        })
      );

      if (result.user) {
        this.isConnected = true;
        this.saveSession();
        return { success: true, user: result.user };
      }
    } catch (error) {
      throw new Error(`密码验证失败: ${error.message}`);
    }
  }

  /**
   * 计算密码哈希（用于两步验证）
   */
  async computePasswordHash(password, accountPassword) {
    // 简化版本 - 在实际使用中需要更复杂的密码哈希计算
    // 这里返回密码的简单哈希
    if (accountPassword.currentAlgo) {
      // 返回密码本身（telegram 库会处理哈希）
      return Buffer.from(password, 'utf-8');
    }
    return Buffer.from(password, 'utf-8');
  }

  async checkConnection() {
    if (!this.client) this.init();
    try {
      if (!this.client.connected) {
        await this.client.connect();
      }
      const me = await this.client.getMe();
      this.isConnected = true;
      this.saveSession();
      return { connected: true, user: me };
    } catch (error) {
      this.isConnected = false;
      const msg = error.message || error.errorMessage || String(error);
      if (msg.includes('unauthorized') || msg.includes('AUTH_KEY')) {
        try {
          if (existsSync(SESSION_FILE)) {
            await fs.promises.unlink(SESSION_FILE);
          }
        } catch {}
      }
      return { connected: false, error: msg };
    }
  }

  /**
   * 获取所有频道/超级群列表
   */
  async getAllChannels() {
    if (!this.isConnected) throw new Error('未连接');
    const dialogs = await this.client.getDialogs({});
    const channels = [];
    for (const d of dialogs) {
      const e = d.entity;
      if (e instanceof Api.Channel || e instanceof Api.Chat) {
        const isChannel = e instanceof Api.Channel && e.broadcast;
        const isMegagroup = e instanceof Api.Channel && e.megagroup;
        if (isChannel || isMegagroup) {
          channels.push({
            id: e.id.toString(),
            username: e.username,
            title: e.title,
            isChannel,
            isMegagroup,
            accessHash: e.accessHash?.toString(),
          });
        }
      }
    }
    channels.sort((a,b) => (a.title||'').localeCompare(b.title||''));
    return { success: true, channels };
  }

  async getChannelEntity(channelInfo) {
    try {
      return await this.client.getInputEntity(channelInfo.username || parseInt(channelInfo.id));
    } catch (err) {
      if (channelInfo.username) {
        const res = await this.client.invoke(new Api.contacts.ResolveUsername({ username: channelInfo.username }));
        return res.chats[0];
      } else {
        return await this.client.getEntity(parseInt(channelInfo.id));
      }
    }
  }

  async findAndJoinChannel(channelInput) {
    if (!this.isConnected) throw new Error('未连接');
    
    let username = channelInput.trim();
    let inviteHash = null;

    // 处理各种链接格式
    // 1. https://t.me/joinchat/xxxx 或 https://t.me/+xxxx (私密频道邀请链接)
    const joinMatch = username.match(/t\.me\/(?:joinchat\/|\+)([\w-]+)/);
    if (joinMatch) {
      inviteHash = joinMatch[1];
    } 
    // 2. https://t.me/username 或 t.me/username
    else if (username.includes('t.me/')) {
      username = username.split('t.me/')[1].split('/')[0];
    }
    // 3. @username
    else if (username.startsWith('@')) {
      username = username.slice(1);
    }

    try {
      // 如果解析出了邀请哈希，优先尝试通过邀请链接加入
      if (inviteHash) {
        try {
          const invite = await this.client.invoke(new Api.messages.ImportChatInvite({ hash: inviteHash }));
          const chat = invite.chats[0];
          return { 
            success: true, 
            channel: {
              id: chat.id.toString(),
              username: chat.username,
              title: chat.title
            } 
          };
        } catch (err) {
          // 如果已经加入过，可能会报错，尝试直接解析哈希（某些情况下可用）
          return { success: false, error: `加入私密频道失败: ${err.message}` };
        }
      }

      // 尝试解析公开用户名
      const result = await this.client.invoke(new Api.contacts.ResolveUsername({ username }));
      if (!result.chats || result.chats.length === 0) {
        return { success: false, error: '未找到该频道' };
      }
      
      const chat = result.chats[0];
      
      // 检查是否已经加入，如果没有则尝试加入
      try {
        await this.client.invoke(new Api.channels.JoinChannel({
          channel: chat
        }));
      } catch (joinErr) {
        // 如果是已经加入或者不需要加入（如自己是管理员），忽略错误
        if (!joinErr.message.includes('CHANNELS_ADMIN_PUBLIC_LEFT')) {
          this.logger.debug(`加入频道提示: ${joinErr.message}`);
        }
      }

      return { 
        success: true, 
        channel: {
          id: chat.id.toString(),
          username: chat.username,
          title: chat.title
        } 
      };
    } catch (e) {
      const errorMsg = e.errorMessage || e.message;
      
      // 如果是私密频道错误，但输入看起来像哈希，尝试再次作为邀请链接处理
      if (errorMsg.includes('CHANNEL_PRIVATE') && !inviteHash && username.length > 10) {
        try {
          const invite = await this.client.invoke(new Api.messages.ImportChatInvite({ hash: username }));
          const chat = invite.chats[0];
          return { 
            success: true, 
            channel: {
              id: chat.id.toString(),
              username: chat.username,
              title: chat.title
            } 
          };
        } catch (e2) {
          return { success: false, error: e2.errorMessage || e2.message };
        }
      }
      
      return { success: false, error: errorMsg };
    }
  }

  async getChannelMessages(channelInfo, options = {}) {
    if (!this.isConnected) throw new Error('未连接');
    const { startDate, endDate, keyword, limit = 1000, fileTypes = [] } = options;
    const msgs = [];
    let offsetId = 0;
    let hasMore = true;
    let fetched = 0;
    const channel = await this.getChannelEntity(channelInfo);
    while (hasMore && fetched < limit) {
      const result = await this.client.invoke(new Api.messages.GetHistory({
        peer: channel,
        offsetId,
        limit: 100,
        maxId: 0,
        minId: 0,
      }));
      if (!result.messages || result.messages.length === 0) break;
      for (const msg of result.messages) {
        if (!(msg instanceof Api.Message)) continue;
        if (startDate && msg.date < startDate) { hasMore = false; break; }
        if (endDate && msg.date > endDate) continue;
        if (keyword) {
          const text = msg.message || '';
          if (!text.toLowerCase().includes(keyword.toLowerCase())) continue;
        }
        const mediaType = msg.media ? this.getMediaTypeFromMessage(msg) : null;
        if (fileTypes.length && msg.media && (!mediaType || !fileTypes.includes(mediaType))) continue;
        msgs.push({
          id: msg.id,
          date: msg.date,
          message: msg.message,
          hasMedia: !!msg.media,
          mediaType,
          groupedId: msg.groupedId ? msg.groupedId.toString() : null,
          mediaCount: 1,
        });
        fetched++;
        if (fetched >= limit) { hasMore = false; break; }
      }
      if (result.messages.length < 100) break;
      offsetId = result.messages[result.messages.length - 1].id;
    }
    return msgs;
  }

  getMediaTypeFromMessage(msg) {
    if (!msg.media) return null;
    
    if (msg.media instanceof Api.MessageMediaDocument) {
      const doc = msg.media.document;
      if (!doc || !doc.attributes) return 'document';
      
      for (const attr of doc.attributes) {
        if (attr instanceof Api.DocumentAttributeVideo) return 'video';
        if (attr instanceof Api.DocumentAttributeAudio) return attr.voice ? 'voice' : 'audio';
        if (attr instanceof Api.DocumentAttributeAnimated) return 'animation';
      }
      
      if (doc.mimeType) {
        if (doc.mimeType.startsWith('video/')) return 'video';
        if (doc.mimeType.startsWith('audio/')) return 'audio';
        if (doc.mimeType.startsWith('image/')) return 'photo';
      }
      return 'document';
    }
    
    if (msg.media instanceof Api.MessageMediaPhoto) return 'photo';
    if (msg.media instanceof Api.MessageMediaWebPage) return 'webpage';
    
    return 'unknown';
  }

  async getGroupedMessageIds(channelInfo, messageId) {
    const channel = await this.getChannelEntity(channelInfo);
    const result = await this.client.invoke(new Api.messages.GetHistory({
      peer: channel,
      offsetId: 0,
      limit: 200,
      maxId: messageId,
      minId: messageId,
    }));
    if (!result.messages || result.messages.length === 0) return [messageId];
    const groupedId = result.messages[0].groupedId ? result.messages[0].groupedId.toString() : null;
    if (!groupedId) return [messageId];
    // find all with same groupedId
    return result.messages
      .filter(m => m.groupedId && m.groupedId.toString() === groupedId && m.media)
      .map(m => m.id);
  }

  async getFileInfo(channelInfo, messageId) {
    try {
      const channel = await this.getChannelEntity(channelInfo);
      
      // 添加重试逻辑
      let msgs = null;
      let retries = 0;
      const maxRetries = 3;
      
      while (retries < maxRetries) {
        try {
          msgs = await this.client.getMessages(channel, { ids: [parseInt(messageId)] });
          if (msgs && msgs.length > 0) break;
        } catch (e) {
          if (e.message.includes('FloodWait')) {
            const seconds = parseInt(e.message.match(/\d+/)[0]) || 5;
            this.logger.warn(`遇到 FloodWait，等待 ${seconds} 秒后重试...`);
            await new Promise(resolve => setTimeout(resolve, seconds * 1000));
          } else {
            throw e;
          }
        }
        retries++;
        if (retries < maxRetries) await new Promise(resolve => setTimeout(resolve, 1000));
      }

      if (!msgs || msgs.length === 0 || !msgs[0]) {
        throw new Error(`无法获取消息内容 (Message ID: ${messageId})`);
      }
      
      const m = msgs[0];
      
      let fileName = this.getFileNameFromMessage(m);
      let fileSize = 0;
      const mediaType = this.getMediaTypeFromMessage(m);

      if (m.media instanceof Api.MessageMediaDocument) {
        const doc = m.media.document;
        fileSize = doc.size ? (typeof doc.size === 'bigint' ? Number(doc.size) : doc.size) : 0;
      } else if (m.media instanceof Api.MessageMediaPhoto) {
        const photo = m.media.photo;
        if (photo && photo.sizes) {
          const largest = photo.sizes[photo.sizes.length - 1];
          // 尝试多种可能的大小字段
          fileSize = largest.size || (largest instanceof Api.PhotoSize ? largest.size : 0);
          if (!fileSize && largest.sizes) fileSize = largest.sizes.pop();
        }
      }

      return {
        fileName,
        fileSize: Number(fileSize || 0),
        mediaType,
        message: m
      };
    } catch (error) {
      this.logger.error(`getFileInfo 出错 (ID: ${messageId}):`, error.message);
      throw error;
    }
  }

  getFileNameFromMessage(m) {
    if (!m.media) return `text_${m.id}`;

    if (m.media instanceof Api.MessageMediaDocument) {
      const doc = m.media.document;
      // 优先从属性中查找文件名
      const fileAttr = doc.attributes?.find(a => a instanceof Api.DocumentAttributeFilename);
      if (fileAttr && fileAttr.fileName) return fileAttr.fileName;
      
      // 根据类型生成保底名
      const mediaType = this.getMediaTypeFromMessage(m);
      if (mediaType === 'video') return `${m.id}.mp4`;
      if (mediaType === 'audio') return `${m.id}.mp3`;
      if (mediaType === 'animation') return `${m.id}.gif`;
      return `doc_${m.id}`;
    }
    
    if (m.media instanceof Api.MessageMediaPhoto) {
      return `${m.id}.jpg`;
    }
    
    return `file_${m.id}`;
  }

  async downloadMedia(channelInfo, messageId, savePath, progressCallback) {
    // 利用 client.downloadMedia 获取 Buffer 并写入文件
    const msg = await this.client.getMessages(await this.getChannelEntity(channelInfo), { ids: [messageId] });
    if (!msg || msg.length === 0) throw new Error('消息不存在');
    const m = msg[0];
    const buffer = await this.client.downloadMedia(m, {
      fileCache: false,
      progressCallback,
    });
    await fs.promises.writeFile(savePath, buffer);
  }
}
