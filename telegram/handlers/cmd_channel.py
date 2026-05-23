"""频道管理 — /channel

/channel connect @xx  连接频道
/channel list          查看已连接频道
"""

from telegram.handlers.channel import channels_list_handler, connect_channel_handler


async def channel_handler(event, arg=None):
    if not arg:
        return await channels_list_handler(event)

    parts = arg.strip().split(maxsplit=1)
    sub = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if sub in ("list", "ls", "all"):
        return await channels_list_handler(event)
    elif sub in ("connect", "add", "join", "cc"):
        return await connect_channel_handler(event, rest)
    else:
        # 默认当作频道标识去连接
        return await connect_channel_handler(event, arg.strip())
