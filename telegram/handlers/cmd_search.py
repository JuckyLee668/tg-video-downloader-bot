"""搜索 — /search

/search keyword <关键词>      关键词搜索
/search recent [数量]         获取最新
/search time <开始> <结束>    时间范围搜索
"""

from telegram.handlers.search import (
    search_history_handler,
    search_keyword_handler,
    search_recent_handler,
    search_time_handler,
)


async def search_handler(event, arg=None):
    if not arg:
        return await search_keyword_handler(event)

    parts = arg.strip().split(maxsplit=1)
    sub = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if sub in ("keyword", "k", "csk"):
        return await search_keyword_handler(event, rest)
    elif sub in ("recent", "r", "csr", "latest"):
        return await search_recent_handler(event, rest)
    elif sub in ("time", "t", "cst", "date"):
        return await search_time_handler(event, rest)
    elif sub in ("history", "h", "sh"):
        return await search_history_handler(event, rest)
    else:
        # 默认当作关键词搜索
        return await search_keyword_handler(event, arg.strip())
