from loguru import logger

from core.config import config
from telegram.client import tg_clients


async def local_forward_handler(event, arg=None):
    """配置默认操作 — 收到视频时自动执行，跳过手动选择。

    /autofwd                  — 查看当前配置
    /autofwd on               — 启用默认操作
    /autofwd off              — 禁用
    /autofwd action download  — 默认: 下载到本地
    /autofwd action forward   — 默认: 下载并转发
    /autofwd action cloud     — 默认: 下载并上传云盘
    /autofwd action all       — 默认: 全部
    /autofwd target <id>      — 设置转发目标
    """
    if not arg:
        return await _show_status(event)

    parts = arg.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    param = parts[1] if len(parts) > 1 else ""

    if cmd == "on":
        config.default_action.enabled = True
        config.save()
        logger.info("Default action enabled")
        await event.respond("✅ 已启用默认操作。收到视频时将自动按配置执行。")

    elif cmd == "off":
        config.default_action.enabled = False
        config.save()
        logger.info("Default action disabled")
        await event.respond("⏸️ 已禁用默认操作。收到视频时将询问操作。")

    elif cmd == "action":
        actions = ("download", "forward", "cloud", "all")
        if param not in actions:
            await event.respond(
                "❌ 用法：`/autofwd action <download|forward|cloud|all>`\n\n"
                "• `download` — 仅下载到本地\n"
                "• `forward` — 下载并转发\n"
                "• `cloud` — 下载并上传云盘\n"
                "• `all` — 全部执行"
            )
            return
        config.default_action.action = param
        config.save()
        labels = {"download": "下载到本地", "forward": "下载并转发", "cloud": "下载并上传云盘", "all": "全部"}
        await event.respond(f"✅ 默认操作已设为：{labels[param]}")

        if param in ("forward", "all") and not config.default_action.target_chat:
            await event.respond("⚠️ 尚未设置转发目标，请使用 `/autofwd target <id>` 设置。")

    elif cmd == "target":
        if not param:
            await event.respond("❌ 用法：`/autofwd target <chat_id 或 @username>`")
            return
        # 验证目标可达
        if tg_clients.user_client and await tg_clients.user_client.is_user_authorized():
            try:
                from downloader.manager import download_manager
                await download_manager._resolve_forward_peer(tg_clients.user_client, param)
            except Exception as e:
                await event.respond(f"⚠️ 目标暂无法解析，但仍会保存配置。\n错误: {e}")
        config.default_action.target_chat = param
        config.save()
        await event.respond(f"✅ 转发目标已设为：`{param}`")

    else:
        await event.respond(
            "❌ 未知子命令。\n"
            "用法：`/autofwd on|off|action|target`\n"
            "发送 `/autofwd` 查看当前配置。"
        )


async def _show_status(event):
    da = config.default_action
    status = "🟢 已启用" if da.enabled else "🔴 已禁用"
    labels = {"download": "下载到本地", "forward": "下载并转发", "cloud": "下载并上传云盘", "all": "全部"}
    action_label = labels.get(da.action, da.action)
    target = da.target_chat or "未设置"

    text = (
        f"⚙️ **默认操作配置**\n\n"
        f"状态：{status}\n"
        f"操作：{action_label}\n"
        f"转发目标：`{target}`\n\n"
        f"💡 启用后收到任何视频都将自动执行，不再询问。\n\n"
        f"用法：\n"
        f"• `/autofwd on` — 启用\n"
        f"• `/autofwd off` — 禁用\n"
        f"• `/autofwd action <download|forward|cloud|all>` — 设置操作\n"
        f"• `/autofwd target <id>` — 设置转发目标\n"
    )
    await event.respond(text)
