import os
import asyncio
import math
from typing import Optional, Callable
from loguru import logger
from telethon import TelegramClient, errors, functions, types
from core.config import config

class DownloadEngine:
    def __init__(self):
        # Telegram API GetFileRequest 限制单块最大 512KB (524288 bytes)
        # 提高单块尺寸与并发，减少请求次数，加快大文件下载
        self.chunk_size = 1024 * 1024  # 1MB
        # 并行分片数: 建议 8-12，视网络/CPU 而定
        self.parallel_chunks = 12

    async def download_via_telethon(self, client: TelegramClient, message, save_path: str, progress_callback: Optional[Callable] = None):
        """
        使用并行分片逻辑下载 Telegram 媒体
        """
        if not message or not message.media:
            raise Exception("消息不包含媒体内容")

        # 获取文件信息
        file_size = message.file.size
        if not file_size:
            return await client.download_media(message, file=save_path, progress_callback=progress_callback)

        # 小于 5MB 的文件直接标准下载
        if file_size < 5 * 1024 * 1024:
            return await client.download_media(message, file=save_path, progress_callback=progress_callback)

        # 直接使用标准下载，避免跨 DC 并行导致的迁移/断线问题
        return await client.download_media(message, file=save_path, progress_callback=progress_callback)

    async def _parallel_download(self, client: TelegramClient, message, save_path: str, progress_callback: Optional[Callable] = None):
        file_size = message.file.size
        
        # 确定媒体所在的数据中心 (DC)
        # 如果媒体在不同 DC，手动并行下载会触发 FileMigrateError
        # 在多任务环境下切换主客户端 DC 会导致其他任务断开连接，因此我们直接抛错触发回退
        dc_id = getattr(message.document or message.photo, 'dc_id', None)
        if dc_id and dc_id != client.session.dc_id:
            raise Exception(f"文件位于 DC {dc_id}，为维护连接稳定，将使用标准模式下载")

        # 确定下载位置对象
        input_location = message.document or message.photo or message.media
        
        # 准备分片任务
        part_count = math.ceil(file_size / self.chunk_size)
        temp_path = f"{save_path}.part"
        os.makedirs(os.path.dirname(temp_path), exist_ok=True)
        
        with open(temp_path, 'wb') as f:
            f.truncate(file_size)

        sem = asyncio.Semaphore(self.parallel_chunks)
        downloaded = 0
        
        async def download_part(part_index):
            nonlocal downloaded
            offset = part_index * self.chunk_size
            limit = self.chunk_size
            
            async with sem:
                for attempt in range(3):
                    try:
                        result = await client(functions.upload.GetFileRequest(
                            location=input_location,
                            offset=offset,
                            limit=limit
                        ))
                        
                        if isinstance(result, types.upload.File):
                            with open(temp_path, 'rb+') as f:
                                f.seek(offset)
                                f.write(result.bytes)
                            
                            downloaded += len(result.bytes)
                            if progress_callback:
                                await progress_callback(downloaded, file_size)
                            return
                    except errors.FloodWaitError as e:
                        await asyncio.sleep(e.seconds)
                    except Exception as e:
                        if attempt == 2: raise e
                        await asyncio.sleep(1)

        try:
            # 执行并行任务
            tasks = [download_part(i) for i in range(part_count)]
            await asyncio.gather(*tasks)
            
            # 下载成功后重命名
            if os.path.exists(save_path):
                os.remove(save_path)
            os.rename(temp_path, save_path)
            
        finally:
            if os.path.exists(temp_path):
                try: 
                    # 只有在最终文件已生成的情况下才删除临时文件
                    if os.path.exists(save_path):
                        os.remove(temp_path)
                except: pass
        
        if progress_callback:
            await progress_callback(file_size, file_size)
            
        return save_path

download_engine = DownloadEngine()
