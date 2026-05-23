"""转发 — /forward

/forward [序号范围]              从搜索结果批量转发
/forward link <url>              通过链接转发
/forward to <目标> [序号]        指定目标 + 序号
"""

from telegram.handlers.forward import batch_forward_handler, forward_link_handler


async def forward_handler(event, arg=None):
    if not arg:
        return await batch_forward_handler(event)

    parts = arg.strip().split(maxsplit=1)
    sub = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if sub in ("link", "url", "l"):
        return await forward_link_handler(event, rest)
    elif sub in ("to", "target", "t"):
        # 解析: /forward to @target [indices]
        to_parts = rest.split(maxsplit=1) if rest else []
        if not to_parts:
            from telegram.state_manager import state_manager
            await event.respond("📤 请输入目标聊天（ID 或 @username）：")
            await state_manager.set(event.chat_id, {'command': 'bf', 'step': 'target'})
            return
        target = to_parts[0]
        indices_str = to_parts[1] if len(to_parts) > 1 else "all"
        # 直接调用 do_bf
        from telegram.handlers.forward import do_bf
        return await do_bf(event, target, indices_str)
    else:
        # 默认当作序号范围
        return await batch_forward_handler(event, arg.strip())
