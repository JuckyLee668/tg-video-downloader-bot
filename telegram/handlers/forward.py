from downloader.manager import download_manager
from telegram.client import tg_clients
from telegram.handlers.utils import message_file_name, parse_indices
from telegram.search_cache import search_cache
from telegram.state_manager import state_manager


async def batch_forward_handler(event, arg=None):
    # 先问序号范围，再问目标
    last_results = search_cache.get(event.chat_id)
    if not last_results:
        await event.respond("❌ 请先进行搜索。")
        return

    if not arg or arg.strip().lower() == 'all':
        indices_str = 'all'
    else:
        # 校验是否有效的序号格式
        try:
            parse_indices(arg)
            indices_str = arg
        except Exception:
            await event.respond("📤 请输入序号范围（如 `1-5,8` 或 `all`）：")
            await state_manager.set(event.chat_id, {'command': 'bf', 'step': 'indices'})
            return

    await state_manager.set(event.chat_id, {
        'command': 'bf',
        'step': 'target',
        'indices': indices_str,
    })
    await event.respond("📤 请输入目标聊天（ID 或 @username）：")


async def forward_link_handler(event, arg=None):
    # 处理 /forward [link]
    link = arg
    if not link:
        await event.respond("🔗 请提供消息链接。")
        return

    try:
        msg = await tg_clients.user_client.get_messages(link)
        if msg and msg.media:
            from telegram import search as search_module
            await search_module.searcher.batch_add_tasks([msg], str(event.chat_id))
            await event.respond("✅ 链接消息已加入下载队列。")
        else:
            await event.respond("❌ 链接无效或无媒体。")
    except Exception as e:
        await event.respond(f"❌ 获取失败: {e}")


async def do_bf(event, target, indices_str=None, delete_after=False):
    if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
        await event.respond("❌ 用户客户端未登录。")
        return

    # 预校验转发目标是否有效
    try:
        await download_manager._resolve_forward_peer(tg_clients.user_client, target)
    except Exception as e:
        await event.respond(f"❌ 目标无效: {e}\n请使用 @username 或聊天 ID（如 -1001234567890）")
        return

    last_results = search_cache.get(event.chat_id)
    if not last_results:
        await event.respond("❌ 请先搜索。")
        return

    messages_to_forward = []
    if not indices_str or indices_str.lower() == 'all':
        messages_to_forward = last_results
    else:
        indices = parse_indices(indices_str)
        for idx in sorted(indices):
            if 1 <= idx <= len(last_results):
                messages_to_forward.append(last_results[idx - 1])

    if not messages_to_forward:
        await event.respond("❌ 未找到匹配消息。")
        return

    # 2GB 限制预检
    large_files = [m for m in messages_to_forward if m.file and m.file.size > 2000 * 1024 * 1024]
    if large_files:
        me = await tg_clients.user_client.get_me()
        if not getattr(me, 'premium', False):
            await event.respond(f"⚠️ 检测到 {len(large_files)} 个文件超过 2GB，非会员无法转发。")
            return

    added = 0
    for msg in messages_to_forward:
        file_name = message_file_name(msg)
        display_name = f"[DEL]{file_name}" if delete_after else file_name

        task = {
            'chat_id': str(event.chat_id),
            'message_id': msg.id,
            'file_name': display_name,
            'media_type': msg.media.__class__.__name__ if msg.media else 'unknown',
            'file_size': msg.file.size if msg.file else 0,
            'channel_id': msg.chat_id,
            'channel_username': getattr(msg.chat, 'username', '') if msg.chat else '',
            'channel_title': getattr(msg.chat, 'title', '') if msg.chat else '',
            'task_data': {
                'original_file_name': file_name,
                'forward_target': str(target),
                'delete_after_forward': delete_after,
                'caption': msg.message or "",
                'access_hash': getattr(msg.chat, 'access_hash', None),
                'requester_chat_id': event.chat_id
            }
        }
        await download_manager.add_task(task)
        added += 1

    await event.respond(f"📥 已添加 {added} 个转发任务到队列。")
