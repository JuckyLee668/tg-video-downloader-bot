"""配置下载进度推送 — 内联键盘交互

/push — 查看当前状态，使用内联键盘切换
"""

from loguru import logger
from telethon import Button

from core.config import config


def _push_status_text():
    status = "🟢 已启用" if config.progress_notification else "🔴 已禁用"
    return f"📊 **下载进度推送**\n\n状态: {status}"


def _push_keyboard():
    return [
        [Button.inline("🟢 启用", b"push:enable"),
         Button.inline("🔴 禁用", b"push:disable")],
    ]


async def progress_push_handler(event, arg=None):
    """显示进度推送配置内联键盘"""
    await event.respond(
        _push_status_text() + "\n\n💡 点击按钮切换：",
        buttons=_push_keyboard(),
    )


async def push_callback_handler(event):
    """处理 push: 回调"""
    data = event.data.decode() if isinstance(event.data, bytes) else event.data

    if data == "push:enable":
        config.progress_notification = True
        config.save()
        logger.info("Progress notification enabled (via inline)")
        await event.edit(
            _push_status_text() + "\n\n✅ 已启用\n\n💡 点击按钮切换：",
            buttons=_push_keyboard(),
        )
    elif data == "push:disable":
        config.progress_notification = False
        config.save()
        logger.info("Progress notification disabled (via inline)")
        await event.edit(
            _push_status_text() + "\n\n⏸️ 已禁用\n\n💡 点击按钮切换：",
            buttons=_push_keyboard(),
        )
