from telethon import events, Button
from loguru import logger
from datetime import datetime
import os
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
user_states = {}

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
                    types.BotCommand(command='auth', description='查看账号登录状态'),
                    types.BotCommand(command='bd', description='批量下载媒体'),
                    types.BotCommand(command='bdf', description='按格式批量下载'),
                    types.BotCommand(command='bf', description='批量转发媒体'),
                    types.BotCommand(command='cancel', description='取消当前操作'),
                    types.BotCommand(command='cc', description='连接频道'),
                    types.BotCommand(command='channels', description='查看已连接频道'),
                    types.BotCommand(command='clear', description='清理下载记录'),
                    types.BotCommand(command='csk', description='按关键词搜索'),
                    types.BotCommand(command='csr', description='获取最新消息'),
                    types.BotCommand(command='cst', description='按时间搜索'),
                    types.BotCommand(command='dl', description='查看下载队列'),
                    types.BotCommand(command='help', description='返回完整菜单'),
                    types.BotCommand(command='login', description='登录账号'),
                    types.BotCommand(command='sh', description='搜索历史记录'),
                    types.BotCommand(command='start', description='开始使用'),
                    types.BotCommand(command='status', description='查看任务状态'),
                ]
            ))
            logger.info("Bot commands menu has been set up")
        except Exception as e:
            logger.error(f"Failed to set bot commands: {e}")

    # Run command setup in background
    import asyncio
    asyncio.create_task(set_bot_commands())

    # --- Helper Functions (Core Logic) ---

    def _is_valid_target(target: str) -> bool:
        if not target: return False
        if target.startswith("https://t.me/") or target.startswith("t.me/"): return True
        if target.startswith("@") and len(target) > 3: return True
        if target.lstrip("-").isdigit(): return True
        return False

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
            response += f"✅ `{item['file_name']}`\n  时间: `{item['downloaded_at']}` | 大小: `{format_size(item['file_size'] or 0)}` | 频道: `{item['channel_title']}`\n\n"
        await event.respond(response)

    async def do_cst(event, start_str, end_str):
        if not search.searcher:
            from telegram.search import init_searcher
            init_searcher(tg_clients.user_client)
        if not await search.searcher.ensure_connected():
            await event.respond("❌ 请先使用 /cc 连接频道。")
            return
        try:
            start_date = datetime.strptime(start_str, "%Y-%m-%d")
            end_date = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            await event.respond(f"🔍 正在搜索时间段: `{start_date.date()}` 至 `{end_date.date()}`...")
            messages = await search.searcher.search_by_time(start_date, end_date)
            last_search_results[event.chat_id] = messages
            await event.respond(f"✅ 找到 {len(messages)} 条媒体消息。发送 `/bd` 即可全部下载。")
        except Exception as e:
            await event.respond(f"❌ 搜索出错: {str(e)}")

    async def do_bd(event, arg):
        last_results = last_search_results.get(event.chat_id, [])
        if not last_results:
            await event.respond("❌ 请先进行搜索。")
            return
        messages_to_download = []
        if not arg:
            messages_to_download = last_results
        else:
            try:
                indices = set()
                arg_clean = arg.replace('，', ',')
                parts = arg_clean.split(',')
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
                await event.respond(f"❌ 解析范围出错: {str(e)}")
                return
        if not messages_to_download:
            await event.respond("❌ 没有找到匹配的可下载内容。")
            return
        await event.respond(f"📥 正在将 {len(messages_to_download)} 个任务加入队列...")
        count = await search.searcher.batch_add_tasks(messages_to_download, str(event.chat_id))
        await event.respond(f"✅ 成功添加 {count} 个下载任务。")

    async def do_bdf(event, formats_str, indices_str=None):
        last_results = last_search_results.get(event.chat_id, [])
        if not last_results:
            await event.respond("❌ 请先进行搜索。")
            return
        formats = [f.strip() for f in formats_str.replace('，', ',').split(',')]
        messages_to_download = []
        if not indices_str or indices_str.lower() == 'all':
            messages_to_download = last_results
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
                for idx in sorted(indices):
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

    async def do_bf(event, target, indices_str=None, delete_after=False):
        if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
            await event.respond("❌ 用户客户端未登录，无法转发。请先 /login。")
            return
        last_results = last_search_results.get(event.chat_id, [])
        if not last_results:
            await event.respond("❌ 请先进行搜索。")
            return
        messages_to_forward = []
        if not indices_str or indices_str.lower() == 'all':
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
                for idx in sorted(indices):
                    if 1 <= idx <= len(last_results):
                        messages_to_forward.append(last_results[idx-1])
            except Exception as e:
                await event.respond(f"❌ 解析序号出错: {str(e)}")
                return
        if not messages_to_forward:
            await event.respond("❌ 没有找到匹配的消息。")
            return

        large_files = [m for m in messages_to_forward if m.file and m.file.size > 2000 * 1024 * 1024]
        if large_files:
            me = await tg_clients.user_client.get_me()
            if not getattr(me, 'premium', False):
                await event.respond(f"⚠️ 检测到大文件（>2GB）。非 Premium 账号无法转发大文件。")
                return

        added = 0
        for msg in messages_to_forward:
            file_name = msg.file.name if msg.file and msg.file.name else f"media_{msg.id}"
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
        await event.respond(f"📥 已将 {added} 条消息加入下载转发队列。")

    # --- Event Handlers ---

    @bot.on(events.NewMessage(pattern=r'^/(start|help|h)$'))
    async def help_handler(event):
        help_text = (
            "🤖 **Telegram Media Downloader Bot**\n\n"
            "🔐 **账号管理**\n"
            "• `/auth` — 查看账号登录状态\n"
            "• `/login` — 登录 Telegram 账号\n"
            "• `/status` (`/s`) — 查看任务状态与下载进度\n\n"
            "📺 **频道管理**\n"
            "• `/channel_connect` (`/cc`) — 连接频道进行搜索\n"
            "• `/channels` — 查看已连接频道\n\n"
            "🔍 **频道内容搜索**\n"
            "• `/channel_search_keyword` (`/csk`) — 按关键词搜索\n"
            "• `/channel_search_recent` (`/csr`) — 获取最新消息\n"
            "• `/channel_search_time` (`/cst`) — 按时间范围搜索\n\n"
            "📥 **下载与转发**\n"
            "• `/batch_download` (`/bd`) — 批量下载媒体\n"
            "• `/batch_download_formats` (`/bdf`) — 按格式下载\n"
            "• `/batch_forward` (`/bf`) — 批量转发媒体\n\n"
            "📋 **下载记录**\n"
            "• `/download_list` (`/dl`) — 查看下载队列和历史\n"
            "• `/search_history` (`/sh`) — 搜索历史下载记录\n\n"
            "⚙️ **系统命令**\n"
            "• `/cancel` (`/c`) — 取消当前操作或下载\n"
            "• `/clear` (`/cl`) — 清理下载队列和历史\n"
            "• `/help` (`/h`) — 返回完整菜单"
        )
        await event.respond(help_text)
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/(status|s)$'))
    async def status_handler(event):
        summary = await db_manager.get_stats_summary()
        async with aiosqlite.connect(db_manager.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM download_queue WHERE status = 'downloading' LIMIT 10")
            active_tasks = await cursor.fetchall()
        status_text = (
            "📊 **系统状态:**\n"
            f"⏳ 队列: `{summary['pending']}` | 📥 正在下载: `{summary['downloading']}`\n"
            f"✅ 已完成: `{summary['completed']}` | 📦 总大小: `{format_size(summary['total_size'] or 0)}`"
        )
        if active_tasks:
            status_text += "\n\n🚀 **活跃任务:**\n"
            for t in active_tasks: status_text += f"• `{t['file_name']}`: {t['progress'] or 0}%\n"
        await event.respond(status_text)
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/(cancel|c)$'))
    async def cancel_handler(event):
        await download_manager.cancel_user_tasks(str(event.chat_id))
        await event.respond("🚫 已取消所有待处理任务。")
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/(clear|cl)$'))
    async def clear_handler(event):
        if event.chat_id in last_search_results: del last_search_results[event.chat_id]
        await event.respond("🧹 搜索缓存已清理。")
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/(auth|login_status)$'))
    async def auth_handler(event):
        user_authorized = False
        if tg_clients.user_client:
            await tg_clients.user_client.connect()
            user_authorized = await tg_clients.user_client.is_user_authorized()
        await event.respond(f"🔐 **登录状态:** {'✅ 已登录' if user_authorized else '❌ 未登录'}")
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/(download_list|dl)(?: +(\d+))?$'))
    async def dl_list_handler(event):
        page = int(event.pattern_match.group(2) or 1)
        res = await db_manager.get_download_list(page, 10)
        if not res['items']: await event.respond("📭 队列为空。"); return
        response = f"📋 **下载队列 (P{page}):**\n\n"
        for t in res['items']: response += f"• `{t['file_name']}` | `{t['status']}`\n"
        await event.respond(response)
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/(channel_connect|cc)(?: +(.+))?$'))
    async def cc_handler(event):
        identifier = event.pattern_match.group(2)
        if not identifier:
            await event.respond("🔗 请输入频道用户名或链接：")
            user_states[event.chat_id] = {'command': 'cc'}
        else: await do_cc(event, identifier)
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/(channel_search_keyword|csk)(?: +(.+))?$'))
    async def csk_handler(event):
        keyword = event.pattern_match.group(2)
        if not keyword:
            await event.respond("🔍 请输入搜索关键词：")
            user_states[event.chat_id] = {'command': 'csk'}
        else: await do_csk(event, keyword)
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/(channel_search_recent|csr)(?: +(\d+))?$'))
    async def csr_handler(event):
        if not search.searcher: from telegram.search import init_searcher; init_searcher(tg_clients.user_client)
        if not await search.searcher.ensure_connected(): await event.respond("❌ 请先 /cc 连接。"); return
        count = int(event.pattern_match.group(2) or 50)
        await event.respond(f"🔍 获取最近 {count} 条消息...")
        try:
            messages = await search.searcher.get_recent(count)
            last_search_results[event.chat_id] = messages
            await event.respond(f"🔍 找到 {len(messages)} 条媒体消息。")
        except Exception as e: await event.respond(f"❌ 错误: {e}")
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/(channel_search_time|cst)(?: +(\d{4}-\d{2}-\d{2}))?(?: +(\d{4}-\d{2}-\d{2}))?$'))
    async def cst_handler(event):
        start = event.pattern_match.group(2); end = event.pattern_match.group(3)
        if not start:
            await event.respond("⌛ 开始日期 (YYYY-MM-DD)："); user_states[event.chat_id] = {'command': 'cst', 'step': 'start'}
        elif not end:
            await event.respond("⌛ 结束日期 (YYYY-MM-DD)："); user_states[event.chat_id] = {'command': 'cst', 'step': 'end', 'start': start}
        else: await do_cst(event, start, end)
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/(batch_download|bd)(?: +(.+))?$'))
    async def bd_handler(event):
        await do_bd(event, event.pattern_match.group(2))
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/(batch_download_formats|bdf)(?: +([^ ]+))?(?: +(.+))?$'))
    async def bdf_handler(event):
        f_str = event.pattern_match.group(2); i_str = event.pattern_match.group(3)
        if not f_str:
            await event.respond("🧩 输入格式（如 mp4,mkv）："); user_states[event.chat_id] = {'command': 'bdf', 'step': 'formats'}
        else: await do_bdf(event, f_str, i_str)
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/(batch_forward|bf)(?: +([^ ]+))?(?: +(.+))?$'))
    async def bf_handler(event):
        target = event.pattern_match.group(2); i_str = event.pattern_match.group(3)
        if not target:
            await event.respond("📤 输入目标聊天 ID/@username："); user_states[event.chat_id] = {'command': 'bf', 'step': 'target'}
        else:
            user_states[event.chat_id] = {'command': 'bf', 'step': 'delete', 'target': target, 'indices': i_str}
            await event.respond("🗑️ 转发后删除本地文件？回复 yes/no。")
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/channels$'))
    async def channels_handler(event):
        channels = await db_manager.get_connected_channels()
        if not channels: await event.respond("📭 尚未连接频道。"); return
        res = "📺 **已连接频道:**\n\n"
        for ch in channels: res += f"• **{ch['title']}** (@{ch['username'] or 'N/A'})\n"
        await event.respond(res)
        raise events.StopPropagation

    @bot.on(events.NewMessage(pattern=r'^/login$'))
    async def login_handler(event):
        await event.respond("🔑 输入手机号 (如 +86...)："); user_states[event.chat_id] = {'command': 'login', 'step': 'phone'}
        raise events.StopPropagation

    # --- Global Interaction Handler ---

    @bot.on(events.NewMessage)
    async def interaction_handler(event):
        if not event.text or event.text.startswith('/'): return
        state = user_states.get(event.chat_id)
        if not state: return
        
        cmd = state.get('command')
        if cmd == 'login':
            if state['step'] == 'phone':
                phone = event.text.strip().replace(' ', '')
                try:
                    await tg_clients.send_code(phone)
                    await event.respond("📩 验证码已发送！输入减一后的验证码。")
                    state['step'] = 'code'
                except Exception as e: await event.respond(f"❌: {e}"); del user_states[event.chat_id]
            elif state['step'] == 'code':
                code = "".join(str((int(d)+1)%10) if d.isdigit() else d for d in event.text.strip())
                try:
                    await tg_clients.sign_in(code); await event.respond("🎉 登录成功！"); del user_states[event.chat_id]
                except Exception as e: await event.respond(f"❌: {e}"); del user_states[event.chat_id]
        elif cmd == 'cc':
            del user_states[event.chat_id]; await do_cc(event, event.text.strip())
        elif cmd == 'csk':
            del user_states[event.chat_id]; await do_csk(event, event.text.strip())
        elif cmd == 'cst':
            if state['step'] == 'start':
                state['step'] = 'end'; state['start'] = event.text.strip(); await event.respond("⌛ 结束日期：")
            elif state['step'] == 'end':
                start = state['start']; end = event.text.strip(); del user_states[event.chat_id]; await do_cst(event, start, end)
        elif cmd == 'bdf':
            if state['step'] == 'formats':
                state['step'] = 'indices'; state['formats'] = event.text.strip(); await event.respond("🔢 序号范围（如 1-5,8 或 all）：")
            elif state['step'] == 'indices':
                f = state['formats']; i = event.text.strip(); del user_states[event.chat_id]; await do_bdf(event, f, i)
        elif cmd == 'bf':
            if state['step'] == 'target':
                state['step'] = 'delete'; state['target'] = event.text.strip(); await event.respond("🗑️ 转发后删除？yes/no。")
            elif state['step'] == 'delete':
                t = state['target']; i = state.get('indices'); ans = event.text.strip().lower()
                del user_states[event.chat_id]; await do_bf(event, t, i, ans not in ['no', 'n'])
        
        raise events.StopPropagation

    # --- Auto-Media Handler ---
    @bot.on(events.NewMessage)
    async def media_handler(event):
        if not event.message.media or (event.message.text and event.message.text.startswith('/')): return
        m = event.message; chat_id = str(event.chat_id)
        m_type = "video" if m.video else "photo" if m.photo else "document" if m.document else "unknown"
        if m_type not in config.media_types: return
        f_name = m.file.name or f"{m_type}_{m.id}"
        task = {
            'chat_id': chat_id, 'message_id': str(m.id), 'file_name': f_name, 'media_type': m_type,
            'file_size': m.file.size or 0, 'task_data': {'caption': m.message, 'requester_chat_id': chat_id}
        }
        await download_manager.add_task(task)
        if not m.grouped_id: await event.respond(f"📥 加入队列: `{f_name}`")

def format_size(size_bytes):
    if size_bytes == 0: return "0B"
    import math
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i); s = round(size_bytes / p, 2)
    return f"{s} {('B', 'KB', 'MB', 'GB', 'TB')[i]}"

logger.info("Bot 处理器已全面恢复并优化")
