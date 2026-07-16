import re

from loguru import logger
from telethon import events

from core.config import config
from downloader.manager import download_manager
from telegram.client import tg_clients

# 导入所有模块化的 handler
from telegram.handlers import (
    auth,
    channel,
    cmd_aliyun,
    cmd_channel,
    cmd_download,
    cmd_forward,
    cmd_search,
    cmd_x,
    download,
    forward,
    local_forward,
    progress_push,
    search,
    smart_rename,
    storage,
    system,
    watch_handler,
)
from telegram.state_manager import state_manager

# 命令映射表
COMMAND_MAP = {
    # 系统
    "start": system.start_handler,
    "help": system.help_handler,
    "h": system.help_handler,
    "status": system.status_handler,
    "s": system.status_handler,
    "login": auth.login_handler,
    # 频道
    "channel": cmd_channel.channel_handler,
    # 搜索
    "search": cmd_search.search_handler,
    # 下载
    "download": cmd_download.download_handler,
    "forward": cmd_forward.forward_handler,
    "dl": download.download_list_handler,
    "cancel": download.cancel_handler,
    "c": download.cancel_handler,
    "clear": download.clear_cache_handler,
    "cl": download.clear_cache_handler,
    # 存储
    "files": storage.storage_handler,
    "f": storage.storage_handler,
    # 配置
    "autofwd": local_forward.local_forward_handler,
    "push": progress_push.progress_push_handler,
    "rename": smart_rename.smart_rename_handler,
    "watch": watch_handler.watch_handler,
    # 云盘
    "aliyun": cmd_aliyun.aliyun_handler,
    # 外部下载
    "tw": cmd_x.x_handler,
    # 旧命令别名（兼容老用户）
    "bf": forward.batch_forward_handler,
    "bd": download.batch_download_handler,
    "bdf": download.batch_download_formats_handler,
}

_owner_user_id_cache = None


async def _get_owner_user_id():
    global _owner_user_id_cache
    if _owner_user_id_cache:
        return _owner_user_id_cache

    if tg_clients.user_client and await tg_clients.user_client.is_user_authorized():
        me = await tg_clients.user_client.get_me()
        if me and getattr(me, "id", None):
            _owner_user_id_cache = str(me.id)
            return _owner_user_id_cache
    return None


async def _is_authorized_event(event) -> bool:
    allowed = config.allowed_user_ids or []
    if not allowed:
        return True

    normalized = {str(item).strip().lower() for item in allowed if str(item).strip()}
    sender_id = str(getattr(event, "sender_id", None) or getattr(event, "chat_id", ""))

    if sender_id and sender_id in normalized:
        return True

    try:
        sender = await event.get_sender()
        username = (getattr(sender, "username", None) or "").lower()
        if username and (username in normalized or f"@{username}" in normalized):
            return True
    except Exception:
        pass

    if "me" in normalized:
        owner_id = await _get_owner_user_id()
        if owner_id and sender_id == owner_id:
            return True

    return False


async def _allow_login_bootstrap(event) -> bool:
    """
    Prevent deadlock when allowlist contains only `me` but owner
    is not initialized yet. Allow only in private chat.
    """
    allowed = config.allowed_user_ids or []
    if not allowed:
        return False

    normalized = {str(item).strip().lower() for item in allowed if str(item).strip()}
    if "me" not in normalized:
        return False

    if not bool(getattr(event, "is_private", False)):
        return False

    owner_id = await _get_owner_user_id()
    return owner_id is None


async def _ensure_authorized_or_reply(event, allow_login_bootstrap: bool = False) -> bool:
    if await _is_authorized_event(event):
        return True

    if allow_login_bootstrap and await _allow_login_bootstrap(event):
        logger.info(
            "Allow login bootstrap before owner init: sender_id={} chat_id={}",
            getattr(event, "sender_id", None),
            getattr(event, "chat_id", None),
        )
        return True

    logger.warning(
        "Blocked unauthorized request: sender_id={} chat_id={}",
        getattr(event, "sender_id", None),
        getattr(event, "chat_id", None),
    )
    try:
        await event.respond("❌ 你没有权限使用这个机器人。")
    except Exception:
        pass
    return False

async def command_router(event):
    """
    统一命令路由入口
    """
    text = event.raw_text.strip()
    if not text.startswith('/'):
        return

    # 解析命令和参数: /cmd arg1 arg2 -> cmd, "arg1 arg2"
    match = re.match(r'^/(\w+)(?: +(.+))?$', text)
    if not match:
        return

    cmd = match.group(1).lower()
    arg = match.group(2)

    allow_login_bootstrap = (cmd == "login")
    if not await _ensure_authorized_or_reply(event, allow_login_bootstrap=allow_login_bootstrap):
        raise events.StopPropagation

    handler = COMMAND_MAP.get(cmd)
    if not handler:
        return

    logger.debug(f"Routing command /{cmd} to {handler.__name__}")
    try:
        await handler(event, arg)
    except Exception as e:
        logger.exception(f"Error in command /{cmd}: {e}")
        await event.respond(f"❌ 执行命令时出错: {e}")

    # 停止后续 handler 触发 (Telethon 机制)
    raise events.StopPropagation

