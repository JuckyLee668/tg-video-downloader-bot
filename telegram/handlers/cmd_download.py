"""下载 — /download

/download [序号范围]              批量下载
/download format <格式> [序号]    按格式下载
"""

from telegram.handlers.download import batch_download_formats_handler, batch_download_handler


async def download_handler(event, arg=None):
    if not arg:
        return await batch_download_handler(event)

    parts = arg.strip().split(maxsplit=1)
    sub = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if sub in ("format", "fmt", "f", "bdf"):
        return await batch_download_formats_handler(event, rest)
    else:
        # 默认当作序号范围
        return await batch_download_handler(event, arg.strip())
