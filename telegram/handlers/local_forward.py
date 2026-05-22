from loguru import logger

from core.config import config
from telegram.client import tg_clients


async def local_forward_handler(event, arg=None):
    """配置下载后自动转发到本地聊天

    /lf            - 查看当前配置
    /lf on         - 启用
    /lf off        - 禁用
    /lf set <id>   - 设置目标聊天 (chat_id 或 @username)
    /lf delete     - 切换转发后删除本地文件
    """
    if not arg:
        return await _show_status(event)

    parts = arg.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    param = parts[1] if len(parts) > 1 else ""

    if cmd == "on":
        config.local_forward.enabled = True
        config.save()
        logger.info("Local forward enabled")
        await event.respond("✅ 已启用下载后自动转发。")

    elif cmd == "off":
        config.local_forward.enabled = False
        config.save()
        logger.info("Local forward disabled")
        await event.respond("⏸️ 已禁用下载后自动转发。")

    elif cmd == "set":
        if not param:
            await event.respond("❌ 用法：`/lf set <chat_id>` 或 `/lf set @username`")
            return
        # 验证目标是否可达
        if tg_clients.user_client and await tg_clients.user_client.is_user_authorized():
            try:
                from downloader.manager import download_manager
                await download_manager._resolve_forward_peer(tg_clients.user_client, param)
            except Exception as e:
                await event.respond(f"⚠️ 目标暂无法解析，但仍会保存配置。\n错误: {e}")
        config.local_forward.target_chat = param
        config.save()
        await event.respond(f"✅ 已设置转发目标: `{param}`")

    elif cmd == "delete":
        new_val = not config.local_forward.delete_after_forward
        config.local_forward.delete_after_forward = new_val
        config.save()
        status = "✅ 转发后删除本地文件" if new_val else "⏸️ 转发后保留本地文件"
        await event.respond(status)

    else:
        await event.respond("❌ 未知子命令。支持: `on`, `off`, `set <id>`, `delete`")


async def _show_status(event):
    lf = config.local_forward
    status = "🟢 已启用" if lf.enabled else "🔴 已禁用"
    target = lf.target_chat or "未设置"
    delete_str = "是" if lf.delete_after_forward else "否"
    text = (
        f"📤 **下载后自动转发**\n\n"
        f"状态: {status}\n"
        f"目标: `{target}`\n"
        f"转发后删除: {delete_str}\n\n"
        f"💡 用法:\n"
        f"• `/lf on` — 启用\n"
        f"• `/lf off` — 禁用\n"
        f"• `/lf set <chat_id>` — 设置目标\n"
        f"• `/lf set @username` — 设置目标\n"
        f"• `/lf delete` — 切换删除开关\n"
        f"• `/lf` — 查看状态"
    )
    await event.respond(text)
