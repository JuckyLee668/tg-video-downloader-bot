import aiosqlite

from core.database import db_manager
from telegram.handlers.utils import format_size


async def start_handler(event, arg=None):
    return await help_handler(event)


async def help_handler(event, arg=None):
    help_text = (
        "🤖 **Telegram Media Downloader Bot**\n\n"
        "🔐 **账号管理**\n"
        "• `/auth` — 查看账号登录状态\n"
        "• `/login` — 登录 Telegram 账号\n"
        "• `/status` (`/s`) — 查看任务状态与下载进度\n\n"
        "📺 **频道管理**\n"
        "• `/cc` — 连接频道进行搜索\n"
        "• `/channels` — 查看已连接频道\n\n"
        "🔍 **频道内容搜索**\n"
        "• `/csk` — 按关键词搜索\n"
        "• `/csr` — 获取最新消息\n"
        "• `/cst` — 按时间范围搜索\n\n"
        "📥 **下载与转发**\n"
        "• `/bd` — 批量下载媒体\n"
        "• `/bdf` — 按格式下载\n"
        "• `/bf` — 批量转发媒体\n\n"
        "📋 **下载记录**\n"
        "• `/dl` — 查看下载队列和历史\n"
        "• `/sh` — 搜索历史下载记录\n\n"
        "⚙️ **系统命令**\n"
        "• `/cancel` (`/c`) — 取消当前操作或下载\n"
        "• `/clear` (`/cl`) — 清理下载队列和历史\n"
        "• `/files` (`/f`) — 查看/清理本地下载文件\n"
        "• `/lf` — 配置下载后自动转发\n"
        "• `/watch` — 频道自动监控\n"
        "• `/help` — 返回完整菜单"
    )
    await event.respond(help_text)


async def status_handler(event, arg=None):
    summary = await db_manager.get_stats_summary()

    # 获取数据库活跃任务
    async with aiosqlite.connect(db_manager.db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM download_queue WHERE status = 'downloading' LIMIT 10")
        active_tasks = await cursor.fetchall()

    status_text = (
        "📊 **当前系统状态:**\n\n"
        f"⏳ 队列等待中: `{summary['pending']}`\n"
        f"📥 正在下载: `{summary['downloading']}`\n"
        f"✅ 已完成历史: `{summary['completed']}`\n"
        f"📦 总计下载大小: `{format_size(summary['total_size'] or 0)}`\n\n"
    )

    if active_tasks:
        status_text += "🚀 **活跃下载任务:**\n"
        for task in active_tasks:
            status_text += f"• `{task['file_name']}`: {task['progress'] or 0}%\n"
    else:
        status_text += "💤 当前没有正在运行的任务。"

    await event.respond(status_text)