async def _try_auth_token(event):
    """用户发送 auth_token <auth_token值> <ct0值>，保存为 Twitter cookies 文件。"""
    text = event.text.strip()
    if not text.lower().startswith("auth_token"):
        return False

    parts = text.split(maxsplit=2)
    auth_value = parts[1] if len(parts) > 1 else ""
    ct0_value = parts[2] if len(parts) > 2 else ""

    if not auth_value or not ct0_value:
        await event.respond(
            "🔑 用法：`auth_token <auth_token值> <ct0值>`\n\n"
            "获取方式：\n"
            "1. 浏览器打开 x.com 并登录\n"
            "2. F12 → Application → Cookies → x.com\n"
            "3. 找到 **auth_token** 和 **ct0**，复制值\n"
            "4. 发给我，格式：`auth_token xxxxx yyyyy`"
        )
        return True

    from pathlib import Path
    cookies_path = Path(__file__).resolve().parent.parent / "data" / "twitter_cookies.txt"
    cookies_path.parent.mkdir(parents=True, exist_ok=True)

    cookies_content = f"""# Netscape HTTP Cookie File
# This is a generated file. Do not edit.
.x.com\tTRUE\t/\tTRUE\t0\tauth_token\t{auth_value}
.x.com\tTRUE\t/\tTRUE\t0\tct0\t{ct0_value}
"""

    cookies_path.write_text(cookies_content)
    logger.info(f"Twitter cookies saved to {cookies_path}")

    from downloader.external import external_downloader
    external_downloader.cookies_file = str(cookies_path)

    await event.respond("✅ Twitter cookies 已保存（auth_token + ct0），可以开始下载视频了。")
    return True


async def _try_url_auto_detect(event):
    """检测消息是否为支持的视频链接，是则自动走交互流程。"""
    text = event.text.strip()

    # 检查是否为 URL
    from downloader.external import external_downloader
    if not external_downloader.is_supported(text):
        return

    if not await _ensure_authorized_or_reply(event):
        raise events.StopPropagation

    logger.info(f"Auto-detected external URL: {text[:80]}...")
    from telegram.handlers.cmd_x import handle_interactive
    await handle_interactive(event, text)
    raise events.StopPropagation


