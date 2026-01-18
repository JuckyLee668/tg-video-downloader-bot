import { fileURLToPath } from 'url';
import { dirname } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

/**
 * 消息发送限流器
 * 用于控制 Telegram Bot API 消息发送频率，避免触发 429 错误
 */
export class MessageRateLimiter {
  constructor(logger, options = {}) {
    this.logger = logger;
    // 更保守的配置：每秒最多 10 条消息，每分钟最多 20 条
    // 对同一 chatId：每秒最多 1 条消息
    this.maxPerSecond = options.maxPerSecond || 10;
    this.maxPerMinute = options.maxPerMinute || 20;
    this.maxPerChatPerSecond = options.maxPerChatPerSecond || 1;
    
    // 记录发送时间（全局）
    this.recentMessages = [];
    // 记录每个 chatId 的发送时间
    this.chatMessages = new Map(); // chatId -> [timestamps]
    
    // 429 错误重试配置
    this.retryDelays = new Map(); // chatId -> retryAfter timestamp
  }

  /**
   * 发送消息（带限流和重试）
   */
  async sendMessage(bot, chatId, text, options = {}) {
    // 检查是否有待重试的延迟
    const retryAfter = this.retryDelays.get(chatId);
    if (retryAfter && Date.now() < retryAfter) {
      const waitTime = retryAfter - Date.now();
      this.logger.debug(`等待 ${waitTime}ms 后重试发送消息到 ${chatId}`);
      await this.sleep(waitTime);
      // 清除重试记录
      this.retryDelays.delete(chatId);
    }

    // 限流检查（传入 chatId 以检查单聊限制）
    await this.waitForRateLimit(chatId);

    try {
      const result = await bot.sendMessage(chatId, text, options);
      this.recordMessage(chatId);
      return result;
    } catch (error) {
      // 处理 429 错误（Too Many Requests）
      if (error.response?.error_code === 429 || error.code === 'ETELEGRAM' && error.response?.statusCode === 429) {
        const retryAfterValue = error.response?.parameters?.retry_after ||
                          error.response?.body?.parameters?.retry_after ||
                          5;
        const waitTime = retryAfterValue * 1000; // 转换为毫秒

        this.logger.warn(`收到 429 限流错误，等待 ${retryAfterValue} 秒后重试 (chatId: ${chatId})`);

        // 记录重试时间（增加缓冲时间）
        this.retryDelays.set(chatId, Date.now() + waitTime + 1000);

        // 等待后重试
        await this.sleep(waitTime + 1000);

        // 清除重试记录
        this.retryDelays.delete(chatId);

        // 再次限流检查
        await this.waitForRateLimit(chatId);

        // 重试发送
        try {
          const result = await bot.sendMessage(chatId, text, options);
          this.recordMessage(chatId);
          this.logger.info(`重试发送消息成功 (chatId: ${chatId})`);
          return result;
        } catch (retryError) {
          // 如果重试仍然失败，等待更长时间
          if (retryError.response?.error_code === 429 || retryError.code === 'ETELEGRAM' && retryError.response?.statusCode === 429) {
            const newRetryAfter = retryError.response?.parameters?.retry_after ||
                                 retryError.response?.body?.parameters?.retry_after ||
                                 10;
            const newWaitTime = newRetryAfter * 1000;

            this.logger.error(`重试后仍收到 429 错误，等待 ${newRetryAfter} 秒 (chatId: ${chatId})`);

            // 等待更长时间
            await this.sleep(newWaitTime + 2000);

            // 再次尝试（这是最后一次尝试）
            try {
              // 先检查限流
              await this.waitForRateLimit(chatId);

              const finalResult = await bot.sendMessage(chatId, text, options);
              this.recordMessage(chatId);
              this.logger.info(`第三次尝试发送消息成功 (chatId: ${chatId})`);
              return finalResult;
            } catch (finalError) {
              // 如果仍然失败，设置延迟并抛出
              this.retryDelays.set(chatId, Date.now() + newWaitTime + 5000);
              this.logger.error(`发送消息最终失败 (chatId: ${chatId}):`, finalError.message);
              throw finalError;
            }
          }
          throw retryError;
        }
      }

      // 其他错误直接抛出
      throw error;
    }
  }

