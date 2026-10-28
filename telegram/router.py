import datetime
import re

from loguru import logger
from telethon import events
from telethon.errors.rpcerrorlist import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from core.config import config
from downloader.manager import download_manager
from telegram.client import CodeExpiredError, decode_shifted_code, tg_clients

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


def _bj_stamp(ts: float) -> str:
    """Wall clock (Asia/Shanghai) of a unix timestamp — the server runs in UTC."""
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.datetime.fromtimestamp(ts, ZoneInfo("Asia/Shanghai"))
    except Exception:
        dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%H:%M:%S")


def _sent_at_hint() -> str:
    """Send time of the last code request, so the user picks the right message."""
    ts = getattr(tg_clients, "code_sent_at", None)
    if not ts:
        return ""
    return f"（发送时间 {_bj_stamp(ts)} 北京时间，请用聊天里**最新**那条）"


def _delivery_where(res) -> str:
    """Human readable description of where the login code went."""
    delivery = type(getattr(res, "type", None)).__name__
    return {
        "SentCodeTypeApp": "Telegram 官方「Telegram」服务对话（站内消息，不是短信）",
        "SentCodeTypeSms": "短信",
        "SentCodeTypeCall": "语音电话",
    }.get(delivery, "你的 Telegram 设备")


async def _login_code_step(event, raw_code: str, phone: str | None = None):
    """Submit a login code, keeping the login FSM usable whatever happens.

    The code is expected to arrive with every digit bumped by one (12345 ->
    23456): Telethon documents that a code sent through the app itself expires
    immediately, so the real code must never appear verbatim in the chat.  The
    verbatim form is tried as a fallback in case the user forgets.
    """
    phone = phone or getattr(tg_clients, "phone", None)

    decoded = decode_shifted_code(raw_code)
    if decoded and decoded != raw_code:
        candidates = [decoded, raw_code]
    else:
        candidates = [raw_code]

    for code in candidates:
        try:
            await tg_clients.sign_in(code)
        except SessionPasswordNeededError:
            await event.respond("🔐 你的账号启用了两步验证，请输入密码：")
            await state_manager.set(
                event.chat_id, {"command": "login", "step": "password", "phone": phone}
            )
            return
        except PhoneCodeInvalidError:
            # Try the next interpretation (verbatim vs. de-shifted) before
            # bothering the user again.
            continue
        except (PhoneCodeExpiredError, CodeExpiredError):
            # Telegram kills the previous code the moment a newer one is issued,
            # so a code copied from an older message reads as "expired" even
            # seconds after a fresh request. Re-issue one and keep the step
            # alive: clearing it made the user's corrected retry land on
            # "No state" and get dropped.
            age = tg_clients.code_age()
            age_txt = f"{age:.0f}s" if age is not None else "unknown"
            logger.warning(
                f"验证码已失效: user={event.chat_id}, phone={phone}, code_age={age_txt}"
            )
            if not phone:
                await event.respond(
                    "⚠️ 验证码已失效，且本次登录上下文已丢失（服务可能重启过）。\n"
                    "请发送 `/login 手机号` 重新开始。"
                )
                await state_manager.clear(event.chat_id)
                return
            try:
                res = await tg_clients.send_code(phone, force=True)
            except Exception as e:
                await event.respond(
                    "⚠️ 这条验证码已失效（Telegram 在新码发出后会立刻作废旧码，"
                    "多半是拿成了上一条）。\n"
                    f"❌ 自动重发也失败了：{e}\n"
                    "稍等片刻后直接发送手机号即可重新获取。"
                )
                await state_manager.set(
                    event.chat_id, {"command": "login", "step": "phone"}
                )
            else:
                await event.respond(
                    "⚠️ 这条验证码已失效（Telegram 在新码发出后会立刻作废旧码，"
                    "多半是拿成了上一条）。\n\n"
                    f"📩 已重新发送一条新的到：{_delivery_where(res)}{_sent_at_hint()}\n"
                    "请把**最新**那条验证码每位 +1 后再发给我。"
                )
                await state_manager.set(
                    event.chat_id, {"command": "login", "step": "code", "phone": phone}
                )
            return
        except Exception as e:
            await event.respond(f"❌ 登录失败：{e}")
            await state_manager.set(
                event.chat_id, {"command": "login", "step": "phone"}
            )
            return
        else:
            await event.respond("🎉 登录成功！现在可以使用频道搜索、下载和自动监控了。")
            await state_manager.clear(event.chat_id)
            return

    # Every interpretation of the input was rejected: a typo, or +1 forgotten
    # and the digits happened to be a different valid code.
    await event.respond(
        "❌ 验证码不正确。请把**最新**那条验证码的每一位都 +1 后发给我\n"
        "（例：真实 12345 → 发送 23456；9 变成 0）。\n"
        "（发送 /cancel 可取消登录）"
    )
    await state_manager.set(
        event.chat_id, {"command": "login", "step": "code", "phone": phone}
    )


