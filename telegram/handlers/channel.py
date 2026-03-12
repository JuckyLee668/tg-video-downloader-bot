from telethon import events
from core.database import db_manager
from telegram.handlers.utils import ensure_searcher
from telegram.state_manager import state_manager
from telegram import search as search_module

async def connect_channel_handler(event, arg=None):
    if not await ensure_searcher(event): return
    
    identifier = arg
    if not identifier:
        await event.respond("🔗 请输入要连接的频道用户名或邀请链接：")
        state_manager.set(event.chat_id, {'command': 'cc'})
        return

    await event.respond(f"⏳ 正在尝试连接频道: `{identifier}`...")
    try:
        info = await search_module.searcher.connect_channel(identifier)
        await event.respond(f"✅ 已成功连接到频道:\n**{info['title']}** (@{info['username'] or 'N/A'})\nID: `{info['id']}`")
    except Exception as e:
        await event.respond(f"❌ 连接失败: {str(e)}")

async def channels_list_handler(event, arg=None):
    channels = await db_manager.get_connected_channels()
    if not channels:
        await event.respond("📭 尚未连接任何频道。")
        return
        
    response = "📺 **已连接/已保存频道:**\n\n"
    for ch in channels:
        response += f"• **{ch['title']}** (@{ch['username'] or 'N/A'})\n  ID: `{ch['channel_id']}`\n"
    await event.respond(response)
