from typing import Callable, Optional

from telethon import TelegramClient

from telegram.limiter import rate_limiter


class DownloadEngine:
    async def download_via_telethon(
        self,
        client: TelegramClient,
        message,
        save_path: str,
        progress_callback: Optional[Callable] = None,
    ):
        if not message or not message.media:
            raise ValueError("Message does not contain media")

        await rate_limiter.wait()
        return await client.download_media(message, file=save_path, progress_callback=progress_callback)


download_engine = DownloadEngine()
