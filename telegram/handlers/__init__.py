import asyncio

from loguru import logger
from telethon import events

from telegram.client import tg_clients
from telegram.router import command_router, media_auto_handler, state_handler


def setup_handlers():
    """
    Bot 处理器初始化入口
    """
    bot = tg_clients.bot_client
    if not bot:
        logger.error("Bot 客户端未初始化，无法注册处理器")
        return

    # 1. 设置机器人命令菜单 (后台执行)
    async def _set_menu():
        try:
            from telethon import functions, types
            await bot(functions.bots.SetBotCommandsRequest(
                scope=types.BotCommandScopeDefault(),
                lang_code='en',
                commands=[
                    types.BotCommand(command='start', description='开始使用'),
                    types.BotCommand(command='auth', description='查看登录状态'),
                    types.BotCommand(command='login', description='登录账号'),
                    types.BotCommand(command='status', description='任务状态'),
                    types.BotCommand(command='cc', description='连接频道'),
                    types.BotCommand(command='csk', description='关键词搜索'),
                    types.BotCommand(command='csr', description='获取最新'),
                    types.BotCommand(command='cst', description='按时搜索'),
                    types.BotCommand(command='bd', description='批量下载'),
                    types.BotCommand(command='bf', description='批量转发'),
                    types.BotCommand(command='dl', description='下载队列'),
                    types.BotCommand(command='sh', description='搜索历史'),
                    types.BotCommand(command='cancel', description='取消操作'),
                    types.BotCommand(command='clear', description='清理缓存'),
                    types.BotCommand(command='files', description='本地文件管理'),
                    types.BotCommand(command='help', description='完整菜单'),
                ]
            ))
            logger.info("Bot 命令菜单已更新")
        except Exception as e:
            logger.error(f"设置菜单失败: {e}")

    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_set_menu())
    except RuntimeError:
        pass

    # 2. 注册统一路由
    # 顺序：状态机 -> 命令 -> 自动媒体
    bot.add_event_handler(state_handler, events.NewMessage(incoming=True))
    bot.add_event_handler(command_router, events.NewMessage(incoming=True, pattern=r'^/'))
    bot.add_event_handler(media_auto_handler, events.NewMessage(incoming=True))

    logger.info("Bot 生产级路由系统已全面加载")