async def state_handler(event):
    """
    处理处于中间状态的交互逻辑 (FSM)
    同时自动识别 URL（无状态时发送链接）
    """
    if not event.text or event.text.startswith('/'):
        return

    state = await state_manager.get(event.chat_id)
    if not state:
        # auth_token 保存
        if await _try_auth_token(event):
            raise events.StopPropagation
        # 无状态时，检查是否为支持的视频链接
        await _try_url_auto_detect(event)
        logger.info(f"No state for {event.chat_id}, text={event.text[:50] if event.text else '(no text)'}")
        return

    cmd = state.get('command')
    step = state.get('step')
    allow_login_bootstrap = (cmd == "login")
    if not await _ensure_authorized_or_reply(event, allow_login_bootstrap=allow_login_bootstrap):
        raise events.StopPropagation

    logger.info(f"Processing state: user={event.chat_id}, cmd={cmd}, step={step}")

    try:
        # --- Action prompt (unified) ---
        if cmd == 'action':
            from telegram.handlers.action_prompt import handle_action_choice, handle_action_target
            step = state.get("step")
            if step == "choose":
                await handle_action_choice(event, state)
            elif step == "forward_target":
                await handle_action_target(event, state)
            else:
                await state_manager.clear(event.chat_id)

        # --- LOGIN STATE ---
        elif cmd == 'login':
            if step == 'phone':
                phone = event.text.strip().replace(' ', '')
                await tg_clients.send_code(phone)
                await event.respond("📩 验证码已发送！请输入收到的验证码（注意：每一位数字减一后输入）。")
                await state_manager.update(event.chat_id, step='code')
            elif step == 'code':
                raw_code = event.text.strip()
                transformed_code = "".join(str((int(d) + 1) % 10) if d.isdigit() else d for d in raw_code)
                await tg_clients.sign_in(transformed_code)
                await event.respond("🎉 登录成功！")
                await state_manager.clear(event.chat_id)

        # --- CHANNEL CONNECT ---
        elif cmd == 'cc':
            await state_manager.clear(event.chat_id)
            await channel.connect_channel_handler(event, event.text.strip())

        # --- SEARCH KEYWORD ---
        elif cmd == 'csk':
            await state_manager.clear(event.chat_id)
            await search.search_keyword_handler(event, event.text.strip())

        # --- SEARCH HISTORY ---
        elif cmd == 'sh':
            await state_manager.clear(event.chat_id)
            await search.search_history_handler(event, event.text.strip())

        # --- SEARCH TIME (CST) ---
        elif cmd == 'cst':
            if step == 'start':
                await state_manager.update(event.chat_id, step='end', start=event.text.strip())
                await event.respond("⌛ 请输入结束日期 (YYYY-MM-DD)：")
            elif step == 'end':
                start = state.get('start')
                end = event.text.strip()
                await state_manager.clear(event.chat_id)
                await search.do_cst(event, start, end)

        # --- BATCH DOWNLOAD FORMATS (BDF) ---
        elif cmd == 'bdf':
            if step == 'formats':
                formats_str = event.text.strip()
                await state_manager.update(event.chat_id, step='indices', formats=formats_str)
                await event.respond("📤 请输入需要下载的序号范围（如 `1-5,8` 或 `all`）：")
            elif step == 'indices':
                formats_str = state.get('formats')
                indices = None if event.text.strip().lower() == 'all' else event.text.strip()
                await state_manager.clear(event.chat_id)
                await download.do_bdf(event, formats_str, indices)

        # --- BATCH FORWARD (BF) ---
        elif cmd == 'bf':
            if step == 'indices':
                indices = 'all' if event.text.strip().lower() == 'all' else event.text.strip()
                await state_manager.update(event.chat_id, step='target', indices=indices)
                await event.respond("📤 请输入目标聊天（ID 或 @username）：")
            elif step == 'target':
                target = event.text.strip()
                await state_manager.update(event.chat_id, step='delete', target=target)
                await event.respond("🗑️ 转发后是否删除？回复 yes/no。")
            elif step == 'delete':
                target = state.get('target')
                indices = state.get('indices')
                answer = event.text.strip().lower()
                delete_after = False if answer in ['no', 'n', '0'] else True
                await state_manager.clear(event.chat_id)
                await forward.do_bf(event, target, indices, delete_after)
            elif step == 'large_file_choice':
                choice = event.text.strip()
                target = state.get('target')
                indices = state.get('indices')
                delete_after = state.get('delete_after')
                if choice == '1':
                    await state_manager.clear(event.chat_id)
                    await forward.do_bf(event, target, indices, delete_after, exclude_large=True)
                elif choice == '2':
                    await state_manager.clear(event.chat_id)
                    await event.respond("❌ 已取消整个转发任务。")
                else:
                    await event.respond("⚠️ 无效输入，请回复数字 1（排除并继续）或 2（取消任务）。")

        # --- SEARCH SELECT (autofwd post-search index selection) ---
        elif cmd == 'search_select':
            from telegram.handlers.search import handle_search_select
            await handle_search_select(event, state)

        # --- TW (Twitter) STATE ---
        elif cmd == 'tw':
            from telegram.handlers import cmd_x
            await cmd_x.handle_x_state(event, state)

    except Exception as e:
        logger.exception(f"State handler error: {e}")
        await event.respond(f"❌ 流程处理出错: {e}")
        await state_manager.clear(event.chat_id)

    raise events.StopPropagation

async def media_auto_handler(event):
    """
    处理普通媒体消息 (自动入库)
    """
    if event.message.text and event.message.text.startswith('/'):
        return
    if not event.message.media:
        return

    if not await _ensure_authorized_or_reply(event):
        return

    # 过滤 bot 自己发送的消息（user_client 转发到频道/群组后 bot_client 也会看到）
    sender_id = getattr(event.message, "sender_id", None)
    if sender_id and tg_clients.user_client and await tg_clients.user_client.is_user_authorized():
        me = await tg_clients.user_client.get_me()
        if me and sender_id == me.id:
            return

    chat_id = str(event.chat_id)
    media_type = "video" if event.message.video else "photo" if event.message.photo else "document"
    if media_type not in config.media_types:
        return

    # 群组媒体（相册）→ 批量静默入库
    if event.message.grouped_id:
        import os
        raw_name = event.message.file.name
        file_name = raw_name if (raw_name and os.path.splitext(raw_name)[0]) else f"media_{event.message.id}"
        task = {
            'chat_id': chat_id,
            'message_id': event.message.id,
            'file_name': file_name,
            'media_type': media_type,
            'file_size': event.message.file.size or 0,
            'task_data': {'requester_chat_id': chat_id}
        }
        await download_manager.add_task(task)
        return

    # 频道/群组里静默入库
    if download_manager._is_channel_or_group(chat_id):
        import os
        raw_name = event.message.file.name
        file_name = raw_name if (raw_name and os.path.splitext(raw_name)[0]) else f"media_{event.message.id}"
        task = {
            'chat_id': chat_id,
            'message_id': event.message.id,
            'file_name': file_name,
            'media_type': media_type,
            'file_size': event.message.file.size or 0,
            'task_data': {'requester_chat_id': chat_id}
        }
        await download_manager.add_task(task)
        return

    # 私聊单文件 → 交互式选择
    from telegram.handlers.download import _build_source_data, _msg_to_info
    info = _msg_to_info(event.message)
    source_data = _build_source_data(event.message, event)
    from telegram.handlers.action_prompt import handle_media_action
    await handle_media_action(event, info, source_type="telegram", source_data=source_data)
