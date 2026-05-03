from core.database import db_manager
from telegram import search as search_module
from telegram.client import tg_clients
from telegram.state_manager import state_manager


async def connect_channel_handler(event, arg=None):
    identifier = (arg or '').strip()
    if not identifier:
        await event.respond("请输入要连接的频道用户名或邀请链接（例如 `@channel` 或 `https://t.me/...`）：")
        state_manager.set(event.chat_id, {'command': 'cc'})
        return

    # /cc should only require user login; it must not require an already-connected channel.
    if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
        await event.respond("❌ 用户客户端未登录，请先使用 /login 完成登录。")
        return

    if not search_module.searcher:
        from telegram.search import init_searcher
        init_searcher(tg_clients.user_client)

    await event.respond(f"正在尝试连接频道：`{identifier}` ...")
    try:
        info = await search_module.searcher.connect_channel(identifier)
        await event.respond(
            f"✅ 已成功连接频道：\n**{info['title']}** (@{info['username'] or 'N/A'})\nID: `{info['id']}`"
        )
    except Exception as e:
        await event.respond(f"❌ 连接失败：{str(e)}")


async def channels_list_handler(event, arg=None):
    channels = await db_manager.get_connected_channels()
    if not channels:
        await event.respond("暂无已连接频道。")
        return

    response = "📵 已连接并保存的频道：\n\n"
    for ch in channels:
        response += f"- **{ch['title']}** (@{ch['username'] or 'N/A'})\n  ID: `{ch['channel_id']}`\n"
    await event.respond(response)