  /**
   * 等待限流通过
   */
  async waitForRateLimit(chatId = null) {
    const now = Date.now();
    
    // 清理超过 1 分钟的记录
    this.recentMessages = this.recentMessages.filter(
      timestamp => now - timestamp < 60000
    );

    // 检查全局每秒限制
    const messagesInLastSecond = this.recentMessages.filter(
      timestamp => now - timestamp < 1000
    ).length;

    if (messagesInLastSecond >= this.maxPerSecond) {
      // 找到最近一条消息的时间（最旧的）
      const recentMessages = this.recentMessages.filter(
        timestamp => now - timestamp < 1000
      );
      if (recentMessages.length > 0) {
        // 找到最旧的消息时间
        const oldestRecent = Math.min(...recentMessages);
        const waitTime = 1000 - (now - oldestRecent) + 100; // 额外 100ms 缓冲
        if (waitTime > 0 && waitTime < 2000) { // 确保等待时间合理
          this.logger.debug(`限流：等待 ${Math.round(waitTime)}ms (全局每秒限制: ${this.maxPerSecond})`);
          await this.sleep(waitTime);
        }
      }
    }

    // 检查全局每分钟限制
    if (this.recentMessages.length >= this.maxPerMinute) {
      const oldestMessage = this.recentMessages[0];
      const waitTime = 60000 - (now - oldestMessage) + 100; // 额外 100ms 缓冲
      if (waitTime > 0) {
        this.logger.debug(`限流：等待 ${waitTime}ms (全局每分钟限制: ${this.maxPerMinute})`);
        await this.sleep(waitTime);
      }
    }

    // 检查同一 chatId 的限制（如果提供了 chatId）
    if (chatId) {
      const chatTimestamps = this.chatMessages.get(chatId) || [];
      const recentChatMessages = chatTimestamps.filter(
        timestamp => now - timestamp < 1000
      );

      if (recentChatMessages.length >= this.maxPerChatPerSecond) {
        const oldestChatMessage = Math.min(...recentChatMessages);
        const waitTime = 1000 - (now - oldestChatMessage) + 200; // 额外 200ms 缓冲
        if (waitTime > 0 && waitTime < 2000) { // 确保等待时间合理
          this.logger.debug(`限流：等待 ${Math.round(waitTime)}ms (chatId ${chatId} 每秒限制: ${this.maxPerChatPerSecond})`);
          await this.sleep(waitTime);
        }
      }

      // 清理旧的记录
      const cleanedTimestamps = chatTimestamps.filter(
        timestamp => now - timestamp < 60000
      );
      this.chatMessages.set(chatId, cleanedTimestamps);
    }
  }

  /**
   * 记录消息发送时间
   */
  recordMessage(chatId = null) {
    const now = Date.now();
    this.recentMessages.push(now);
    
    // 保持数组大小合理（只保留最近 1 分钟的记录）
    if (this.recentMessages.length > this.maxPerMinute * 2) {
      this.recentMessages = this.recentMessages.slice(-this.maxPerMinute);
    }

    // 记录每个 chatId 的发送时间
    if (chatId) {
      const chatTimestamps = this.chatMessages.get(chatId) || [];
      chatTimestamps.push(now);
      
      // 清理超过 1 分钟的记录
      const cleanedTimestamps = chatTimestamps.filter(
        timestamp => now - timestamp < 60000
      );
      this.chatMessages.set(chatId, cleanedTimestamps);
    }
  }

  /**
   * 睡眠函数
   */
  sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  /**
   * 批量发送消息（自动限流）
   */
  async sendMessagesBatch(bot, messages) {
    const results = [];
    for (const { chatId, text, options } of messages) {
      try {
        const result = await this.sendMessage(bot, chatId, text, options);
        results.push({ success: true, result });
      } catch (error) {
        results.push({ success: false, error: error.message });
      }
    }
    return results;
  }
}
