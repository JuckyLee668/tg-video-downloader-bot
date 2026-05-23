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
    "start": system.start_handler,
    "help": system.help_handler,
    "h": system.help_handler,
    "status": system.status_handler,
    "s": system.status_handler,
    "auth": auth.auth_status_handler,
    "login": auth.login_handler,
    # 合并命令
    "channel": cmd_channel.channel_handler,
    "search": cmd_search.search_handler,
    "download": cmd_download.download_handler,
    "forward": cmd_forward.forward_handler,
    # 旧命令保留别名
    "cc": channel.connect_channel_handler,
    "channels": channel.channels_list_handler,
    "csk": search.search_keyword_handler,
    "csr": search.search_recent_handler,
    "cst": search.search_time_handler,
    "sh": search.search_history_handler,
    "bd": download.batch_download_handler,
    "bdf": download.batch_download_formats_handler,
    "bf": forward.batch_forward_handler,
    "dl": download.download_list_handler,
    "cancel": download.cancel_handler,
    "c": download.cancel_handler,
    "clear": download.clear_cache_handler,
    "cl": download.clear_cache_handler,
    "files": storage.storage_handler,
    "f": storage.storage_handler,
    "lf": local_forward.local_forward_handler,
    "push": progress_push.progress_push_handler,
    "rename": smart_rename.smart_rename_handler,
    "watch": watch_handler.watch_handler,
    "aliyun": cmd_aliyun.aliyun_handler,
}

# 别名映射 (处理长短命令一致性)
ALIAS_MAP = {
    "channel_connect": "cc",
    "channel_search_keyword": "csk",
    "channel_search_recent": "csr",
    "channel_search_time": "cst",
    "search_history": "sh",
    "batch_download": "bd",
    "batch_download_formats": "bdf",
    "download_list": "dl",
    "batch_forward": "bf",
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

    # 处理别名
    if cmd in ALIAS_MAP:
        cmd = ALIAS_MAP[cmd]

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

async def state_handler(event):
    """
    处理处于中间状态的交互逻辑 (FSM)
    """
    if not event.text or event.text.startswith('/'):
        return

    state = await state_manager.get(event.chat_id)
    if not state:
        return

    cmd = state.get('command')
    step = state.get('step')
    allow_login_bootstrap = (cmd == "login")
    if not await _ensure_authorized_or_reply(event, allow_login_bootstrap=allow_login_bootstrap):
        raise events.StopPropagation

    logger.debug(f"Processing state: user={event.chat_id}, cmd={cmd}, step={step}")

    try:
        # --- LOGIN STATE ---
        if cmd == 'login':
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

    # ... 这里保留你原来的 media_handler 逻辑，但调用 download_manager ...
    chat_id = str(event.chat_id)
    # 简化逻辑，实际项目中可从原 handlers 移过来
    media_type = "video" if event.message.video else "photo" if event.message.photo else "document"
    if media_type not in config.media_types:
        return

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
    if not event.message.grouped_id:
        await event.respond(f"📥 已加入下载队列: `{task['file_name']}`")
