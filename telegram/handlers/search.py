import asyncio
from datetime import datetime

from core.config import config
from core.database import db_manager
from telegram import search as search_module
from telegram.client import tg_clients
from telegram.handlers.thumbnail import cleanup_old_thumbs, generate_thumbnails, send_thumbnails
from telegram.handlers.utils import ensure_searcher, message_file_info, parse_indices
from telegram.search_cache import search_cache
from telegram.state_manager import state_manager


def _autofwd_hint() -> str:
    """Return the post-search hint based on default_action config."""
    da = config.default_action
    if not (da.enabled and da.action):
        return (
            "💡 用法：\n"
            "/download [序号] 下载（默认全部）\n"
            "/download format 格式 按格式下载\n"
            "/forward [序号] 转发\n"
            "/forward to 目标 [序号] 指定目标转发"
        )

    action_labels = {
        "download": "下载到本地",
        "forward": f"下载并转发至 `{da.target_chat}`",
        "cloud": "下载并上传云盘",
        "all": f"下载+转发至 `{da.target_chat}`+云盘",
    }
    label = action_labels.get(da.action, da.action)
    return (
        f"⚡ 默认操作: {label}\n"
        f"💡 回复序号选择文件（如 `1-5,8,10` 或 `all`），将自动按默认操作处理\n"
        f"或使用 /download /forward 手动操作"
    )


def _build_hint_with_state(messages, chat_id: int) -> str:
    """Set FSM state so user can reply with indices, and return the hint text."""
    da = config.default_action
    if not (da.enabled and da.action):
        return _autofwd_hint()

    # Save the search action config in state so the handler knows what to do
    asyncio.create_task(
        state_manager.set(chat_id, {
            "command": "search_select",
            "step": "indices",
            "action": da.action,
            "target": da.target_chat if da.action in ("forward", "all") else "",
        })
    )
    return _autofwd_hint()


async def handle_search_select(event, state):
    """FSM handler: user replied with indices after a search (autofwd enabled)."""
    indices_str = event.text.strip()
    action = state.get("action", "download")
    target = state.get("target", "")

    messages = search_cache.get(event.chat_id)
    if not messages:
        await state_manager.clear(event.chat_id)
        await event.respond("❌ 搜索结果已过期，请重新搜索。")
        return

    # Parse indices
    if indices_str.lower() == "all":
        selected = messages
    else:
        try:
            idx_set = parse_indices(indices_str)
        except Exception:
            await event.respond("⚠️ 格式错误，请使用如 `1-5,8,10` 或 `all`")
            return

        selected = []
        for idx in sorted(idx_set):
            if 1 <= idx <= len(messages):
                selected.append(messages[idx - 1])

    if not selected:
        await state_manager.clear(event.chat_id)
        await event.respond("❌ 没有匹配的文件。")
        return

    await state_manager.clear(event.chat_id)

    # Enqueue selected messages with default action
    from downloader.manager import download_manager

    count = 0
    for msg in selected:
        if not msg or not msg.media:
            continue
        file_name, media_type = message_file_info(msg)
        if media_type not in config.media_types:
            continue

        task_data = {
            "requester_chat_id": str(event.chat_id),
            "action": action,
            "caption": msg.message or "",
            "date": msg.date.isoformat() if msg.date else "",
            "access_hash": getattr(msg.chat, "access_hash", None),
        }
        if target:
            task_data["forward_target"] = str(target)
            task_data["delete_after_forward"] = True

        task = {
            "chat_id": str(event.chat_id),
            "message_id": str(msg.id),
            "file_name": file_name,
            "media_type": media_type,
            "file_size": msg.file.size if msg.file else 0,
            "channel_id": str(msg.chat_id),
            "channel_title": getattr(msg.chat, "title", "") if msg.chat else "",
            "task_data": task_data,
        }
        await download_manager.add_task(task)
        count += 1

    action_labels = {
        "download": "📥 下载到本地",
        "forward": f"📤 下载并转发至 `{target}`",
        "cloud": "☁️ 下载并上传云盘",
        "all": f"🔄 全部（下载+转发至 `{target}`+云盘）",
    }
    label = action_labels.get(action, action)
    await event.respond(f"⚡ {label}\n📊 已将 {count} 个任务加入队列")


