"""本地文件管理 — 内联键盘交互

/files — 查看本地下载文件，内联键盘管理
"""

from pathlib import Path

from loguru import logger
from telethon import Button

from core.config import config
from telegram.handlers.utils import format_size, format_time
from telegram.state_manager import state_manager

# 分页缓存 (chat_id -> list of files)
_page_cache: dict[str, list] = {}


def _files_keyboard(has_files=True):
    kb = [
        [Button.inline("📂 列出文件", b"files:list")],
        [Button.inline("🗑 按关键词删除", b"files:delete_kw")],
        [Button.inline("🧹 清空所有", b"files:clear")],
        [Button.inline("🖼 缩略图缓存", b"files:thumbs")],
    ]
    return kb


def _list_keyboard(page: int, total_pages: int, has_files: bool):
    kb = []
    if has_files and total_pages > 1:
        nav = []
        if page > 1:
            nav.append(Button.inline("⬅️ 上一页", f"files:page:{page - 1}".encode()))
        if page < total_pages:
            nav.append(Button.inline("下一页 ➡️", f"files:page:{page + 1}".encode()))
        if nav:
            kb.append(nav)
    kb.append([Button.inline("🔙 返回", b"files:menu")])
    return kb


def _thumbs_keyboard():
    return [
        [Button.inline("🗑 清空缓存", b"files:thumbs_clear")],
        [Button.inline("🔙 返回", b"files:menu")],
    ]


def _clear_confirm_keyboard():
    return [
        [Button.inline("⚠️ 确认清空", b"files:clear_confirm")],
        [Button.inline("🔙 返回", b"files:menu")],
    ]