# 命令映射表
COMMAND_MAP = {
    # 系统
    "start": system.start_handler,
    "help": system.help_handler,
    "h": system.help_handler,
    "status": system.status_handler,
    "s": system.status_handler,
    "login": auth.login_handler,
    "qr": auth.qr_login_handler,
    "logout": auth.logout_handler,
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
    # "panel": system.panel_handler,  # 已屏蔽
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

    # 解析命令和参数: /cmd arg1 arg2 /cmd@botname arg
    match = re.match(r'^/(\w+)(?:@\w+)?(?:\s+(.*))?$', text)
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
    text = (event.text or "").strip()

    # Extract the link instead of passing the whole message: yt-dlp rejects
    # "看看这个 https://… 谢谢" with "[generic] ... is not a valid URL".
    from downloader.external import external_downloader
    url = external_downloader.extract_supported_url(text)
    if not url:
        return

    if not await _ensure_authorized_or_reply(event):
        raise events.StopPropagation

    logger.info(f"Auto-detected external URL: {url[:80]}")
    from telegram.handlers.cmd_x import handle_interactive
    await handle_interactive(event, url)
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
        # Recover a lost login step: a code typed right after a restart or after
        # a failed attempt used to be dropped with "No state for ..., text=...".
        pending = tg_clients.pending_login()
        typed = (event.text or "").strip().replace(' ', '')
        if pending and typed.isdigit() and 4 <= len(typed) <= 7:
            await _login_code_step(event, typed, pending[0])
            raise events.StopPropagation

        if not await _ensure_authorized_or_reply(event):
            raise events.StopPropagation
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
                try:
                    res = await tg_clients.send_code(phone)
                except Exception as e:
                    await event.respond(f"❌ 验证码发送失败：{e}")
                    # Stay on the phone step: once the cooldown / flood wait is
                    # over the user can just send the number again.
                    await state_manager.update(event.chat_id, step='phone')
                    return
                delivery = type(getattr(res, "type", None)).__name__
                if delivery == "SentCodeTypeApp":
                    where = "Telegram 官方「Telegram」服务对话（站内消息，不是短信）"
                elif delivery == "SentCodeTypeSms":
                    where = "短信"
                elif delivery == "SentCodeTypeCall":
                    where = "语音电话"
                else:
                    where = "你的 Telegram 设备"
                await event.respond(
                    f"📩 验证码已发送到：{where}{_sent_at_hint()}\n\n"
                    "⚠️ 请把验证码的**每一位数字都 +1** 后发给我\n"
                    "（例：真实 12345 → 发送 23456；9 变成 0）。\n"
                    "这样验证码不会原样出现在聊天里，避免 Telegram 判定为"
                    "「站内转发」而立刻作废。\n\n"
                    "需要两步验证密码的话会再提示，发送 /cancel 可取消。"
                )
                await state_manager.update(event.chat_id, step='code', phone=phone)
            elif step == 'code':
                raw_code = event.text.strip().replace(' ', '')
                digits = raw_code.lstrip('+')
                # A phone number typed while waiting for the code means "send a
                # new code to this number" — restart instead of burning a
                # guaranteed-to-fail sign-in attempt.
                if raw_code.startswith('+') and digits.isdigit() and len(digits) > 8:
                    try:
                        res = await tg_clients.send_code(raw_code)
                    except Exception as e:
                        await event.respond(f"❌ 验证码发送失败：{e}")
                    else:
                        await event.respond(
                            f"📩 验证码已重新发送到：{_delivery_where(res)}\n\n"
                            "请使用**最新**一条验证码。"
                        )
                        await state_manager.update(
                            event.chat_id, step='code', phone=raw_code
                        )
                    raise events.StopPropagation

                await _login_code_step(event, raw_code, state.get('phone'))
            elif step == 'password':
                try:
                    await tg_clients.sign_in(password=event.text.strip())
                    await event.respond("🎉 两步验证通过，登录成功！")
                    await state_manager.clear(event.chat_id)
                except Exception as e:
                    await event.respond(f"❌ 密码错误或登录失败：{e}")

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
                elif choice == '3':
                    await state_manager.clear(event.chat_id)
                    await forward.do_bf(event, target, indices, delete_after, compress_large=True)
                else:
                    await event.respond("⚠️ 无效输入，请回复数字 1（排除）、2（取消）或 3（压缩并转发）。")

        # --- AUTOFWD TARGET (inline button → FSM) ---
        elif cmd == 'autofwd_target':
            from telegram.handlers.local_forward import handle_autofwd_target
            await handle_autofwd_target(event, state)

        # --- RENAME PATTERN (inline button → FSM) ---
        elif cmd == 'rename_pattern':
            from telegram.handlers.smart_rename import handle_rename_pattern
            await handle_rename_pattern(event, state)

        # --- WATCH ADD (inline button → 2-step FSM) ---
        elif cmd == 'watch_add':
            from telegram.handlers.watch_handler import (
                handle_watch_add_channel,
                handle_watch_add_keyword,
            )
            step = state.get("step", "")
            if step == "channel":
                await handle_watch_add_channel(event, state)
            elif step == "keyword":
                await handle_watch_add_keyword(event, state)

        # --- FILES DELETE (inline button → FSM) ---
        elif cmd == 'files_delete':
            from telegram.handlers.storage import handle_files_delete
            await handle_files_delete(event, state)

        # --- ALIYUN PATH (inline button → FSM) ---
        elif cmd == 'aliyun_path':
            from telegram.handlers.cmd_aliyun import handle_aliyun_path
            await handle_aliyun_path(event, state)

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
        raw_name = event.message.file.name if event.message.file else ""
        file_name = raw_name if (raw_name and os.path.splitext(raw_name)[0]) else f"media_{event.message.id}"
        task = {
            'chat_id': chat_id,
            'message_id': event.message.id,
            'file_name': file_name,
            'media_type': media_type,
            'file_size': (event.message.file.size if event.message.file else 0) or 0,
            'task_data': {'requester_chat_id': chat_id}
        }
        await download_manager.add_task(task)
        return

    # 频道/群组里静默入库
    if download_manager._is_channel_or_group(chat_id):
        import os
        raw_name = event.message.file.name if event.message.file else ""
        file_name = raw_name if (raw_name and os.path.splitext(raw_name)[0]) else f"media_{event.message.id}"
        task = {
            'chat_id': chat_id,
            'message_id': event.message.id,
            'file_name': file_name,
            'media_type': media_type,
            'file_size': (event.message.file.size if event.message.file else 0) or 0,
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
