from telethon import events, Button
from loguru import logger
from datetime import datetime
import re
import json
import aiosqlite

from core.config import config
from core.database import db_manager
from downloader.manager import download_manager
from telegram.client import tg_clients
from telegram.limiter import rate_limiter
from telegram import search

# Store last search results globally for batch download
last_search_results = {}

def setup_handlers():
    bot = tg_clients.bot_client
    if not bot:
        return

    # Set Bot Commands Menu
    async def set_bot_commands():
        try:
            from telethon import functions, types
            await bot(functions.bots.SetBotCommandsRequest(
                scope=types.BotCommandScopeDefault(),
                lang_code='en',
                commands=[
                    types.BotCommand(command='start', description='Start & Help'),
                    types.BotCommand(command='status', description='Current download status'),
                    types.BotCommand(command='dl', description='Download queue & history'),
                    types.BotCommand(command='cc', description='Connect to a channel'),
                    types.BotCommand(command='csk', description='Search keyword in channel'),
                    types.BotCommand(command='csr', description='Get recent media from channel'),
                    types.BotCommand(command='bd', description='Batch download from results'),
                    types.BotCommand(command='channels', description='List connected channels'),
                    types.BotCommand(command='login', description='Login to user account'),
                ]
            ))
            logger.info("Bot commands menu has been set up")
        except Exception as e:
            logger.error(f"Failed to set bot commands: {e}")

    # Run command setup in background
    import asyncio
    asyncio.create_task(set_bot_commands())

    # Help & Features
    @bot.on(events.NewMessage(pattern=r'/(start|help|h)'))
    async def help_handler(event):
        help_text = (
            "🤖 **Telegram Media Downloader Bot**\n\n"
            "📋 **下载管理:**\n"
            "• `/download_list` (`/dl`) - 查看队列和历史\n"
            "• `/search_history` (`/sh`) - 搜索下载历史记录\n\n"
            "🔍 **频道搜索:**\n"
            "• `/channel_connect` (`/cc`) - 连接到频道\n"
            "• `/channel_search_keyword` (`/csk`) - 搜索关键词\n"
            "• `/channel_search_time` (`/cst`) - 按时间搜索\n"
            "• `/channel_search_recent` (`/csr`) - 获取最新消息\n\n"
            "📥 **批量操作:**\n"
            "• `/batch_download` (`/bd`) - 批量下载\n"
            "• `/batch_download_formats` (`/bdf`) - 按格式下载\n"
            "• `/batch_forward` (`/bf`) - 批量转发\n"
            "  用法: `/bf 目标ID [序号]`\n\n"
            "📊 **状态监控:**\n"
            "• `/status` (`/s`) - 查看当前下载进度\n\n"
            "📺 **频道管理:**\n"
            "• `/channels` - 列出已连接频道\n"
            "• `/channel_join` - 加入新频道\n"
            "• `/login` - 登录 Telegram 账号\n\n"
            "💡 所有搜索后的批量操作均支持序号范围，如 `1-5, 8, 10`。"
        )
        await event.respond(help_text)

    @bot.on(events.NewMessage(pattern='/features'))
    async def features_handler(event):
        # ... (features_text remains same)
        pass

    # Status
    @bot.on(events.NewMessage(pattern=r'/(status|s)'))
    async def status_handler(event):
        summary = await db_manager.get_stats_summary()
        
        # Get active downloading tasks from DB
        async with aiosqlite.connect(db_manager.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM download_queue WHERE status = 'downloading' LIMIT 10")
            active_tasks = await cursor.fetchall()
            
        status_text = (
            "📊 **当前系统状态:**\n\n"
            f"⏳ 队列等待中: `{summary['pending']}`\n"
            f"📥 正在下载: `{summary['downloading']}`\n"
            f"✅ 已完成历史: `{summary['completed']}`\n"
            f"📦 总计下载大小: `{format_size(summary['total_size'] or 0)}`\n\n"
        )
        
        if active_tasks:
            status_text += "🚀 **活跃下载任务:**\n"
            for task in active_tasks:
                status_text += f"• `{task['file_name']}`: {task['progress'] or 0}%\n"
        else:
            status_text += "💤 当前没有正在运行的任务。"
            
        await event.respond(status_text)

    # Download List
    @bot.on(events.NewMessage(pattern=r'/(download_list|dl)(?: (\d+))?'))
    async def download_list_handler(event):
        page = int(event.pattern_match.group(2) or 1)
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

    # Search History
    @bot.on(events.NewMessage(pattern=r'/(search_history|sh) (.+)'))
    async def search_history_handler(event):
        keyword = event.pattern_match.group(2)
        results = await db_manager.search_history(keyword)
        
        if not results:
            await event.respond(f"🔍 未找到包含 `{keyword}` 的历史记录。")
            return
            
        response = f"🔍 **搜索历史结果 ({len(results)}):**\n\n"
        for item in results:
            response += f"✅ `{item['file_name']}`\n  时间: `{item['downloaded_at']}` | 大小: `{format_size(item['file_size'] or 0)}` | 频道: `{item['channel_title']}`\n\n"
            
        await event.respond(response)

    # Channel Connect
    @bot.on(events.NewMessage(pattern=r'/(channel_connect|cc) (.+)'))
    async def connect_channel_handler(event):
        # ...
        if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
            await event.respond("❌ 用户客户端未登录。请先发送 `/login` 进行登录。")
            return
            
        if not search.searcher:
            from telegram.search import init_searcher
            init_searcher(tg_clients.user_client)
            
        identifier = event.pattern_match.group(2)
        await event.respond(f"⏳ 正在尝试连接频道: `{identifier}`...")
        
        try:
            info = await search.searcher.connect_channel(identifier)
            await event.respond(f"✅ 已成功连接到频道:\n**{info['title']}** (@{info['username'] or 'N/A'})\nID: `{info['id']}`\n\n现在可以使用搜索功能了。")
        except Exception as e:
            await event.respond(f"❌ 连接失败: {str(e)}")

    # Channel Search Keyword
    @bot.on(events.NewMessage(pattern=r'/(channel_search_keyword|csk) (.+)'))
    async def search_keyword_handler(event):
        if not search.searcher:
            from telegram.search import init_searcher
            init_searcher(tg_clients.user_client)
            
        if not await search.searcher.ensure_connected():
            await event.respond("❌ 请先使用 /channel_connect 连接频道。")
            return
            
        keyword = event.pattern_match.group(2)
        await event.respond(f"🔍 正在搜索关键词: `{keyword}`...")
        
        try:
            messages = await search.searcher.search_keyword(keyword)
            last_search_results[event.chat_id] = messages
            
            if not messages:
                await event.respond("📭 未找到相关媒体消息。")
                return
                
            response = f"🔍 **找到 {len(messages)} 条媒体消息:**\n\n"
            for i, msg in enumerate(messages[:20]): # Show top 20
                name = msg.file.name or f"media_{msg.id}"
                response += f"`{i+1}.` {name} (ID: `{msg.id}`)\n"
            
            if len(messages) > 20:
                response += f"\n... 以及另外 {len(messages)-20} 条消息。"
            
            response += "\n\n💡 发送 `/batch_download` (`/bd`) 即可全部下载，或 `/batch_forward` (`/bf`) `目标ID` 转发。"
            await event.respond(response)
        except Exception as e:
            await event.respond(f"❌ 搜索出错: {str(e)}")

    # Channel Search Time
    @bot.on(events.NewMessage(pattern=r'/(channel_search_time|cst) (\d{4}-\d{2}-\d{2}) (\d{4}-\d{2}-\d{2})'))
    async def search_time_handler(event):
        if not search.searcher:
            from telegram.search import init_searcher
            init_searcher(tg_clients.user_client)
            
        if not await search.searcher.ensure_connected():
            await event.respond("❌ 请先使用 /channel_connect 连接频道。")
            return
            
        try:
            start_date = datetime.strptime(event.pattern_match.group(2), "%Y-%m-%d")
            # Set end_date to the end of that day (23:59:59)
            end_date = datetime.strptime(event.pattern_match.group(3), "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            
            await event.respond(f"🔍 正在搜索时间段: `{start_date.date()}` 至 `{end_date.date()}`...")
            messages = await search.searcher.search_by_time(start_date, end_date)
            last_search_results[event.chat_id] = messages
            
            await event.respond(f"✅ 找到 {len(messages)} 条媒体消息。发送 `/batch_download` (`/bd`) 即可全部下载，或 `/batch_forward` (`/bf`) `目标ID` 转发。")
        except Exception as e:
            await event.respond(f"❌ 搜索出错: {str(e)}")

    # Channel Search Recent
    @bot.on(events.NewMessage(pattern=r'/(channel_search_recent|csr)(?: (\d+))?'))
    async def search_recent_handler(event):
        if not search.searcher:
            from telegram.search import init_searcher
            init_searcher(tg_clients.user_client)
            
        if not await search.searcher.ensure_connected():
            await event.respond("❌ 请先使用 /channel_connect 连接频道。")
            return
            
        count = int(event.pattern_match.group(2) or 50)
        await event.respond(f"🔍 正在获取最近 {count} 条消息中的媒体...")
        
        try:
            messages = await search.searcher.get_recent(count)
            last_search_results[event.chat_id] = messages
            
            response = f"🔍 **找到 {len(messages)} 条媒体消息:**\n\n"
            for i, msg in enumerate(messages[:20]):
                name = msg.file.name or f"media_{msg.id}"
                response += f"`{i+1}.` {name}\n"
            
            response += "\n💡 发送 `/batch_download` (`/bd`) 即可全部下载，或 `/batch_forward` (`/bf`) `目标ID` 转发。"
            await event.respond(response)
        except Exception as e:
            await event.respond(f"❌ 获取出错: {str(e)}")

    # Batch Download
    @bot.on(events.NewMessage(pattern=r'/(batch_download|bd)(?: (.+))?'))
    async def batch_download_handler(event):
        arg = event.pattern_match.group(2)
        last_results = last_search_results.get(event.chat_id, [])
        
        messages_to_download = []
        
        if not arg:
            # Download all from last search results
            messages_to_download = last_results
        else:
            # Parse ranges and indices (e.g., "1-3, 5, 10-12")
            try:
                indices = set()
                # Handle both English and Chinese commas
                arg_clean = arg.replace('，', ',')
                parts = arg_clean.split(',')
                for part in parts:
                    part = part.strip()
                    if '-' in part:
                        start_str, end_str = part.split('-')
                        start, end = int(start_str), int(end_str)
                        indices.update(range(start, end + 1))
                    elif part.isdigit():
                        indices.add(int(part))
                
                if not last_results:
                    await event.respond("❌ 请先进行搜索，或直接提供消息 ID。")
                    return
                
                # Map indices to messages (1-based index)
                for idx in sorted(list(indices)):
                    if 1 <= idx <= len(last_results):
                        messages_to_download.append(last_results[idx-1])
            except Exception as e:
                await event.respond(f"❌ 解析范围出错: {str(e)}\n用法示例: `/bd 1-5, 8`")
                return
            
        if not messages_to_download:
            await event.respond("❌ 没有找到匹配的可下载内容。")
            return
            
        await event.respond(f"📥 正在将 {len(messages_to_download)} 个任务加入队列...")
        count = await search.searcher.batch_add_tasks(messages_to_download, str(event.chat_id))
        await event.respond(f"✅ 成功添加 {count} 个下载任务。")

    # Batch Download Formats
    @bot.on(events.NewMessage(pattern=r'/(batch_download_formats|bdf) ([a-zA-Z0-9, ]+)(?: (.+))?'))
    async def batch_download_formats_handler(event):
        formats_str = event.pattern_match.group(2)
        indices_str = event.pattern_match.group(3)
        
        last_results = last_search_results.get(event.chat_id, [])
        if not last_results:
            await event.respond("❌ 请先进行搜索。")
            return

        formats = [f.strip() for f in formats_str.replace('，', ',').split(',')]
        
        messages_to_download = []
        if not indices_str:
            messages_to_download = last_results
        else:
            try:
                indices = set()
                parts = indices_str.replace('，', ',').split(',')
                for part in parts:
                    part = part.strip()
                    if '-' in part:
                        start_str, end_str = part.split('-')
                        indices.update(range(int(start_str), int(end_str) + 1))
                    elif part.isdigit():
                        indices.add(int(part))
                
                for idx in sorted(list(indices)):
                    if 1 <= idx <= len(last_results):
                        messages_to_download.append(last_results[idx-1])
            except Exception as e:
                await event.respond(f"❌ 解析序号出错: {str(e)}")
                return

        if not messages_to_download:
            await event.respond("❌ 没有找到匹配的消息。")
            return
            
        await event.respond(f"📥 正在按格式 `{formats}` 过滤并添加任务...")
        count = await search.searcher.batch_add_tasks(messages_to_download, str(event.chat_id), formats=formats)
        await event.respond(f"✅ 成功添加 {count} 个匹配格式的下载任务。")

    # Batch Forward
    @bot.on(events.NewMessage(pattern=r'/(batch_forward|bf) ([^ ]+)(?: (.+))?'))
    async def batch_forward_handler(event):
        target = event.pattern_match.group(2)
        indices_str = event.pattern_match.group(3)
        last_results = last_search_results.get(event.chat_id, [])
        
        if not last_results:
            await event.respond("❌ 请先进行搜索。")
            return
            
        # Parse indices
        messages_to_forward = []
        if not indices_str:
            messages_to_forward = last_results
        else:
            try:
                indices = set()
                parts = indices_str.replace('，', ',').split(',')
                for part in parts:
                    part = part.strip()
                    if '-' in part:
                        s, e = part.split('-')
                        indices.update(range(int(s), int(e) + 1))
                    elif part.isdigit():
                        indices.add(int(part))
                
                for idx in sorted(list(indices)):
                    if 1 <= idx <= len(last_results):
                        messages_to_forward.append(last_results[idx-1])
            except Exception as e:
                await event.respond(f"❌ 解析序号出错: {str(e)}")
                return
        
        if not messages_to_forward:
            await event.respond("❌ 没有找到匹配的消息。")
            return
            
        await event.respond(f"📤 正在转发 {len(messages_to_forward)} 条消息到 `{target}`...")
        
        # Group by source chat
        by_chat = {}
        for msg in messages_to_forward:
            cid = str(msg.chat_id)
            if cid not in by_chat: by_chat[cid] = []
            by_chat[cid].append(msg.id)
            
        try:
            # Resolve target
            to_peer = target
            if target.replace('-', '').isdigit():
                to_peer = int(target)
            
            total = 0
            for from_chat_id, msg_ids in by_chat.items():
                await search.searcher.forward_messages(int(from_chat_id), msg_ids, to_peer)
                total += len(msg_ids)
                
            await event.respond(f"✅ 成功转发 {total} 条消息。")
        except Exception as e:
            await event.respond(f"❌ 转发失败: {str(e)}")

    # Channels List
    @bot.on(events.NewMessage(pattern='/channels'))
    async def channels_list_handler(event):
        channels = await db_manager.get_connected_channels()
        if not channels:
            await event.respond("📭 尚未连接任何频道。")
            return
            
        response = "📺 **已连接/已保存频道:**\n\n"
        for ch in channels:
            response += f"• **{ch['title']}** (@{ch['username'] or 'N/A'})\n  ID: `{ch['channel_id']}`\n"
        await event.respond(response)

    # Channel Join
    @bot.on(events.NewMessage(pattern=r'/channel_join (.+)'))
    async def channel_join_handler(event):
        link = event.pattern_match.group(1)
        try:
            await search.searcher.join_channel(link)
            await event.respond("✅ 成功加入频道！")
        except Exception as e:
            await event.respond(f"❌ 加入失败: {e}")

    # Forward Link Download
    @bot.on(events.NewMessage(pattern=r'/forward (https://t\.me/c/(\d+)/(\d+)|https://t\.me/([a-zA-Z0-9_]+)/(\d+))'))
    async def forward_handler(event):
        link = event.pattern_match.group(1)
        try:
            # Try to get message from link
            # For public links: https://t.me/channel/123
            # For private links: https://t.me/c/id/123
            msg = await tg_clients.user_client.get_messages(link)
            if msg and msg.media:
                await search.searcher.batch_add_tasks([msg], str(event.chat_id))
                await event.respond("✅ 链接消息已加入下载队列。")
            else:
                await event.respond("❌ 链接无效或消息中没有媒体内容。")
        except Exception as e:
            await event.respond(f"❌ 获取链接内容失败: {e}")

    # Login & Interaction Logic
    user_states = {}

    @bot.on(events.NewMessage(pattern='/login'))
    async def login_handler(event):
        await event.respond("🔑 请输入您的手机号 (国际格式，如 +86138...)：")
        user_states[event.chat_id] = {'command': 'login', 'step': 'phone'}

    # Redefining handlers with logic separated from event for recursion/interaction
    async def do_cc(event, identifier):
        if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
            await event.respond("❌ 用户客户端未登录。请先发送 `/login` 进行登录。")
            return
        if not search.searcher:
            from telegram.search import init_searcher
            init_searcher(tg_clients.user_client)
        await event.respond(f"⏳ 正在尝试连接频道: `{identifier}`...")
        try:
            info = await search.searcher.connect_channel(identifier)
            await event.respond(f"✅ 已成功连接到频道:\n**{info['title']}** (@{info['username'] or 'N/A'})\nID: `{info['id']}`")
        except Exception as e:
            await event.respond(f"❌ 连接失败: {str(e)}")

    async def do_csk(event, keyword):
        if not search.searcher:
            from telegram.search import init_searcher
            init_searcher(tg_clients.user_client)
        if not await search.searcher.ensure_connected():
            await event.respond("❌ 请先使用 /cc 连接频道。")
            return
        await event.respond(f"🔍 正在搜索关键词: `{keyword}`...")
        try:
            messages = await search.searcher.search_keyword(keyword)
            last_search_results[event.chat_id] = messages
            if not messages:
                await event.respond("📭 未找到相关媒体消息。")
                return
            response = f"🔍 **找到 {len(messages)} 条媒体消息:**\n\n"
            for i, msg in enumerate(messages[:20]):
                name = msg.file.name or f"media_{msg.id}"
                response += f"`{i+1}.` {name} (ID: `{msg.id}`)\n"
            if len(messages) > 20: response += f"\n... 以及另外 {len(messages)-20} 条消息。"
            response += "\n\n💡 发送 `/bd` 即可全部下载，或 `/bf` `目标ID` 转发。"
            await event.respond(response)
        except Exception as e:
            await event.respond(f"❌ 搜索出错: {str(e)}")

    async def do_sh(event, keyword):
        results = await db_manager.search_history(keyword)
        if not results:
            await event.respond(f"🔍 未找到包含 `{keyword}` 的历史记录。")
            return
        response = f"🔍 **搜索历史结果 ({len(results)}):**\n\n"
        for item in results:
            response += f"✅ `{item['file_name']}`\n  时间: `{item['downloaded_at']}` | 大小: `{format_size(item['file_size'] or 0)}`\n\n"
        await event.respond(response)

    # Channel Connect
    @bot.on(events.NewMessage(pattern=r'/(channel_connect|cc)(?: (.+))?'))
    async def connect_channel_handler(event):
        identifier = event.pattern_match.group(2)
        if not identifier:
            await event.respond("🔗 请输入要连接的频道用户名或邀请链接：")
            user_states[event.chat_id] = {'command': 'cc'}
            raise events.StopPropagation
        await do_cc(event, identifier)
        raise events.StopPropagation

    # Channel Search Keyword
    @bot.on(events.NewMessage(pattern=r'/(channel_search_keyword|csk)(?: (.+))?'))
    async def search_keyword_handler(event):
        keyword = event.pattern_match.group(2)
        if not keyword:
            await event.respond("🔍 请输入要搜索的关键词：")
            user_states[event.chat_id] = {'command': 'csk'}
            raise events.StopPropagation
        await do_csk(event, keyword)
        raise events.StopPropagation

    # Search History
    @bot.on(events.NewMessage(pattern=r'/(search_history|sh)(?: (.+))?'))
    async def search_history_handler(event):
        keyword = event.pattern_match.group(2)
        if not keyword:
            await event.respond("📜 请输入要搜索的历史记录关键词：")
            user_states[event.chat_id] = {'command': 'sh'}
            raise events.StopPropagation
        await do_sh(event, keyword)
        raise events.StopPropagation

    # Login Handler
    @bot.on(events.NewMessage(pattern='/login'))
    async def login_handler(event):
        await event.respond("🔑 请输入您的手机号 (国际格式，如 +86138...)：")
        user_states[event.chat_id] = {'command': 'login', 'step': 'phone'}
        raise events.StopPropagation

    # Global State Handler
    @bot.on(events.NewMessage)
    async def state_step_handler(event):
        if not event.text:
            return
            
        if event.text.startswith('/'):
            # If a new command comes and we haven't stopped propagation yet,
            # it means it's a command NOT handled by the specific handlers above.
            # In this case, we clear the state.
            if event.chat_id in user_states and user_states[event.chat_id].get('command') != 'login':
                del user_states[event.chat_id]
            return

        state = user_states.get(event.chat_id)
        if not state:
            return
            
        cmd = state.get('command')
        
        if cmd == 'login':
            if state['step'] == 'phone':
                phone = event.text.strip().replace(' ', '')
                if not phone.startswith('+'):
                    await event.respond("❌ 请以国际格式输入 (例如 +86...)")
                    return
                try:
                    await tg_clients.send_code(phone)
                    await event.respond("📩 验证码请求已发送！\n💡 请输入收到的验证码（注意：为防止 Telegram 阻止登录，请将收到的验证码每一位数字减一后输入，例如收到的验证码是 12345，则输入 01234）。")
                    state['step'] = 'code'
                except Exception as e:
                    await event.respond(f"❌ 发送失败: {str(e)}")
                    del user_states[event.chat_id]
            elif state['step'] == 'code':
                raw_code = event.text.strip()
                transformed_code = "".join(str((int(d) + 1) % 10) if d.isdigit() else d for d in raw_code)
                try:
                    await tg_clients.sign_in(transformed_code)
                    await event.respond("🎉 登录成功！用户客户端已就绪。")
                    del user_states[event.chat_id]
                except Exception as e:
                    await event.respond(f"❌ 登录出错: {str(e)}")
                    del user_states[event.chat_id]
            raise events.StopPropagation
                    
        elif cmd == 'cc':
            del user_states[event.chat_id]
            await do_cc(event, event.text.strip())
            raise events.StopPropagation
            
        elif cmd == 'csk':
            del user_states[event.chat_id]
            await do_csk(event, event.text.strip())
            raise events.StopPropagation

        elif cmd == 'sh':
            del user_states[event.chat_id]
            await do_sh(event, event.text.strip())
            raise events.StopPropagation

    # Media Handler (Auto-download)
    @bot.on(events.NewMessage)
    async def media_handler(event):
        if event.message.text and event.message.text.startswith('/'):
            return

        if not event.message.media:
            return

        chat_id = str(event.chat_id)
        message_id = str(event.message.id)
        grouped_id = event.message.grouped_id
        
        media_type = "unknown"
        file_name = "unknown"
        
        if event.message.video:
            media_type = "video"
            file_name = event.message.file.name or f"video_{message_id}.mp4"
        elif event.message.photo:
            media_type = "photo"
            file_name = f"photo_{message_id}.jpg"
        elif event.message.document:
            media_type = "document"
            file_name = event.message.file.name or f"doc_{message_id}"
            
        if media_type not in config.media_types:
            return

        task = {
            'chat_id': chat_id,
            'message_id': message_id,
            'file_name': file_name,
            'media_type': media_type,
            'file_size': event.message.file.size or 0,
            'task_data': {
                'caption': event.message.message,
                'grouped_id': str(grouped_id) if grouped_id else None
            }
        }
        await download_manager.add_task(task)
        if not grouped_id:
            await event.respond(f"📥 已加入下载队列: `{file_name}`")

def format_size(size_bytes):
    if size_bytes == 0: return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    import math
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

logger.info("Bot 处理器已全面加载")
