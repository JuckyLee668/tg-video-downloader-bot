import os
import asyncio
from typing import Optional
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from core.config import config
from loguru import logger

from telegram.search import init_searcher

def get_proxy_dict(proxy_config):
    if not proxy_config:
        return None
    import socks
    return {
        'proxy_type': socks.SOCKS5 if proxy_config.scheme == 'socks5' else socks.HTTP,
        'addr': proxy_config.hostname,
        'port': proxy_config.port,
        'username': proxy_config.username,
        'password': proxy_config.password,
        'rdns': True
    }

class TelegramClientWrapper:
    def __init__(self):
        self.bot_client: Optional[TelegramClient] = None
        self.user_client: Optional[TelegramClient] = None
        self.session_file = "session.txt"
        self.phone = None
        self.phone_code_hash = None
        
    async def init(self):
        proxy = get_proxy_dict(config.proxy)
        
        # Initialize Bot Client
        if config.bot_token:
            self.bot_client = TelegramClient('bot_session', 
                                           int(config.user_api.api_id or 0), 
                                           config.user_api.api_hash or "",
                                           proxy=proxy)
            await self.bot_client.start(bot_token=config.bot_token)
            logger.info("Telegram Bot 客户端已启动 (MTProto)")

        # Initialize User Client
        if config.user_api.api_id and config.user_api.api_hash:
            session_str = ""
            if os.path.exists(self.session_file):
                with open(self.session_file, "r") as f:
                    session_str = f.read().strip()
            
            self.user_client = TelegramClient(StringSession(session_str), 
                                            int(config.user_api.api_id), 
                                            config.user_api.api_hash,
                                            proxy=proxy)
            await self.user_client.connect()
            
            if await self.user_client.is_user_authorized():
                logger.info("Telegram 用户客户端已连接")
                init_searcher(self.user_client)
            else:
                logger.warning("Telegram 用户客户端尚未登录，请通过 Bot 发送 /login 进行登录")
    
    async def save_user_session(self):
        if self.user_client:
            with open(self.session_file, "w") as f:
                f.write(self.user_client.session.save())

    async def send_code(self, phone: str):
        if not self.user_client:
            if not config.user_api.api_id or not config.user_api.api_hash:
                raise Exception("未配置 USER_API_ID 或 USER_API_HASH，请检查 .env 文件")
            
            proxy = get_proxy_dict(config.proxy)
            self.user_client = TelegramClient(StringSession(""), 
                                            int(config.user_api.api_id), 
                                            config.user_api.api_hash,
                                            proxy=proxy)
            await self.user_client.connect()

        self.phone = phone
        res = await self.user_client.send_code_request(phone)
        self.phone_code_hash = res.phone_code_hash
        return res

    async def sign_in(self, code: str):
        try:
            user = await self.user_client.sign_in(self.phone, code, phone_code_hash=self.phone_code_hash)
            await self.save_user_session()
            init_searcher(self.user_client)
            return user
        except Exception as e:
            logger.error(f"登录失败: {e}")
            raise e


tg_clients = TelegramClientWrapper()
