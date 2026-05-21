from core.database import db_manager
from downloader.manager import download_manager
from telegram import search as search_module
from telegram.handlers.utils import parse_indices
from telegram.search_cache import search_cache
from telegram.state_manager import state_manager


async def batch_download_handler(event, arg=None):
    last_results = search_cache.get(event.chat_id)

    messages_to_download = []
    if not arg:
        messages_to_download = last_results
    else:
        # 解析范围 "1-3, 5"
        try:
            indices = parse_indices(arg)
            if not last_results:
                await event.respond("❌ 请先进行搜索。")
                return
            for idx in sorted(list(indices)):
                if 1 <= idx <= len(last_results):
                    messages_to_download.append(last_results[idx-1])
        except Exception as e:
            await event.respond(f"❌ 解析范围出错: {str(e)}")
            return

    if not messages_to_download:
        await event.respond("❌ 没有找到匹配内容。")
        return

    await event.respond(f"📥 正在将 {len(messages_to_download)} 个任务加入队列...")
    count = await search_module.searcher.batch_add_tasks(messages_to_download, str(event.chat_id))
    await event.respond(f"✅ 成功添加 {count} 个下载任务。")

async def batch_download_formats_handler(event, arg=None):
    # 此处逻辑较复杂，常需分步，所以 arg 可能包含格式
    if not arg:
        await event.respond("🧩 请输入格式列表（如: mp4, mp3）：")
        await state_manager.set(event.chat_id, {'command': 'bdf', 'step': 'formats'})
        return
    # 如果有 arg，尝试解析 "mp4 1-5"
    parts = arg.split(maxsplit=1)
    formats_str = parts[0]
    indices_str = parts[1] if len(parts) > 1 else None
    await do_bdf(event, formats_str, indices_str)

async def download_list_handler(event, arg=None):
    page = int(arg) if arg and arg.isdigit() else 1
    res = await db_manager.get_download_list(page, 10)

    if not res['items']:
        await event.respond("📭 下载队列为空。")
        return

    response = f"📋 **下载队列 (第 {page} 页):**\n\n"
    for task in res['items']:
        status_emoji = "⏳" if task['status'] == 'pending' else "🚀" if task['status'] == 'downloading' else "❌"
        response += f"{status_emoji} `{task['file_name']}`\n  状态: `{task['status']}` | 进度: `{task['progress'] or 0}%` | ID: `{task['task_id']}`\n\n"

    total_pages = (res['total'] + 9) // 10
    if total_pages > 1:
        response += f"页码: {page}/{total_pages}\n💡 使用 `/dl [页码]` 查看更多。"

    await event.respond(response)

async def cancel_handler(event, arg=None):
    chat_id = str(event.chat_id)
    # 取消所有待处理/失败/下载中的任务，设满重试次数防止反复重试
    await download_manager.cancel_user_tasks(chat_id)
    # 清除内存中活跃任务标记，让 worker 重新取队列
    download_manager.active_tasks.clear()
    await download_manager.wake_workers()
    await event.respond("🚫 已取消所有下载任务。")

async def clear_cache_handler(event, arg=None):
    search_cache.clear(event.chat_id)
    await event.respond("🧹 已清理搜索结果缓存。")

async def do_bdf(event, formats_str, indices_str=None):
    last_results = search_cache.get(event.chat_id)
    if not last_results:
        await event.respond("❌ 请先进行搜索。")
        return
    formats = [f.strip() for f in formats_str.replace('，', ',').split(',')]
    messages_to_download = []
    if not indices_str:
        messages_to_download = last_results
    else:
        indices = parse_indices(indices_str)
        for idx in sorted(indices):
            if 1 <= idx <= len(last_results):
                messages_to_download.append(last_results[idx-1])

    count = await search_module.searcher.batch_add_tasks(messages_to_download, str(event.chat_id), formats=formats)
    await event.respond(f"✅ 成功添加 {count} 个匹配格式的下载任务。")
