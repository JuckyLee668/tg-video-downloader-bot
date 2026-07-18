from loguru import logger
from telethon import Button

from core.config import config
from telegram.client import tg_clients
from telegram.state_manager import state_manager

# ── helpers ──────────────────────────────────────────────────────────

def _status_text():
    da = config.default_action
    status = "🟢 已启用" if da.enabled else "🔴 已禁用"
    labels = {"download": "下载到本地", "forward": "下载并转发",
              "cloud": "下载并上传云盘", "all": "全部"}
    action_label = labels.get(da.action, da.action)
    target = da.target_chat or "未设置"
    return (
        f"⚙️ **默认操作配置**\n\n"
        f"状态：{status}\n"
        f"操作：{action_label}\n"
        f"转发目标：`{target}`"
    )


def _main_keyboard():
    """1️⃣ 启用  2️⃣ 禁用  3️⃣ 设置操作  4️⃣ 设置转发目标"""
    return [
        [Button.inline("1️⃣ 启用", b"autofwd:enable"),
         Button.inline("2️⃣ 禁用", b"autofwd:disable")],
        [Button.inline("3️⃣ 设置操作", b"autofwd:action")],
        [Button.inline("4️⃣ 设置转发目标", b"autofwd:target")],
    ]


def _action_keyboard():
    return [
        [Button.inline("📥 下载到本地", b"autofwd:action:set:download")],
        [Button.inline("📤 下载并转发", b"autofwd:action:set:forward")],
        [Button.inline("☁️ 下载并上传云盘", b"autofwd:action:set:cloud")],
        [Button.inline("🔄 全部", b"autofwd:action:set:all")],
        [Button.inline("🔙 返回", b"autofwd:menu")],
    ]


# ── command handler ───────────────────────────────────────────────────

async def local_forward_handler(event, arg=None):
    """配置默认操作 — 收到视频时自动执行，跳过手动选择。

    /autofwd  — 查看当前配置（带 1/2/3/4 按钮）
    """
    if not arg:
        await event.respond(
            _status_text() + "\n\n💡 使用下方按钮进行配置：",
            buttons=_main_keyboard(),
        )
        return

    # 兼容旧的文本子命令
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
        labels = {"download": "下载到本地", "forward": "下载并转发",
                  "cloud": "下载并上传云盘", "all": "全部"}
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
            "❌ 未知子命令。使用 `/autofwd` 查看配置菜单。"
        )


# ── callback handler ──────────────────────────────────────────────────

async def autofwd_callback_handler(event):
    """处理 /autofwd 内联按钮回调。"""
    data = event.data.decode() if isinstance(event.data, bytes) else event.data
    chat_id = event.chat_id

    # ── enable ──
    if data == "autofwd:enable":
        config.default_action.enabled = True
        config.save()
        logger.info("Default action enabled via inline button")
        await event.edit(
            _status_text() + "\n\n✅ 已启用\n\n💡 使用下方按钮进行配置：",
            buttons=_main_keyboard(),
        )

    # ── disable ──
    elif data == "autofwd:disable":
        config.default_action.enabled = False
        config.save()
        logger.info("Default action disabled via inline button")
        await event.edit(
            _status_text() + "\n\n⏸️ 已禁用\n\n💡 使用下方按钮进行配置：",
            buttons=_main_keyboard(),
        )

    # ── action sub-menu ──
    elif data == "autofwd:action":
        await event.edit(
            _status_text() + "\n\n请选择默认操作：",
            buttons=_action_keyboard(),
        )

    # ── set action ──
    elif data.startswith("autofwd:action:set:"):
        action = data.split(":")[-1]
        labels = {"download": "下载到本地", "forward": "下载并转发",
                  "cloud": "下载并上传云盘", "all": "全部"}
        config.default_action.action = action
        config.save()
        logger.info(f"Default action set to {action} via inline button")
        msg = _status_text() + f"\n\n✅ 默认操作已设为：{labels[action]}"
        if action in ("forward", "all") and not config.default_action.target_chat:
            msg += "\n⚠️ 尚未设置转发目标，请使用 4️⃣ 设置。"
        msg += "\n\n💡 使用下方按钮进行配置："
        await event.edit(msg, buttons=_main_keyboard())

    # ── target: prompt for input ──
    elif data == "autofwd:target":
        await state_manager.set(chat_id, {
            "command": "autofwd_target",
            "step": "input",
        })
        await event.edit(
            _status_text() + "\n\n📤 请输入转发目标（ID、@username 或链接）：\n发送 `/cancel` 取消。",
            buttons=None,
        )

    # ── back to main menu ──
    elif data == "autofwd:menu":
        await event.edit(
            _status_text() + "\n\n💡 使用下方按钮进行配置：",
            buttons=_main_keyboard(),
        )

    # ── unknown ──
    else:
        logger.warning(f"Unknown autofwd callback: {data}")


# ── FSM handler for target input ──────────────────────────────────────

async def handle_autofwd_target(event, state):
    """处理 autofwd 转发目标输入（FSM step='input'）"""
    target = event.text.strip()

    # 验证目标可达
    if tg_clients.user_client and await tg_clients.user_client.is_user_authorized():
        try:
            from downloader.manager import download_manager
            await download_manager._resolve_forward_peer(tg_clients.user_client, target)
        except Exception as e:
            await event.respond(f"⚠️ 目标暂无法解析，但仍会保存配置。\n错误: {e}")

    config.default_action.target_chat = target
    config.save()
    await state_manager.clear(event.chat_id)

    await event.respond(
        _status_text() + f"\n\n✅ 转发目标已设为：`{target}`",
        buttons=_main_keyboard(),
    )
