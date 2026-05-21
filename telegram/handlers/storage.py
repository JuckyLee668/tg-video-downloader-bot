from pathlib import Path

from loguru import logger

from core.config import config
from telegram.handlers.utils import format_size, format_time


async def storage_handler(event, arg: str | None):
    """
    /files  - 列出本地下载目录中的所有文件
    /files delete <pattern> - 删除匹配的文件（如 /files delete [DEL]）
    /files clear - 清空所有本地下载文件
    /files thumbs - 查看缩略图缓存状态
    /files thumbs clear - 清空缩略图缓存
    """
    downloads_dir = Path(config.save_path).expanduser().resolve()

    # 缩略图缓存子命令
    if arg and arg.strip().startswith("thumbs"):
        return await _thumbs_handler(event, arg.strip())

    if not downloads_dir.exists():
        await event.respond("📂 下载目录不存在。")
        return

    files = sorted(
        [f for f in downloads_dir.iterdir() if f.is_file()],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    total_size = sum(f.stat().st_size for f in files)
    total_size_str = format_size(total_size)

    if not arg:
        # 列出文件
        if not files:
            await event.respond("📂 本地下载目录为空。")
            return

        lines = [f"📂 **本地下载文件** ({len(files)} 个, 共 {total_size_str})"]
        for i, f in enumerate(files, 1):
            stat = f.stat()
            size = format_size(stat.st_size)
            mtime = format_time(stat.st_mtime)
            marker = "🗑️ " if f.name.startswith("[DEL]") else ""
            lines.append(f"{i}. {marker}{f.name}  _{size}, {mtime}_")
        lines.append("")
        lines.append("💡 使用 `/files delete <关键词>` 删除匹配文件")
        lines.append("💡 使用 `/files clear` 清空所有文件")

        for chunk in _chunk_lines(lines, 20):
            await event.respond("\n".join(chunk))
        return

    parts = arg.strip().split(maxsplit=1)
    action = parts[0].lower()

    if action == "delete":
        pattern = parts[1] if len(parts) > 1 else "[DEL]"
        matched = [f for f in files if pattern in f.name]
        if not matched:
            await event.respond(f"❌ 没有匹配 `{pattern}` 的文件。")
            return

        freed = 0
        for f in matched:
            freed += f.stat().st_size
            f.unlink()
            logger.info(f"Deleted local file: {f.name}")

        await event.respond(
            f"🗑️ 已删除 {len(matched)} 个匹配 `{pattern}` 的文件，释放 {format_size(freed)}。"
        )

    elif action == "clear":
        if not files:
            await event.respond("📂 目录已经空了。")
            return

        freed = sum(f.stat().st_size for f in files)
        for f in files:
            f.unlink()
        logger.info(f"Cleared all local files, freed {format_size(freed)}")

        await event.respond(f"🗑️ 已清空所有本地文件，释放 {format_size(freed)}。")

    else:
        await event.respond("❌ 用法：`/files`、`/files delete <关键词>`、`/files clear`、`/files thumbs`")


async def _thumbs_handler(event, arg: str):
    """处理缩略图缓存子命令"""
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
        logger.info(f"Cleared {total} thumb cache files, freed {format_size(size)}")
        await event.respond(f"🗑️ 已清空 {total} 个缩略图缓存，释放 {format_size(size)}。")
        return

    # 默认：显示缓存状态
    await event.respond(
        f"🖼️ **缩略图缓存**\n"
        f"• 文件数: {total}\n"
        f"• 占用: {format_size(size)}\n"
        f"• 缓存目录: `{THUMB_DIR}`\n\n"
        f"💡 使用 `/files thumbs clear` 清空缓存"
    )


def _chunk_lines(lines: list[str], chunk_size: int):
    for i in range(0, len(lines), chunk_size):
        yield lines[i : i + chunk_size]
