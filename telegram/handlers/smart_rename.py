"""配置下载后智能重命名 — 内联键盘交互

/rename — 查看当前状态，使用内联键盘切换/设置 pattern
"""

from loguru import logger
from telethon import Button

from core.config import config
from telegram.state_manager import state_manager


def _rename_status_text():
    fn = config.file_rename
    status = "🟢 已启用" if fn.enabled else "🔴 已禁用"
    return f"🏷️ **智能重命名**\n\n状态: {status}\nPattern: `{fn.pattern}`"


def _rename_keyboard():
    return [
        [Button.inline("🟢 启用", b"rename:enable"),
         Button.inline("🔴 禁用", b"rename:disable")],
        [Button.inline("✏️ 设置 Pattern", b"rename:pattern")],
    ]


async def smart_rename_handler(event, arg=None):
    """显示重命名配置内联键盘"""
    await event.respond(
        _rename_status_text() + "\n\n💡 选择操作：",
        buttons=_rename_keyboard(),
    )


async def rename_callback_handler(event):
    """处理 rename: 回调"""
    data = event.data.decode() if isinstance(event.data, bytes) else event.data

    if data == "rename:enable":
        config.file_rename.enabled = True
        config.save()
        await event.edit(
            _rename_status_text() + "\n\n✅ 已启用",
            buttons=_rename_keyboard(),
        )
    elif data == "rename:disable":
        config.file_rename.enabled = False
        config.save()
        await event.edit(
            _rename_status_text() + "\n\n⏸️ 已禁用",
            buttons=_rename_keyboard(),
        )
    elif data == "rename:pattern":
        await state_manager.set(event.chat_id, {
            "command": "rename_pattern",
            "step": "input",
        })
        await event.edit(
            _rename_status_text()
            + "\n\n✏️ 请输入重命名 pattern：\n"
            "可用变量: `{channel_title}` `{channel_username}` `{date}` `{time}` `{original_name}` `{ext}`\n"
            "示例: `{channel_title}/{date}_{original_name}{ext}`\n\n"
            "发送 `/cancel` 取消。",
            buttons=None,
        )


async def handle_rename_pattern(event, state):
    """FSM 处理：设置重命名 pattern"""
    pattern = event.text.strip()
    if pattern.lower() == "/cancel":
        await state_manager.clear(event.chat_id)
        await event.respond("❌ 已取消。", buttons=_rename_keyboard())
        return

    config.file_rename.pattern = pattern
    config.file_rename.enabled = True
    config.save()
    logger.info(f"Rename pattern set to: {pattern}")

    await state_manager.clear(event.chat_id)
    await event.respond(
        _rename_status_text() + f"\n\n✅ Pattern 已设置，已自动启用。",
        buttons=_rename_keyboard(),
    )
