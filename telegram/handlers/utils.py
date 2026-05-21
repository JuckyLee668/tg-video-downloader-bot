import math

from telegram import search
from telegram.client import tg_clients


async def ensure_searcher(event=None):
    """
    统一确保 Searcher 已初始化并连接
    """
    if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
        if event:
            await event.respond("❌ 用户客户端未登录。请先发送 `/login` 进行登录。")
        return False

    if not search.searcher:
        from telegram.search import init_searcher
        init_searcher(tg_clients.user_client)

    if not await search.searcher.ensure_connected():
        if event:
            await event.respond("❌ 请先使用 `/cc` 连接一个频道。")
        return False
        
    return True

def parse_indices(arg: str) -> set[int]:
    """解析序号范围字符串（如 '1-3, 5' 或 '1，3-5'），返回索引集合"""
    indices = set()
    arg_clean = arg.replace('，', ',')
    parts = arg_clean.split(',')
    for part in parts:
        part = part.strip()
        if '-' in part:
            start_str, end_str = part.split('-')
            indices.update(range(int(start_str), int(end_str) + 1))
        elif part.isdigit():
            indices.add(int(part))
    return indices

def format_size(size_bytes):
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"
