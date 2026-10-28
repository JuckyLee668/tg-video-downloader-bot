import datetime

from core.config import config
from telegram.client import tg_clients
from telegram.state_manager import state_manager


async def _current_user():
    """Return the logged-in user object, or None."""
    if not tg_clients.user_client:
        return None
    try:
        if not await tg_clients.user_client.is_user_authorized():
            return None
        return await tg_clients.user_client.get_me()
    except Exception:
        return None


def _sent_at_hint() -> str:
    """Send time of the last code request, so the user picks the right message."""
    ts = getattr(tg_clients, "code_sent_at", None)
    if not ts:
        return ""
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.datetime.fromtimestamp(ts, ZoneInfo("Asia/Shanghai"))
    except Exception:
        dt = datetime.datetime.fromtimestamp(ts)
    return f"（发送时间 {dt.strftime('%H:%M:%S')} 北京时间，请用聊天里**最新**那条）"


def _user_label(me) -> str:
    if not me:
        return "未知账号"
    name = " ".join(x for x in (me.first_name, me.last_name) if x) or "无名字"
    handle = f" @{me.username}" if getattr(me, "username", None) else ""
    return f"{name}{handle}（id: {me.id}）"


async def login_handler(event, arg=None):
    """无参数时显示登录状态并等待手机号，带参数时直接开始登录流程。"""
    # Sending a code while already logged in would destroy the live session
    # (Telegram replies AUTH_RESTART), so refuse early and clearly.
    me = await _current_user()
    if me:
        await event.respond(
            f"✅ 用户客户端已登录：{_user_label(me)}\n\n"
            "无需再获取验证码。如需更换账号，请先发送 /logout。"
        )
        return

    if not arg:
        await _show_login_status(event, ask_phone=True)
        return

    # arg is the phone number — strip spaces and start login directly
    phone = arg.strip().replace(" ", "")
    await _send_code(event, phone)


async def logout_handler(event, arg=None):
    """退出当前 user 账号登录，用于换号。"""
    me = await _current_user()
    if not me:
        await event.respond("当前用户客户端未登录，无需退出。发送 /qr 可扫码登录。")
        return

    label = _user_label(me)
    await tg_clients.logout()
    await event.respond(
        f"✅ 已退出登录：{label}\n\n"
        "该会话也已在 Telegram 服务器上终止。发送 /qr 可重新扫码登录。"
    )


async def _send_code(event, phone: str):
    """Send verification code for the given phone number."""
    try:
        res = await tg_clients.send_code(phone)
    except Exception as e:
        await event.respond(f"❌ 验证码发送失败：{e}")
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
    await state_manager.set(
        event.chat_id, {"command": "login", "step": "code", "phone": phone}
    )


async def qr_login_handler(event, arg=None):
    """给出扫码登录链接：电脑打开链接，手机 Telegram 扫码即可。"""
    from telegram.handlers.system import _get_mini_app_url
    from web.qr_login import issue_token

    me = await _current_user()
    if me:
        await event.respond(f"✅ 用户客户端已登录：{_user_label(me)}\n无需扫码。发送 /login 查看状态。")
        return

    try:
        await tg_clients.start_qr_login()
    except Exception as e:
        await event.respond(f"❌ 生成二维码失败：{e}")
        return

    token = issue_token()
    base = _get_mini_app_url().replace("/miniapp.html", "").rstrip("/")
    await event.respond(
        "🖥️ **扫码登录**\n\n"
        "请在**电脑浏览器**打开下面的链接，然后用手机 Telegram 扫码：\n"
        f"`{base}/api/login/qr/page?token={token}`\n\n"
        "手机操作路径：Telegram → 设置 → 设备 → 连接桌面设备 → 扫描二维码。\n"
        "扫码后请立刻在手机上点「确认」。二维码约 30 秒自动换新，链接 1 小时内有效。"
    )


async def _show_login_status(event, ask_phone: bool = False):
    user_cfg_ok = bool(config.user_api.api_id and config.user_api.api_hash)
    user_client_state = "未创建"
    user_authorized = False

    if tg_clients.user_client:
        try:
            await tg_clients.user_client.connect()
            user_authorized = await tg_clients.user_client.is_user_authorized()
            user_client_state = '✅' if tg_clients.user_client.is_connected() else '❌'
            if user_authorized:
                me = await tg_clients.user_client.get_me()
                user_client_state += f" {_user_label(me)}"
        except Exception as e:
            user_client_state = f"异常: {e}"

    proxy_desc = config.user_api.proxy or config.proxy
    proxy_text = "已禁用" if not proxy_desc else (
        f"{proxy_desc.scheme}://{proxy_desc.hostname}:{proxy_desc.port}"
    )

    # Twitter cookies 状态
    from pathlib import Path
    tw_cookies = Path(__file__).resolve().parent.parent.parent / "data" / "twitter_cookies.txt"
    tw_status = "✅ 已配置" if tw_cookies.exists() else "❌ 未配置"

    msg = (
        "🔐 **登录状态**\n\n"
        f"• Bot 运行: ✅\n"
        f"• User API 配置: {'✅' if user_cfg_ok else '❌'}\n"
        f"• User 客户端: {user_client_state}\n"
        f"• 已登录: {'✅' if user_authorized else '❌'}\n"
        f"• 代理: {proxy_text}\n"
        f"• Twitter Cookies: {tw_status}\n"
    )
    if ask_phone and not user_authorized:
        msg += (
            "\n请直接发送手机号（带国际区号，如 `+8613800000000`）开始登录。\n"
            "嫌验证码麻烦？打开网页面板用「扫码登录」更省事。"
        )
        await state_manager.set(event.chat_id, {"command": "login", "step": "phone"})
    elif not user_authorized:
        msg += "\n若未登录，请发送 `/login <手机号>` 开始登录。"
    msg += "\n配置 Twitter：`auth_token <auth_token值> <ct0值>`"
    await event.respond(msg)
