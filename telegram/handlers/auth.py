from telethon import events
from core.config import config
from telegram.client import tg_clients
from telegram.state_manager import state_manager

async def auth_status_handler(event, arg=None):
    user_cfg_ok = bool(config.user_api.api_id and config.user_api.api_hash)
    user_client_state = "未创建"
    user_authorized = False
    
    if tg_clients.user_client:
        try:
            await tg_clients.user_client.connect()
            user_authorized = await tg_clients.user_client.is_user_authorized()
            user_client_state = "已连接" if tg_clients.user_client.is_connected() else "未连接"
        except Exception as e:
            user_client_state = f"异常: {e}"

    proxy_desc = config.user_api.proxy or config.proxy
    proxy_text = "已禁用" if not proxy_desc else (
        f"{proxy_desc.scheme}://{proxy_desc.hostname}:{proxy_desc.port}"
    )

    msg = (
        "🔐 **登录状态检查**\n\n"
        f"• Bot 运行: ✅\n"
        f"• User API 配置: {'✅' if user_cfg_ok else '❌'}\n"
        f"• User 客户端: {user_client_state}\n"
        f"• 已登录: {'✅' if user_authorized else '❌'}\n"
        f"• 代理: {proxy_text}\n"
        "\n若未登录，请发送 /login 按提示完成登录。"
    )
    await event.respond(msg)

async def login_handler(event, arg=None):
    await event.respond("🔑 请输入您的手机号 (国际格式，如 +86138...)：")
    state_manager.set(event.chat_id, {'command': 'login', 'step': 'phone'})
