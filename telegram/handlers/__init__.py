import asyncio
import re

from loguru import logger
from telethon import events

from telegram.client import tg_clients
from telegram.handlers.local_forward import autofwd_callback_handler
from telegram.handlers.system import panel_callback_handler
from telegram.router import command_router, media_auto_handler, state_handler


def setup_handlers():
    """
    Bot 处理器初始化入口
    """
    bot = tg_clients.bot_client
    if not bot:
        logger.error("Bot 客户端未初始化，无法注册处理器")
        return

    # 1. 设置机器人命令菜单 + MenuButton (后台执行)
    async def _set_menu():
        try:
            from telethon import functions, types

            # 命令列表
            await bot(functions.bots.SetBotCommandsRequest(
                scope=types.BotCommandScopeDefault(),
                lang_code='',
                commands=[
                    types.BotCommand(command='start', description='开始使用'),
                    types.BotCommand(command='login', description='登录状态/登录账号'),
                    types.BotCommand(command='status', description='任务状态'),
                    types.BotCommand(command='channel', description='频道连接/列表'),
                    types.BotCommand(command='search', description='搜索频道媒体'),
                    types.BotCommand(command='download', description='批量下载'),
                    types.BotCommand(command='forward', description='批量转发'),
                    types.BotCommand(command='dl', description='下载队列'),
                    types.BotCommand(command='cancel', description='取消操作'),
                    types.BotCommand(command='clear', description='清理队列'),
                    types.BotCommand(command='files', description='本地文件管理'),
                    types.BotCommand(command='aliyun', description='阿里云盘管理'),
                    types.BotCommand(command='watch', description='频道自动监控'),
                    types.BotCommand(command='tw', description='Twitter/X 视频下载'),
                    types.BotCommand(command='autofwd', description='自动转发配置'),
                    # types.BotCommand(command='panel', description='Web 管理面板 (Mini App)'),  # 已屏蔽
                    types.BotCommand(command='push', description='进度推送'),
                    types.BotCommand(command='rename', description='智能重命名'),
                    types.BotCommand(command='help', description='完整菜单'),
                ]
            ))

            # 输入框右侧 MenuButton → 直接打开 Mini App
            from telegram.handlers.system import _get_mini_app_url
            mini_app_url = _get_mini_app_url()
            await bot(functions.bots.SetBotMenuButtonRequest(
                user_id=types.InputUserSelf(),
                button=types.BotMenuButton(
                    text='📊 管理面板',
                    url=mini_app_url,
                ),
            ))

            logger.info("Bot 命令菜单 + MenuButton 已更新")
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

    # 3. 注册内联按钮回调处理器
    bot.add_event_handler(
        autofwd_callback_handler,
        events.CallbackQuery(data=re.compile(rb'^autofwd:'))
    )
    bot.add_event_handler(
        panel_callback_handler,
        events.CallbackQuery(data=re.compile(rb'^panel:'))
    )

    # 交互式命令回调处理器
    from telegram.handlers.cmd_aliyun import aliyun_callback_handler
    from telegram.handlers.progress_push import push_callback_handler
    from telegram.handlers.smart_rename import rename_callback_handler
    from telegram.handlers.storage import storage_callback_handler
    from telegram.handlers.watch_handler import watch_callback_handler

    bot.add_event_handler(
        push_callback_handler,
        events.CallbackQuery(data=re.compile(rb'^push:'))
    )
    bot.add_event_handler(
        rename_callback_handler,
        events.CallbackQuery(data=re.compile(rb'^rename:'))
    )
    bot.add_event_handler(
        watch_callback_handler,
        events.CallbackQuery(data=re.compile(rb'^watch:'))
    )
    bot.add_event_handler(
        storage_callback_handler,
        events.CallbackQuery(data=re.compile(rb'^files:'))
    )
    bot.add_event_handler(
        aliyun_callback_handler,
        events.CallbackQuery(data=re.compile(rb'^aliyun:'))
    )

    logger.info("Bot 生产级路由系统已全面加载")
