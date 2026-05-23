"""配置下载后智能重命名

/rename            — 查看当前状态和 pattern
/rename on         — 启用
/rename off        — 禁用
/rename set <pat>  — 设置重命名 pattern
"""

from loguru import logger

from core.config import config


async def smart_rename_handler(event, arg=None):
    """配置下载后智能重命名"""
    if not arg:
        return await _show_status(event)

    parts = arg.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    param = parts[1] if len(parts) > 1 else ""

    if cmd == "on":
        config.file_rename.enabled = True
        config.save()
        logger.info("Smart rename enabled")
        await event.respond(
            f"✅ 已启用智能重命名。\n"
            f"当前 pattern: `{config.file_rename.pattern}`"
        )

    elif cmd == "off":
        config.file_rename.enabled = False
        config.save()
        logger.info("Smart rename disabled")
        await event.respond("⏸️ 已禁用智能重命名。")

    elif cmd == "set":
        if not param:
            await event.respond(
                "❌ 用法：`/rename set <pattern>`\n\n"
                "可用变量:\n"
                "• `{channel_title}` — 频道标题\n"
                "• `{channel_username}` — 频道用户名\n"
                "• `{date}` — 日期\n"
                "• `{time}` — 时间\n"
                "• `{original_name}` — 原文件名\n"
                "• `{ext}` — 扩展名\n\n"
                "示例: `/rename set {channel_title}/{date}_{original_name}{ext}`"
            )
            return
        config.file_rename.pattern = param
        config.file_rename.enabled = True  # 设置 pattern 时自动启用
        config.save()
        await event.respond(f"✅ 已设置智能重命名 pattern:\n`{param}`\n\n已自动启用。")

    else:
        await event.respond(
            "❌ 未知子命令。支持:\n"
            "• `/rename on` — 启用\n"
            "• `/rename off` — 禁用\n"
            "• `/rename set <pattern>` — 设置 pattern\n"
            "• `/rename` — 查看状态"
        )


async def _show_status(event):
    fn = config.file_rename
    status = "🟢 已启用" if fn.enabled else "🔴 已禁用"
    # Use regular string (not f-string) to avoid brace escaping issues
    text = (
        f"🏷️ **智能重命名**\n\n"
        f"状态: {status}\n"
        f"Pattern: `{fn.pattern}`\n\n"
        f"可用变量:\n"
        "• `{channel_title}` / `{channel_username}` — 频道信息\n"
        "• `{date}` / `{time}` — 时间\n"
        "• `{original_name}` / `{ext}` — 文件名\n\n"
        f"💡 用法:\n"
        "• `/rename on` — 启用\n"
        "• `/rename off` — 禁用\n"
        "• `/rename set {channel_title}/{date}_{original_name}{ext}` — 设置 pattern\n"
        "• `/rename` — 查看状态"
    )
    await event.respond(text)
