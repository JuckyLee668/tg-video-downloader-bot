"""外部视频下载 — /tw 命令 + 自动识别

/tw <url>                  解析视频 → 询问操作（或按默认配置自动执行）
直接发链接                   自动识别并走相同流程
"""

from downloader.external import external_downloader
from telegram.handlers.action_prompt import handle_media_action
from telegram.state_manager import state_manager


async def x_handler(event, arg=None):
    """/tw 命令入口"""
    if not arg:
        await event.respond(
            "🎬 **外部视频下载**\n\n"
            "用法：\n"
            "• `/tw <链接>` — 解析视频并选择操作\n"
            "• 直接发链接 — 自动识别\n\n"
            "支持：Twitter / X"
        )
        return

    # 默认：整个 arg 当作 URL
    return await handle_interactive(event, arg.strip())


async def handle_interactive(event, url: str):
    """交互模式：提取信息 → 让用户选择（也可从自动识别调用）"""
    if not external_downloader.is_supported(url):
        await event.respond("❌ 暂不支持此链接，目前支持 Twitter / X。")
        return

    try:
        await event.respond("🔍 正在解析视频信息...")
        info = await external_downloader.extract_info(url)
        info["source_url"] = url
        info["uploader"] = info.get("uploader", "X")
    except Exception as e:
        await event.respond(f"❌ {e}")
        return

    await handle_media_action(event, info, source_type="twitter")


async def handle_x_state(event, state):
    """处理 /tw 命令的 FSM 状态。委托给统一 action prompt。"""
    from telegram.handlers.action_prompt import handle_action_choice, handle_action_target

    step = state.get("step")
    if step == "choose":
        await handle_action_choice(event, state)
    elif step == "forward_target":
        await handle_action_target(event, state)
    else:
        await state_manager.clear(event.chat_id)
