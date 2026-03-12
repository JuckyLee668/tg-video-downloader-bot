from telethon import events
from telegram.client import tg_clients
from telegram.search_cache import search_cache
from telegram.state_manager import state_manager
from downloader.manager import download_manager

async def batch_forward_handler(event, arg=None):
    # arg: "target indices" or "target"
    if not arg:
        await event.respond("📤 请输入目标聊天（ID 或 @username）")
        state_manager.set(event.chat_id, {'command': 'bf', 'step': 'target'})
        return
        
    parts = arg.split(maxsplit=1)
    target = parts[0]
    indices_str = parts[1] if len(parts) > 1 else None
    
    last_results = search_cache.get(event.chat_id)
    if not last_results:
        await event.respond("❌ 请先进行搜索。")
        return

    # 这里可以继续引导进入 delete_after 确认
    state_manager.set(event.chat_id, {
        'command': 'bf', 
        'step': 'delete', 
        'target': target, 
        'indices': indices_str
    })
    await event.respond("🗑️ 转发完成后是否删除本地文件？回复 yes/no（默认 yes）。")

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
        
    last_results = search_cache.get(event.chat_id)
    if not last_results:
        await event.respond("❌ 请先搜索。")
        return
        
    from telegram.handlers.download import _parse_indices
    messages_to_forward = []
    if not indices_str or indices_str.lower() == 'all':
        messages_to_forward = last_results
    else:
        indices = _parse_indices(indices_str)
        for idx in sorted(indices):
            if 1 <= idx <= len(last_results):
                messages_to_forward.append(last_results[idx-1])

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
        file_name = _get_file_name(msg)
        display_name = f"[DEL]{file_name}" if delete_after else file_name
        
        task = {
            'chat_id': msg.chat_id,
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

def _get_file_name(msg):
    if msg.file and msg.file.name:
        return msg.file.name
    
    mime = getattr(msg.file, 'mime_type', '') if msg.file else ''
    ext = '.mp4' if 'video' in mime else '.mp3' if 'audio' in mime else '.jpg' if 'image' in mime else ''
    return f"media_{msg.id}{ext}"