async def storage_handler(event, arg=None):
    """内联键盘主菜单"""
    downloads_dir = Path(config.save_path).expanduser().resolve()

    # 兼容旧文本命令
    if arg:
        import asyncio
        return await _legacy_handler(event, arg)

    if not downloads_dir.exists():
        await event.respond(
            "📂 **本地文件管理**\n\n下载目录不存在。",
            buttons=_files_keyboard(has_files=False),
        )
        return

    files = sorted(
        [f for f in downloads_dir.iterdir() if f.is_file()],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    total_size = sum(f.stat().st_size for f in files)

    text = (
        f"📂 **本地文件管理**\n\n"
        f"文件数: {len(files)}\n"
        f"总大小: {format_size(total_size)}\n\n"
        f"💡 选择操作："
    )
    await event.respond(text, buttons=_files_keyboard(has_files=bool(files)))


async def storage_callback_handler(event):
    """处理 files: 回调"""
    data = event.data.decode() if isinstance(event.data, bytes) else event.data
    chat_id = str(event.chat_id)
    downloads_dir = Path(config.save_path).expanduser().resolve()

    if data == "files:list":
        if not downloads_dir.exists():
            await event.edit("📂 下载目录不存在。", buttons=_files_keyboard(has_files=False))
            return

        files = sorted(
            [f for f in downloads_dir.iterdir() if f.is_file()],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        _page_cache[chat_id] = files
        await _show_file_page(event, chat_id, files, 0)

    elif data.startswith("files:page:"):
        page = int(data.split(":")[-1]) - 1
        files = _page_cache.get(chat_id, [])
        await _show_file_page(event, chat_id, files, page)

    elif data == "files:delete_kw":
        await state_manager.set(chat_id, {"command": "files_delete", "step": "input"})
        await event.edit(
            "🗑 **按关键词删除**\n\n请输入关键词（匹配文件名）：\n发送 `/cancel` 取消。",
            buttons=None,
        )

    elif data == "files:clear":
        if not downloads_dir.exists():
            await event.edit("📂 下载目录不存在。", buttons=_files_keyboard(has_files=False))
            return
        files = [f for f in downloads_dir.iterdir() if f.is_file()]
        total_size = sum(f.stat().st_size for f in files)
        await event.edit(
            f"⚠️ **确认清空**\n\n将删除 {len(files)} 个文件，释放 {format_size(total_size)}。\n此操作不可撤销。",
            buttons=_clear_confirm_keyboard(),
        )

    elif data == "files:clear_confirm":
        if downloads_dir.exists():
            files = [f for f in downloads_dir.iterdir() if f.is_file()]
            freed = sum(f.stat().st_size for f in files)
            for f in files:
                f.unlink()
            logger.info(f"Cleared all local files via inline: {len(files)} files, {format_size(freed)}")
            await event.edit(
                f"✅ 已清空 {len(files)} 个文件，释放 {format_size(freed)}。",
                buttons=_files_keyboard(has_files=False),
            )
        else:
            await event.edit("📂 下载目录不存在。", buttons=_files_keyboard(has_files=False))

    elif data == "files:thumbs":
        await _show_thumbs_status(event)

    elif data == "files:thumbs_clear":
        from telegram.handlers.thumbnail import THUMB_DIR
        if THUMB_DIR.exists():
            files = [f for f in THUMB_DIR.iterdir() if f.is_file()]
            freed = sum(f.stat().st_size for f in files)
            for f in files:
                f.unlink()
            logger.info(f"Cleared thumb cache via inline: {len(files)} files, {format_size(freed)}")
            await event.edit(
                f"✅ 已清空 {len(files)} 个缩略图，释放 {format_size(freed)}。",
                buttons=_files_keyboard(),
            )
        else:
            await event.edit("🖼️ 缓存目录不存在。", buttons=_files_keyboard())

    elif data == "files:menu":
        if not downloads_dir.exists():
            await event.edit(
                "📂 **本地文件管理**\n\n下载目录不存在。",
                buttons=_files_keyboard(has_files=False),
            )
            return
        files = [f for f in downloads_dir.iterdir() if f.is_file()]
        total_size = sum(f.stat().st_size for f in files)
        await event.edit(
            f"📂 **本地文件管理**\n\n文件数: {len(files)}\n总大小: {format_size(total_size)}\n\n💡 选择操作：",
            buttons=_files_keyboard(has_files=bool(files)),
        )


async def _show_file_page(event, chat_id, files, page):
    """分页显示文件列表"""
    per_page = 15
    total_pages = (len(files) + per_page - 1) // per_page if files else 1
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = files[start:start + per_page]

    if not files:
        await event.edit(
            "📂 本地下载目录为空。",
            buttons=_files_keyboard(has_files=False),
        )
        return

    lines = [f"📂 文件 ({page + 1}/{total_pages}, 共 {len(files)} 个)"]
    for i, f in enumerate(chunk, start + 1):
        stat = f.stat()
        size = format_size(stat.st_size)
        mtime = format_time(stat.st_mtime)
        marker = "🗑️ " if f.name.startswith("[DEL]") else ""
        lines.append(f"{i}. {marker}{f.name}  _{size}, {mtime}_")

    await event.edit(
        "\n".join(lines),
        buttons=_list_keyboard(page + 1, total_pages, bool(files)),
    )


async def _show_thumbs_status(event):
    """显示缩略图缓存状态"""
    from telegram.handlers.thumbnail import THUMB_DIR

    if not THUMB_DIR.exists():
        await event.edit("🖼️ 缩略图缓存目录不存在。", buttons=_files_keyboard())
        return

    files = [f for f in THUMB_DIR.iterdir() if f.is_file()]
    size = sum(f.stat().st_size for f in files)
    text = (
        f"🖼️ **缩略图缓存**\n\n"
        f"文件数: {len(files)}\n"
        f"占用: {format_size(size)}\n"
        f"目录: `{THUMB_DIR}`"
    )
    await event.edit(text, buttons=_thumbs_keyboard())


# ── FSM 处理 ──

async def handle_files_delete(event, state):
    """FSM: 按关键词删除文件"""
    text = event.text.strip()
    chat_id = str(event.chat_id)

    if text.lower() == "/cancel":
        await state_manager.clear(chat_id)
        await event.respond("❌ 已取消。", buttons=_files_keyboard())
        return

    downloads_dir = Path(config.save_path).expanduser().resolve()
    if not downloads_dir.exists():
        await state_manager.clear(chat_id)
        await event.respond("📂 下载目录不存在。", buttons=_files_keyboard())
        return

    files = [f for f in downloads_dir.iterdir() if f.is_file()]
    matched = [f for f in files if text in f.name]

    if not matched:
        await state_manager.clear(chat_id)
        await event.respond(
            f"❌ 没有匹配 `{text}` 的文件。",
            buttons=_files_keyboard(),
        )
        return

    freed = 0
    for f in matched:
        freed += f.stat().st_size
        f.unlink()
        logger.info(f"Deleted local file via inline: {f.name}")

    await state_manager.clear(chat_id)
    await event.respond(
        f"🗑️ 已删除 {len(matched)} 个匹配 `{text}` 的文件，释放 {format_size(freed)}。",
        buttons=_files_keyboard(),
    )


# ── 旧文本命令兼容 ──

async def _legacy_handler(event, arg: str):
    """兼容旧版文本子命令"""
    downloads_dir = Path(config.save_path).expanduser().resolve()

    if arg.strip().startswith("thumbs"):
        from telegram.handlers.thumbnail import THUMB_DIR
        parts = arg.strip().split()
        sub = parts[1] if len(parts) > 1 else ""

        if not THUMB_DIR.exists():
            await event.respond("🖼️ 缩略图缓存目录不存在。")
            return
        files = list(THUMB_DIR.iterdir())
        total = len(files)
        size = sum(f.stat().st_size for f in files if f.is_file())

        if sub == "clear":
            for f in files:
                if f.is_file():
                    f.unlink()
            await event.respond(f"🗑️ 已清空 {total} 个缩略图缓存。")
            return

        await event.respond(f"🖼️ 缓存文件: {total}, 占用: {format_size(size)}")
        return

    if not downloads_dir.exists():
        await event.respond("📂 下载目录不存在。")
        return

    files = sorted(
        [f for f in downloads_dir.iterdir() if f.is_file()],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    parts = arg.strip().split(maxsplit=1)
    action = parts[0].lower()

    if action == "delete":
        pattern = parts[1] if len(parts) > 1 else "[DEL]"
        matched = [f for f in files if pattern in f.name]
        if not matched:
            await event.respond(f"❌ 没有匹配 `{pattern}` 的文件。")
            return
        freed = sum(f.stat().st_size for f in matched)
        for f in matched:
            f.unlink()
        await event.respond(f"🗑️ 已删除 {len(matched)} 个匹配文件，释放 {format_size(freed)}。")

    elif action == "clear":
        freed = sum(f.stat().st_size for f in files)
        for f in files:
            f.unlink()
        await event.respond(f"🗑️ 已清空所有文件，释放 {format_size(freed)}。")

    else:
        await event.respond("❌ 使用 `/files` 打开内联键盘管理界面。")
