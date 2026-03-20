import asyncio
import uvicorn
import sys
from pathlib import Path
from loguru import logger

# Ensure local packages are importable both from source and from a bundled app.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import config
from core.database import db_manager
from telegram.client import tg_clients
from telegram.handlers import setup_handlers
from downloader.manager import download_manager
from web.server import create_app

async def main():
    logger.info("正在启动 Telegram 媒体下载器 Python 版...")
    
    # 1. Initialize Database
    await db_manager.init()
    
    # 2. Initialize Telegram Clients
    await tg_clients.init()
    
    # 3. Setup Handlers
    setup_handlers()
    
    # 4. Initialize Download Manager
    await download_manager.init()
    
    # 5. Start Web Server
    app = create_app()
    server_config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(server_config)
    
    # Run everything
    try:
        tasks = [
            server.serve(),
        ]
        if tg_clients.bot_client:
            tasks.append(tg_clients.bot_client.run_until_disconnected())
        
        # Only run user client if it's already authorized
        if tg_clients.user_client:
            is_authorized = await tg_clients.user_client.is_user_authorized()
            if is_authorized:
                tasks.append(tg_clients.user_client.run_until_disconnected())
            else:
                logger.warning("Telegram 用户客户端尚未登录，仅启动 Bot 客户端")
            
        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("正在关闭应用...")
    finally:
        # Cleanup
        if tg_clients.bot_client:
            await tg_clients.bot_client.disconnect()
        if tg_clients.user_client:
            await tg_clients.user_client.disconnect()
        logger.info("应用已关闭")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
