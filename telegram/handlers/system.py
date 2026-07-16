import aiosqlite

from core.database import db_manager
from telegram.handlers.utils import format_size


async def start_handler(event, arg=None):
    return await help_handler(event)


async def help_handler(event, arg=None):
    help_text = (
        "🤖 **Telegram Media Downloader Bot**\n\n"
        "🔐 **账号**\n"
        "• `/login` — 查看登录状态 / 登录账号\n"
        "• `/status` (`/s`) — 任务队列与进度\n\n"
        "📺 **频道**\n"
        "• `/channel` — 连接 / 查看频道\n"
        "  (`/channel connect @xx` `/channel list`)\n\n"
        "🔍 **搜索**\n"
        "• `/search` — 搜索频道媒体\n"
        "  (`/search keyword xx` `/search recent` `/search time 开始 结束`)\n\n"
        "📥 **下载**\n"
        "• `/download` — 批量下载搜索结果\n"
        "  (`/download 1-5` `/download format mp4`)\n"
        "• `/forward` — 转发到其他聊天\n"
        "  (`/forward 1-5` `/forward to @target` `/forward link <url>`)\n"
        "• `/dl` — 下载队列\n\n"
        "🌐 **外部视频**\n"
        "• `/tw <链接>` — Twitter/X 视频下载\n"
        "• 直接发链接 — 自动识别并下载\n\n"
        "📦 **存储**\n"
        "• `/files` (`/f`) — 本地文件管理\n"
        "• `/aliyun` — 阿里云盘管理\n\n"
        "⚙️ **配置**\n"
        "• `/autofwd` — 下载后自动转发\n"
        "• `/push` — 进度推送开关\n"
        "• `/rename` — 智能重命名\n"
        "• `/watch` — 频道自动监控\n\n"
        "🛠 **其他**\n"
        "• `/cancel` (`/c`) — 取消操作\n"
        "• `/clear` (`/cl`) — 清理下载队列\n"
        "• `/help` (`/h`) — 此菜单"
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
