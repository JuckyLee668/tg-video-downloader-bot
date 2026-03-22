from telethon import events
from datetime import datetime
from telegram.handlers.utils import ensure_searcher
from telegram.search_cache import search_cache
from telegram.state_manager import state_manager
from telegram import search as search_module
from core.database import db_manager

async def search_keyword_handler(event, arg=None):
    if not await ensure_searcher(event): return
    
    keyword = arg
    if not keyword:
        await event.respond("🔍 请输入要搜索的关键词：")
        state_manager.set(event.chat_id, {'command': 'csk'})
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
        
        response += "\n\n💡 用法：/bd [序号范围] 下载（默认全部）；/bdf 格式 [序号范围] 按格式下载（默认全部）；/bf 目标ID [序号范围] 转发（默认全部）。"
        await event.respond(response)
    except Exception as e:
        await event.respond(f"❌ 搜索出错: {str(e)}")

async def search_recent_handler(event, arg=None):
    if not await ensure_searcher(event): return
    
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
        
        response += "\n💡 发送 `/bd` 即可全部下载，或 `/bf` `目标ID` 转发。"
        await event.respond(response)
    except Exception as e:
        await event.respond(f"❌ 获取出错: {str(e)}")

async def search_time_handler(event, arg=None):
    if not await ensure_searcher(event): return
    
    # 解析日期 "2023-01-01 2023-01-02"
    dates = arg.split() if arg else []
    if len(dates) < 2:
        await event.respond("⌛ 请输入开始日期 (YYYY-MM-DD)：")
        state_manager.set(event.chat_id, {'command': 'cst', 'step': 'start'})
        return
        
    start_str, end_str = dates[0], dates[1]
    await do_cst(event, start_str, end_str)

async def search_history_handler(event, arg=None):
    keyword = arg
    if not keyword:
        await event.respond("📜 请输入要搜索的历史记录关键词：")
        state_manager.set(event.chat_id, {'command': 'sh'})
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
        await event.respond(f"✅ 找到 {len(messages)} 条媒体消息。发送 `/bd` 即可全部下载，或 `/bf` `目标ID` 转发。")
    except Exception as e:
        await event.respond(f"❌ 搜索出错: {str(e)}")
