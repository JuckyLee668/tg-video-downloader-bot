"""统一的视频操作交互 — 所有来源（Telegram/Twitter）共用。

流程：
  autofwd 已启用？→ 直接执行默认操作 + 提示
  autofwd 未启用？→ 展示预览 → 询问 1️⃣2️⃣3️⃣4️⃣
"""

from core.config import config
from downloader.manager import download_manager
from telegram.state_manager import state_manager


def _format_duration(seconds: float) -> str:
    if not seconds:
        return "未知"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _truncate_filename(name: str, max_bytes: int = 200) -> str:
    """Truncate a string so its UTF-8 bytes don't exceed max_bytes.

    Preserves as many whole characters as possible within the limit,
    avoiding filesystem Errno 36 (File name too long).
    """
    encoded = name.encode("utf-8")
    if len(encoded) <= max_bytes:
        return name
    # Walk back from the cutoff to find a valid UTF-8 boundary
    truncated = encoded[:max_bytes]
    for cut in range(len(truncated), max(0, len(truncated) - 4), -1):
        try:
            return truncated[:cut].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return name[: max_bytes // 3]  # fallback for extreme all-multibyte input


def _format_size(size: int) -> str:
    if not size:
        return "未知"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


async def handle_media_action(event, info: dict, source_type: str = "telegram", source_data: dict = None):
    """统一入口：收到任何视频后决定操作。

    info 需包含:
        title: str          视频标题/文件名
        duration: float     时长(秒), 0=未知
        resolution: str     如 "1920x1080"
        filesize: int       文件大小(字节)
        uploader: str       来源作者(可选)
        ext: str            扩展名(可选)
        source_url: str     源URL(Twitter用)

    source_type: "telegram" | "twitter"
    source_data: 额外入队数据
    """
    da = config.default_action

    if da.enabled and da.action:
        # 自动模式：不询问，直接执行
        await _execute_auto(event, info, source_type, source_data, da)
    else:
        # 交互模式：展示预览 + 询问
        await _show_preview_and_ask(event, info, source_type, source_data)


async def _execute_auto(event, info: dict, source_type: str, source_data: dict, da):
    """自动模式：按默认配置直接入队。"""
    action = da.action
    await state_manager.clear(event.chat_id)

    target = da.target_chat if action in ("forward", "all") else ""
    await _enqueue_task(event, info, source_type, source_data, action, target)

    labels = {
        "download": f"📥 默认: 下载到本地\n🎬 `{info['title']}`",
        "forward": f"📤 默认: 下载并转发至 `{target}`\n🎬 `{info['title']}`",
        "cloud": f"☁️ 默认: 下载并上传云盘\n🎬 `{info['title']}`",
        "all": f"🔄 默认: 全部（下载+转发至 `{target}`+云盘）\n🎬 `{info['title']}`",
    }
    await event.respond(labels.get(action, f"✅ 已加入队列\n🎬 `{info['title']}`"))


async def _show_preview_and_ask(event, info: dict, source_type: str, source_data: dict):
    """交互模式：展示预览，询问操作。"""
    filesize = info.get("filesize", 0) or 0
    is_large = filesize > 2000 * 1024 * 1024

    text = (
        f"🎬 **{info['title']}**\n\n"
        f"⏱ 时长：{_format_duration(info['duration'])}\n"
        f"📐 分辨率：{info.get('resolution', '未知')}\n"
        f"📦 大小：{_format_size(filesize)}\n"
        f"👤 来源：{info.get('uploader') or '未知'}\n\n"
        f"请选择操作：\n"
        f"1️⃣  仅下载到本地\n"
        f"2️⃣  下载并转发到频道\n"
        f"3️⃣  下载并上传云盘\n"
        f"4️⃣  全部（下载 + 转发 + 云盘）\n"
    )
    if is_large:
        text += (
            f"⚠️ 此文件超过 2GB，非 Premium 账户转发/上传将受限\n"
            f"5️⃣  压缩后下载（用 ffmpeg 缩小至 2GB 以内）\n"
        )
    text += f"\n回复数字 {'1-5' if is_large else '1-4'} 进行选择。"

    try:
        if info.get("thumbnail"):
            await event.respond(text, link_preview=True)
        else:
            await event.respond(text)
    except Exception:
        await event.respond(text)

    from loguru import logger
    logger.info(f"Setting action state for chat_id={event.chat_id}, step=choose")
    await state_manager.set(event.chat_id, {
        "command": "action",
        "step": "choose",
        "info": info,
        "source_type": source_type,
        "source_data": source_data or {},
    })


async def handle_action_choice(event, state):
    """处理操作选择（FSM step='choose'）"""
    choice = event.text.strip()
    info = state["info"]
    source_type = state.get("source_type", "telegram")
    source_data = state.get("source_data", {})

    if choice == "1":
        await state_manager.clear(event.chat_id)
        await _enqueue_task(event, info, source_type, source_data, "download")
    elif choice == "2":
        await state_manager.update(event.chat_id, step="forward_target", action="forward")
        await event.respond("📤 请输入转发目标（ID、@username 或链接）：")
    elif choice == "3":
        await state_manager.clear(event.chat_id)
        await _enqueue_task(event, info, source_type, source_data, "cloud")
    elif choice == "4":
        await state_manager.update(event.chat_id, step="forward_target", action="all")
        await event.respond("📤 请输入转发目标（ID、@username 或链接）：")
    elif choice == "5":
        await state_manager.clear(event.chat_id)
        await _enqueue_task(event, info, source_type, source_data, "download", compress=True)
    else:
        await event.respond("⚠️ 请回复数字 1-4 进行选择。")


async def handle_action_target(event, state):
    """处理转发目标输入（FSM step='forward_target'）"""
    target = event.text.strip()
    info = state["info"]
    source_type = state.get("source_type", "telegram")
    source_data = state.get("source_data", {})
    action = state.get("action", "forward")
    await state_manager.clear(event.chat_id)
    await _enqueue_task(event, info, source_type, source_data, action, target)


async def _enqueue_task(event, info: dict, source_type: str, source_data: dict,
                        action: str, forward_target: str = "", compress: bool = False):
    """统一入队。"""

    title = info.get("title", "未知") or "未知"
    ext = info.get("ext", "mp4") or "mp4"
    # Truncate long titles (e.g. full tweet text) to avoid filesystem name-too-long errors
    safe_title = _truncate_filename(title, max_bytes=200)
    file_name = f"{safe_title}.{ext}"

    task_data: dict = dict(source_data or {})
    task_data["requester_chat_id"] = event.chat_id
    task_data["action"] = action

    if compress:
        task_data["compress"] = True

    if forward_target:
        task_data["forward_target"] = str(forward_target)
        task_data["delete_after_forward"] = True

    if source_type == "twitter":
        import hashlib
        url_hash = hashlib.md5((info.get("source_url") or "").encode()).hexdigest()[:12]
        task_data["source_type"] = "external"
        task_data["source_url"] = info.get("source_url", "")
        task_data["external_info"] = info
        task_id_prefix = url_hash
    else:
        task_id_prefix = str(info.get("message_id", event.message.id if event.message else 0))

    # source_data may be None when called from Twitter/X auto-detect flow
    _sd = source_data or {}
    task = {
        "chat_id": str(event.chat_id),
        "message_id": task_id_prefix,
        "file_name": file_name,
        "media_type": "video",
        "file_size": info.get("filesize", 0) or 0,
        "channel_id": _sd.get("channel_id", str(event.chat_id)),
        "task_data": task_data,
    }

    result = await download_manager.add_task(task)
    if result == "duplicate":
        await event.respond(f"⚠️ 已下载过，跳过：\n🎬 `{file_name}`")
        return

    labels = {
        "download": f"📥 已加入队列：下载到本地\n🎬 `{file_name}`",
        "forward": f"📤 已加入队列：下载并转发\n🎬 `{file_name}`",
        "cloud": f"☁️ 已加入队列：下载并上传云盘\n🎬 `{file_name}`",
        "all": f"🔄 已加入队列：下载+转发+云盘\n🎬 `{file_name}`",
        "compress": f"🗜️ 已加入队列：下载并压缩\n🎬 `{file_name}`",
    }
    await event.respond(labels.get(action, f"✅ 已加入队列\n🎬 `{file_name}`"))
