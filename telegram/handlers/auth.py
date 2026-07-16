from core.config import config
from telegram.client import tg_clients
from telegram.state_manager import state_manager


async def login_handler(event, arg=None):
    """无参数时显示登录状态，带参数时开始登录流程。"""
    if not arg:
        return await _show_login_status(event)

    await event.respond("🔑 请输入您的手机号 (国际格式，如 +86138...)：")
    await state_manager.set(event.chat_id, {'command': 'login', 'step': 'phone'})


async def _show_login_status(event):
    user_cfg_ok = bool(config.user_api.api_id and config.user_api.api_hash)
    user_client_state = "未创建"
    user_authorized = False

    if tg_clients.user_client:
        try:
            await tg_clients.user_client.connect()
            user_authorized = await tg_clients.user_client.is_user_authorized()
            user_client_state = '✅' if tg_clients.user_client.is_connected() else '❌'
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
        "\n若未登录，请发送 `/login <手机号>` 开始登录。\n"
        "配置 Twitter：`auth_token <auth_token值> <ct0值>`"
    )
    await event.respond(msg)