async def search_keyword_handler(event, arg=None):
    if not await ensure_searcher(event):
        return

    keyword = arg
    if not keyword:
        await event.respond("🔍 请输入要搜索的关键词：")
        await state_manager.set(event.chat_id, {'command': 'csk'})
        return

    await event.respond(f"🔍 正在搜索关键词: `{keyword}`...")
    try:
        messages = await search_module.searcher.search_keyword(keyword)
        search_cache.set(event.chat_id, messages)

        if not messages:
            await event.respond("📭 未找到相关媒体消息。")
            return

        response = f"🔍 **找到 {len(messages)} 条媒体消息:**\n\n"
        for i, msg in enumerate(messages[:20]):
            name = msg.file.name or f"media_{msg.id}"
            response += f"`{i+1}.` {name} (ID: `{msg.id}`)\n"

        if len(messages) > 20:
            response += f"\n... 以及另外 {len(messages)-20} 条消息。"
        await event.respond(response)

        # 缩略图打包成一个相册
        await asyncio.sleep(0.3)
        if tg_clients.user_client and await tg_clients.user_client.is_user_authorized():
            await cleanup_old_thumbs()
            thumb_items = await generate_thumbnails(tg_clients.user_client, messages[:20])
            if thumb_items:
                await send_thumbnails(event, thumb_items)

        # Hint — auto-select mode when autofwd enabled
        hint = _build_hint_with_state(messages, event.chat_id)
        await event.respond(hint)
    except Exception as e:
        await event.respond(f"❌ 搜索出错: {str(e)}")


async def search_recent_handler(event, arg=None):
    if not await ensure_searcher(event):
        return

    count = int(arg) if arg and arg.isdigit() else 50
    await event.respond(f"🔍 正在获取最近 {count} 条消息中的媒体...")

    try:
        messages = await search_module.searcher.get_recent(count)
        search_cache.set(event.chat_id, messages)

        if not messages:
            await event.respond("📭 未找到相关媒体消息。")
            return

        response = f"🔍 **找到 {len(messages)} 条媒体消息:**\n\n"
        for i, msg in enumerate(messages[:20]):
            name = msg.file.name or f"media_{msg.id}"
            response += f"`{i+1}.` {name}\n"

        await event.respond(response)

        hint = _build_hint_with_state(messages, event.chat_id)
        await event.respond(hint)
    except Exception as e:
        await event.respond(f"❌ 获取出错: {str(e)}")


async def search_time_handler(event, arg=None):
    if not await ensure_searcher(event):
        return

    # 解析日期 "2023-01-01 2023-01-02"
    dates = arg.split() if arg else []
    if len(dates) < 2:
        await event.respond("⌛ 请输入开始日期 (YYYY-MM-DD)：")
        await state_manager.set(event.chat_id, {'command': 'cst', 'step': 'start'})
        return

    start_str, end_str = dates[0], dates[1]
    await do_cst(event, start_str, end_str)


async def search_history_handler(event, arg=None):
    keyword = arg
    if not keyword:
        await event.respond("📜 请输入要搜索的历史记录关键词：")
        await state_manager.set(event.chat_id, {'command': 'sh'})
        return

    results = await db_manager.search_history(keyword)
    if not results:
        await event.respond(f"🔍 未找到包含 `{keyword}` 的历史记录。")
        return

    from telegram.handlers.utils import format_size
    response = f"🔍 **搜索历史结果 ({len(results)}):**\n\n"
    for item in results:
        response += f"✅ `{item['file_name']}`\n  时间: `{item['downloaded_at']}` | 大小: `{format_size(item['file_size'] or 0)}` | 频道: `{item['channel_title']}`\n\n"

    await event.respond(response)


async def do_cst(event, start_str, end_str):
    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        await event.respond(f"🔍 正在搜索时间段: `{start_date.date()}` 至 `{end_date.date()}`...")
        messages = await search_module.searcher.search_by_time(start_date, end_date)
        search_cache.set(event.chat_id, messages)

        hint = _build_hint_with_state(messages, event.chat_id)
        await event.respond(hint)
    except Exception as e:
        await event.respond(f"❌ 搜索出错: {str(e)}")
