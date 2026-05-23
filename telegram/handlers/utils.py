import os
from datetime import datetime
from typing import Tuple


def parse_indices(indices_str: str) -> set[int]:
    """解析序号范围字符串，如 '1-3, 5' → {1,2,3,5} 或 'all' → 空集"""
    if not indices_str or indices_str.strip().lower() == "all":
        return set()

    indices: set[int] = set()
    parts = indices_str.replace("，", ",").split(",")
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                start, end = map(int, part.split("-", 1))
                indices.update(range(start, end + 1))
            except ValueError:
                raise ValueError(f"Invalid range: {part}") from None
        else:
            try:
                indices.add(int(part))
            except ValueError:
                raise ValueError(f"Invalid index: {part}") from None
    return indices


def format_size(size_bytes: int) -> str:
    """字节数 → 人类可读大小字符串"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} PB"


def format_time(timestamp: float) -> str:
    """时间戳 → 月-日 时:分 字符串"""
    return datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M")


def message_file_info(msg) -> Tuple[str, str]:
    """从 Telegram Message 对象提取 (file_name, media_type)"""
    raw_name = msg.file.name if msg.file else None
    safe_name = raw_name if (raw_name and os.path.splitext(raw_name)[0]) else None
    if msg.video:
        return safe_name or f"video_{msg.id}.mp4", "video"
    if msg.photo:
        return f"photo_{msg.id}.jpg", "photo"
    if msg.audio:
        return safe_name or f"audio_{msg.id}.mp3", "audio"
    if msg.voice:
        return f"voice_{msg.id}.ogg", "voice"
    if msg.gif:
        return safe_name or f"animation_{msg.id}.gif", "animation"
    if msg.document:
        return safe_name or f"doc_{msg.id}", "document"
    return f"media_{msg.id}", "unknown"


def message_file_name(msg) -> str:
    """从 Telegram Message 对象提取文件名"""
    if msg.file and msg.file.name:
        return msg.file.name
    mime = getattr(msg.file, "mime_type", "") if msg.file else ""
    if "video" in mime:
        ext = ".mp4"
    elif "audio" in mime:
        ext = ".mp3"
    elif "image" in mime:
        ext = ".jpg"
    else:
        ext = ""
    return f"media_{msg.id}{ext}"


async def ensure_searcher(event=None):
    """确保 Searcher 已初始化并连接"""
    from telegram.client import tg_clients

    if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
        if event:
            await event.respond("❌ 请先使用 /login 登录用户账号。")
        return False

    from telegram.search import init_searcher, searcher

    if not searcher:
        init_searcher(tg_clients.user_client)

    if not await searcher.ensure_connected():
        if event:
            await event.respond("❌ 请先使用 /cc 连接要搜索的频道。")
        return False
    return True
