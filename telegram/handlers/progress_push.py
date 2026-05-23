"""配置下载进度推送

/push         — 查看当前状态
/push on      — 启用
/push off     — 禁用
"""

from loguru import logger

from core.config import config


async def progress_push_handler(event, arg=None):
    """配置下载进度推送"""
    if not arg:
        return await _show_status(event)

    cmd = arg.strip().lower()

    if cmd == "on":
        config.progress_notification = True
        config.save()
        logger.info("Progress notification enabled")
        await event.respond("✅ 已启用下载进度推送。大文件下载时会实时显示进度。")

    elif cmd == "off":
        config.progress_notification = False
        config.save()
        logger.info("Progress notification disabled")
        await event.respond("⏸️ 已禁用下载进度推送。")

    else:
        await event.respond(
            "❌ 未知子命令。支持:\n"
            "• `/push on` — 启用\n"
            "• `/push off` — 禁用\n"
            "• `/push` — 查看状态"
        )


async def _show_status(event):
    status = "🟢 已启用" if config.progress_notification else "🔴 已禁用"
    text = (
        f"📊 **下载进度推送**\n\n"
        f"状态: {status}\n\n"
        f"💡 用法:\n"
        f"• `/push on` — 启用\n"
        f"• `/push off` — 禁用\n"
        f"• `/push` — 查看状态"
    )
    await event.respond(text)
