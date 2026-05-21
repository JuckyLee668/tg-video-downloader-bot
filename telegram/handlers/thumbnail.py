import asyncio
import os
import time
from pathlib import Path
from typing import List, Optional, Tuple

from loguru import logger
from telethon import TelegramClient
from telethon.tl.types import Message

THUMB_DIR = Path("/root/.tg_downloader_thumbs")
MAX_THUMBS = 15


async def ensure_thumb_dir():
    THUMB_DIR.mkdir(parents=True, exist_ok=True)


async def cleanup_old_thumbs():
    """清理 1 小时前的旧缩略图"""
    if not THUMB_DIR.exists():
        return
    now = time.time()
    for f in list(THUMB_DIR.iterdir()):
        if f.is_file() and now - f.stat().st_mtime > 3600:
            try:
                f.unlink()
            except OSError:
                pass


async def generate_thumbnails(
    client: TelegramClient,
    messages: List[Message],
    max_thumbs: int = MAX_THUMBS,
) -> List[Tuple[Path, str]]:
    """
    为搜索结果生成缩略图。
    - 视频：下载 Telegram 内置的视频缩略图
    - 图片：下载最小尺寸，再用 Pillow 压缩
    返回 [(缩略图路径, 文件名), ...] 列表，只含视频/图片。
    """
    await ensure_thumb_dir()

    targets: List[Tuple[Message, str]] = []
    for msg in messages[:max_thumbs]:
        name = msg.file.name or f"media_{msg.id}"
        if msg.video:
            targets.append((msg, name))
        elif msg.photo:
            targets.append((msg, name))

    if not targets:
        return []

    async def _download_one(msg: Message, fname: str) -> Optional[Tuple[Path, str]]:
        try:
            safe_id = f"{abs(msg.chat_id)}_{msg.id}"

            if msg.video:
                out_path = THUMB_DIR / f"v_{safe_id}.jpg"
                if out_path.exists():
                    return (out_path, fname)

                # 下载 Telegram 内置视频缩略图（速度快，不下载完整视频）
                result = await client.download_media(msg, thumb=-1)
                if result and Path(result).stat().st_size > 0:
                    p = Path(result)
                    if p != out_path:
                        os.rename(str(p), str(out_path))
                    return (out_path, fname)
                # 没有内置缩略图，跳过（避免下载整个视频）
                logger.debug(f"No thumbnail available for video msg {msg.id}")

            elif msg.photo:
                out_path = THUMB_DIR / f"p_{safe_id}.jpg"
                if out_path.exists():
                    return (out_path, fname)

                result = await client.download_media(msg, file=str(out_path))
                if result and Path(result).stat().st_size > 0:
                    p = Path(result)
                    if p != out_path:
                        os.rename(str(p), str(out_path))
                    try:
                        from PIL import Image
                        img = Image.open(out_path)
                        img.thumbnail((320, 320))
                        img.save(out_path, "JPEG", quality=75)
                    except Exception:
                        pass
                    return (out_path, fname)
        except Exception as e:
            logger.warning(f"Thumbnail failed for msg {msg.id}: {e}")
        return None

    tasks = [_download_one(msg, fname) for msg, fname in targets]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    valid = []
    for r in results:
        if isinstance(r, Exception):
            continue
        if r and Path(r[0]).exists() and Path(r[0]).stat().st_size > 0:
            valid.append(r)

    return valid


async def send_thumbnails(event, thumb_items: List[Tuple[Path, str]]):
    """
    以相册形式发送缩略图，不带标题。
    thumb_items: [(图片路径, 文件名), ...]
    """
    if not thumb_items:
        return
    try:
        batch_size = 10
        for i in range(0, len(thumb_items), batch_size):
            batch = thumb_items[i:i + batch_size]
            files = [str(p) for p, _ in batch]
            await event.client.send_file(event.chat_id, files, album=True)
    except Exception as e:
        logger.warning(f"Failed to send thumbnail album: {e}")
        try:
            for p, _ in thumb_items:
                await event.client.send_file(event.chat_id, str(p))
        except Exception:
            pass
