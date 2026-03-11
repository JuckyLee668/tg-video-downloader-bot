#!/usr/bin/env node
// small helper script to authenticate the user client and save session.txt
import dotenv from 'dotenv';
import { TelegramUserClient } from './src/channelClient.js';
import { parseProxy } from './src/utils.js';
import Input from 'input';

dotenv.config();

async function main() {
  const apiId = process.env.USER_API_ID;
  const apiHash = process.env.USER_API_HASH;

  if (!apiId || !apiHash) {
    console.error('请在环境变量中设置 USER_API_ID 和 USER_API_HASH');
    process.exit(1);
  }

  const proxy = parseProxy();
  const client = new TelegramUserClient(parseInt(apiId), apiHash, proxy);
  client.init();

  try {
    // start login flow
    await client.client.start({
      phoneNumber: async () => await Input.text('Phone number:'),
      password: async () => await Input.password('Two‑step password:'),
      phoneCode: async () => await Input.text('Code:'),
      onError: (err) => console.error('登录错误', err),
    });
    client.saveSession();
    console.log('登录成功，会话已保存到 session.txt');
  } catch (e) {
    console.error('登录失败:', e.message);
  }
}

main().catch(console.error);
