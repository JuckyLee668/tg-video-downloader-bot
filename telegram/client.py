import os
import asyncio
from typing import Optional
from telethon import TelegramClient
from telethon.sessions import StringSession
from core.config import config, ProxyConfig
from loguru import logger
from utils.runtime_paths import app_path

from telegram.search import init_searcher

# Telethon connection tuning to improve stability behind proxies
TELETHON_KWARGS = dict(
    connection_retries=8,
    request_retries=5,
    timeout=30,        # seconds
    use_ipv6=False     # many proxies / networks don't handle IPv6 MTProto well
)


def get_proxy_dict(proxy_config):
    """
    Normalize proxy configuration to the mapping expected by Telethon.
    Accepts either ProxyConfig or a plain dict (from API payloads).
    """
    if not proxy_config:
        return None

    if isinstance(proxy_config, ProxyConfig):
        proxy_config = proxy_config.model_dump()

    scheme = proxy_config.get("scheme", "http").lower()

    return {
        # Telethon/python-socks accept string protocol names
        "proxy_type": "socks5" if scheme == "socks5" else "http",
        "addr": proxy_config.get("hostname", "127.0.0.1"),
        "port": int(proxy_config.get("port", 1080)),
        "username": proxy_config.get("username"),
        "password": proxy_config.get("password"),
        "rdns": proxy_config.get("rdns", True),
    }


class TelegramClientWrapper:
    def __init__(self):
        self.bot_client: Optional[TelegramClient] = None
        self.user_client: Optional[TelegramClient] = None
        self.session_file = str(app_path("session.txt"))
        self.phone = None
        self.phone_code_hash = None
        
    async def init(self):
        # Bot uses global proxy; user client prefers dedicated proxy when provided
        bot_proxy = get_proxy_dict(config.proxy)
        user_proxy = get_proxy_dict(config.user_api.proxy or config.proxy)
        
        # Initialize Bot Client
        if config.bot_token:
            logger.info(f"Init bot client with proxy={bot_proxy}")
            self.bot_client = TelegramClient(
                "bot_session",
                int(config.user_api.api_id or 0),
                config.user_api.api_hash or "",
                proxy=bot_proxy,
                **TELETHON_KWARGS,
            )
            await self.bot_client.start(bot_token=config.bot_token)
            logger.info("Telegram Bot 客户端已启动 (MTProto)")

        # Initialize User Client
        if config.user_api.api_id and config.user_api.api_hash:
            session_str = ""
            if os.path.exists(self.session_file):
                with open(self.session_file, "r") as f:
                    session_str = f.read().strip()
            
            logger.info(f"Init user client with proxy={user_proxy}")
            self.user_client = TelegramClient(
                StringSession(session_str),
                int(config.user_api.api_id),
                config.user_api.api_hash,
                proxy=user_proxy,
                **TELETHON_KWARGS,
            )
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
            
            proxy = get_proxy_dict(config.user_api.proxy or config.proxy)
            self.user_client = TelegramClient(
                StringSession(""),
                int(config.user_api.api_id),
                config.user_api.api_hash,
                proxy=proxy,
                **TELETHON_KWARGS,
            )
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
