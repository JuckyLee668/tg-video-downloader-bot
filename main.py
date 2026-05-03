import asyncio
import os
import sys

import uvicorn
from loguru import logger

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import config
from core.database import db_manager
from downloader.manager import download_manager
from telegram.client import tg_clients
from telegram.handlers import setup_handlers
from web.server import create_app


async def main():
    logger.info("Starting Telegram media downloader")

    await db_manager.init()
    await tg_clients.init()
    setup_handlers()
    await download_manager.init()

    app = create_app()
    web_api_key = (os.getenv("WEB_API_KEY") or "").strip()
    if config.web_host == "0.0.0.0" and not web_api_key:
        logger.warning("Web server is externally reachable without WEB_API_KEY; use local mode only.")

    server_config = uvicorn.Config(app, host=config.web_host, port=config.web_port, log_level="info")
    server = uvicorn.Server(server_config)

    try:
        tasks = [server.serve()]
        if tg_clients.bot_client:
            tasks.append(tg_clients.bot_client.run_until_disconnected())

        if tg_clients.user_client:
            if await tg_clients.user_client.is_user_authorized():
                tasks.append(tg_clients.user_client.run_until_disconnected())
            else:
                logger.warning("Telegram user client is not logged in; bot client only")

        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("Shutting down application")
    finally:
        for worker in download_manager.worker_tasks:
            worker.cancel()
        if download_manager.worker_tasks:
            await asyncio.gather(*download_manager.worker_tasks, return_exceptions=True)
        if tg_clients.bot_client:
            await tg_clients.bot_client.disconnect()
        if tg_clients.user_client:
            await tg_clients.user_client.disconnect()
        logger.info("Application stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
