import asyncio
import math
import os
from typing import Callable, Optional

from telethon import TelegramClient, errors, functions, types

from telegram.limiter import rate_limiter


class DownloadEngine:
    def __init__(self):
        self.chunk_size = 1024 * 1024
        self.parallel_chunks = 12

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

    async def _parallel_download(
        self,
        client: TelegramClient,
        message,
        save_path: str,
        progress_callback: Optional[Callable] = None,
    ):
        file_size = message.file.size
        dc_id = getattr(message.document or message.photo, "dc_id", None)
        if dc_id and dc_id != client.session.dc_id:
            raise RuntimeError(f"Media is stored in DC {dc_id}; use standard download mode")

        input_location = message.document or message.photo or message.media
        part_count = math.ceil(file_size / self.chunk_size)
        temp_path = f"{save_path}.part"
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)

        with open(temp_path, "wb") as file:
            file.truncate(file_size)

        sem = asyncio.Semaphore(self.parallel_chunks)
        downloaded = 0

        async def download_part(part_index):
            nonlocal downloaded
            offset = part_index * self.chunk_size

            async with sem:
                for attempt in range(3):
                    try:
                        result = await client(functions.upload.GetFileRequest(
                            location=input_location,
                            offset=offset,
                            limit=self.chunk_size,
                        ))
                        if isinstance(result, types.upload.File):
                            with open(temp_path, "rb+") as file:
                                file.seek(offset)
                                file.write(result.bytes)

                            downloaded += len(result.bytes)
                            if progress_callback:
                                await progress_callback(downloaded, file_size)
                            return
                    except errors.FloodWaitError as e:
                        await asyncio.sleep(e.seconds)
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(1)

        try:
            await asyncio.gather(*(download_part(i) for i in range(part_count)))
            if os.path.exists(save_path):
                os.remove(save_path)
            os.rename(temp_path, save_path)
        finally:
            if os.path.exists(temp_path) and os.path.exists(save_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

        if progress_callback:
            await progress_callback(file_size, file_size)
        return save_path


download_engine = DownloadEngine()
