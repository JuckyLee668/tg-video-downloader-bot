import { readFileSync, writeFileSync, existsSync } from 'fs';
import { parse, stringify } from 'yaml';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { config as dotenvConfig } from 'dotenv';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

export class ConfigManager {
  static config = null;

  /**
   * 加载环境变量
   */
  static loadEnv() {
    const envPath = join(__dirname, '../.env');
    if (existsSync(envPath)) {
      dotenvConfig({ path: envPath });
    }
  }

  /**
   * 从环境变量或配置中获取值
   */
  static getEnvValue(key, defaultValue = null) {
    return process.env[key] || defaultValue;
  }

  static loadConfig() {
    if (this.config) {
      return this.config;
    }

    // 先加载 .env 文件
    this.loadEnv();

    try {
      // 尝试加载 config.local.yaml，否则使用 config.yaml
      let configPath = join(__dirname, '../config.local.yaml');
      try {
        readFileSync(configPath);
      } catch (e) {
        configPath = join(__dirname, '../config.yaml');
      }

      const fileContents = readFileSync(configPath, 'utf-8');
      this.config = parse(fileContents);
      
      // 从环境变量覆盖敏感信息（环境变量优先）
      // Bot Token
      this.config.bot_token = this.getEnvValue('BOT_TOKEN', this.config.bot_token);
      
      // 覆盖 remote_api 配置（如果环境变量存在）
      if (!this.config.remote_api) {
        this.config.remote_api = {};
      }
      
      // 环境变量优先，如果不存在则使用配置文件中的值
      this.config.remote_api.bot_api_host = this.getEnvValue(
        'BOT_API_HOST',
        this.config.remote_api.bot_api_host
      );
      
      this.config.remote_api.public_file_base_url = this.getEnvValue(
        'PUBLIC_FILE_BASE_URL',
        this.config.remote_api.public_file_base_url
      );
      
      this.config.remote_api.tg_base_dir = this.getEnvValue(
        'TG_BASE_DIR',
        this.config.remote_api.tg_base_dir || '/media/TGbot'
      );
      
      // 加载 user_api 配置（用于频道搜索）
      if (!this.config.user_api) {
        this.config.user_api = {};
      }
      
      this.config.user_api.api_id = this.getEnvValue(
        'USER_API_ID',
        this.config.user_api.api_id
      );
      
      this.config.user_api.api_hash = this.getEnvValue(
        'USER_API_HASH',
        this.config.user_api.api_hash
      );
      
      this.config.user_api.proxy = this.getEnvValue(
        'USER_API_PROXY',
        this.config.user_api.proxy
      );
      
      return this.config;
    } catch (error) {
      throw new Error(`加载配置文件失败: ${error.message}`);
    }
  }

  static updateLastReadMessageId(chatId, messageId) {
    if (!this.config) {
      return;
    }

    const chat = this.config.chat.find((c) => c.chat_id === chatId);
    if (chat) {
      chat.last_read_message_id = messageId;

      // 保存到文件
      try {
        const configPath = join(__dirname, '../config.yaml');
        const yamlString = stringify(this.config);
        writeFileSync(configPath, yamlString, 'utf-8');
      } catch (error) {
        // 静默失败，避免影响主流程
        // 如果需要日志，可以通过参数传入 logger
        // console.error('保存配置失败:', error);
      }
    }
  }

  static getConfig() {
    return this.config || this.loadConfig();
  }
}
