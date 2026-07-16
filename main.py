import asyncio
import os
import sys
from pathlib import Path

import uvicorn
from loguru import logger

# Ensure local packages are importable both from source and from a bundled app.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import config
from core.database import db_manager
from downloader.manager import download_manager
from telegram.client import tg_clients
from telegram.handlers import setup_handlers
from web.server import create_app

PID_FILE = PROJECT_ROOT / "data" / "bot.pid"


def _acquire_pid_lock() -> int:
    """Write PID file and return our PID.

    Refuses to start if another instance is already running (stale PID
    files are detected and overwritten automatically).
    """
    pid_file = str(PID_FILE)
    pid = os.getpid()

    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            # Check whether the old process is still alive
            try:
                os.kill(old_pid, 0)
            except OSError:
                logger.warning(f"Stale PID file found (pid {old_pid} is gone), overwriting")
            else:
                logger.error(
                    f"Another bot instance is already running (pid {old_pid}). "
                    f"If you are sure it has stopped, delete {pid_file} and try again."
                )
                sys.exit(1)
        except (ValueError, FileNotFoundError):
            pass

    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    with open(pid_file, "w") as f:
        f.write(str(pid))
    logger.info(f"PID lock acquired → {pid_file} ({pid})")
    return pid


def _release_pid_lock():
    """Remove the PID file on clean shutdown."""
    pid_file = str(PID_FILE)
    try:
        os.remove(pid_file)
        logger.info("PID lock released")
    except FileNotFoundError:
        pass


async def main():
    logger.info("Starting Telegram media downloader")

    _acquire_pid_lock()

    await db_manager.init()
    await tg_clients.init()
    setup_handlers()
    await download_manager.init()

    # 启动频道自动监控
    from telegram.auto_watch import watch_manager

    await watch_manager.start()

    app = create_app()
    web_api_key = (os.getenv("WEB_API_KEY") or "").strip()
    if config.web_host == "0.0.0.0" and not web_api_key:
        logger.warning("Web server is externally reachable without WEB_API_KEY; use local mode only.")

    server_config = uvicorn.Config(app, host=config.web_host, port=config.web_port, log_level="info")

    from telethon.errors.rpcerrorlist import AuthKeyDuplicatedError

    try:
        while True:
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
                break  # Normal exit — don't loop
            except AuthKeyDuplicatedError:
                logger.error(
                    "Auth key 已失效（IP 冲突），正在重置所有 session 并自动重连..."
                )
                tg_clients.reset_bot_session()
                tg_clients.reset_user_session()
                # Disconnect any open connections before re-init
                if tg_clients.bot_client:
                    await tg_clients.bot_client.disconnect()
                if tg_clients.user_client:
                    await tg_clients.user_client.disconnect()
                # Reinitialize clients
                await tg_clients.init()
                # Re-register handlers (they may reference the old client)
                from telegram.handlers import setup_handlers
                setup_handlers()
                logger.info("Session 已重置，继续运行...")
                continue
            except (asyncio.CancelledError, KeyboardInterrupt):
                logger.info("Shutting down application")
                break
    finally:
        for worker in download_manager.worker_tasks:
            worker.cancel()
        if download_manager.worker_tasks:
            await asyncio.gather(*download_manager.worker_tasks, return_exceptions=True)
        if tg_clients.bot_client:
            await tg_clients.bot_client.disconnect()
        if tg_clients.user_client:
            await tg_clients.user_client.disconnect()
        await db_manager.close()
        _release_pid_lock()
        logger.info("Application stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
