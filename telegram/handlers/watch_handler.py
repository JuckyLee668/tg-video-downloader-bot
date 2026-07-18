"""频道自动监控规则 — 内联键盘交互

/watch — 查看规则列表，内联键盘管理
"""

from loguru import logger
from telethon import Button

from core.database import db_manager
from telegram.client import tg_clients
from telegram.state_manager import state_manager


async def watch_handler(event, arg=None):
    """显示监控规则列表 + 内联键盘"""
    chat_id = str(event.chat_id)

    # 兼容旧文本命令
    arg = (arg or '').strip()
    if arg:
        parts = arg.split(maxsplit=1)
        subcmd = parts[0].lower()
        if subcmd == 'add':
            return await _add_rule(event, chat_id, parts[1] if len(parts) > 1 else '')
        elif subcmd in ('remove', 'delete'):
            return await _delete_rule(event, chat_id, parts[1] if len(parts) > 1 else '')
        elif subcmd == 'on':
            return await _toggle_rule(event, chat_id, parts[1] if len(parts) > 1 else '', True)
        elif subcmd == 'off':
            return await _toggle_rule(event, chat_id, parts[1] if len(parts) > 1 else '', False)

    # 内联键盘模式
    rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
    await _show_rules_message(event, chat_id, rules, is_new=True)


def _rules_status_text(chat_id, rules):
    if not rules:
        return "📡 **监控规则**\n\n暂无规则。点击下方按钮添加。"
    lines = [f"📡 **监控规则 ({len(rules)})**:"]
    for _i, rule in enumerate(rules, 1):
        keyword_part = f' "{rule["keyword"]}"' if rule.get('keyword') else ''
        media_part = f" [{rule['media_type']}]" if rule.get('media_type') else ''
        extra = keyword_part or media_part or " 全部媒体"
        status = "✅" if rule.get('enabled') else "⛔"
        ch_title = rule.get('channel_title', '') or f"ID {rule['channel_id']}"
        lines.append(f"{status} `{rule['id']}.` {ch_title}{extra}")
    return "\n".join(lines)


def _rules_keyboard(rules):
    """每个规则一行 toggle + delete 按钮"""
    kb = []
    for rule in rules:
        status_icon = "⏸️" if rule.get('enabled') else "▶️"
        kb.append([
            Button.inline(f"{status_icon} {rule['id']}", f"watch:toggle:{rule['id']}".encode()),
            Button.inline(f"🗑 {rule['id']}", f"watch:delete:{rule['id']}".encode()),
        ])
    kb.append([
        Button.inline("➕ 添加", b"watch:add"),
        Button.inline("🔄 刷新", b"watch:refresh"),
    ])
    return kb


async def _show_rules_message(event, chat_id, rules, is_new=False):
    text = _rules_status_text(chat_id, rules)
    buttons = _rules_keyboard(rules)
    if is_new:
        await event.respond(text, buttons=buttons)
    else:
        await event.edit(text, buttons=buttons)


async def watch_callback_handler(event):
    """处理 watch: 回调"""
    data = event.data.decode() if isinstance(event.data, bytes) else event.data
    chat_id = str(event.chat_id)

    if data == "watch:add":
        await state_manager.set(chat_id, {
            "command": "watch_add",
            "step": "channel",
        })
        await event.edit(
            "➕ **添加监控规则**\n\n请输入频道标识（`@username` / 链接 / ID）：\n发送 `/cancel` 取消。",
            buttons=None,
        )
        return

    if data == "watch:refresh":
        rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
        await _show_rules_message(event, chat_id, rules)
        try:
            await event.answer("已刷新")
        except Exception:
            pass
        return

    if data.startswith("watch:toggle:"):
        rule_id = int(data.split(":")[-1])
        rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
        target = next((r for r in rules if r["id"] == rule_id), None)
        if not target:
            try:
                await event.answer("规则不存在")
            except Exception:
                pass
            return
        new_state = not target.get('enabled')
        await db_manager.toggle_watch_rule(rule_id, enabled=new_state)
        logger.info(f"Watch rule {rule_id} toggled to {new_state}")
        rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
        await _show_rules_message(event, chat_id, rules)
        label = "✅ 已启用" if new_state else "⛔ 已禁用"
        try:
            await event.answer(label)
        except Exception:
            pass
        return

    if data.startswith("watch:delete:"):
        rule_id = int(data.split(":")[-1])
        rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
        target = next((r for r in rules if r["id"] == rule_id), None)
        if not target:
            try:
                await event.answer("规则不存在")
            except Exception:
                pass
            return
        await db_manager.delete_watch_rule(rule_id, owner_chat_id=chat_id)
        logger.info(f"Watch rule {rule_id} deleted")
        rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
        await _show_rules_message(event, chat_id, rules)
        ch = target.get('channel_title', '') or target['channel_id']
        try:
            await event.answer(f"已删除: {ch}")
        except Exception:
            pass
        return


# ── FSM handlers ──

async def handle_watch_add_channel(event, state):
    """FSM: 接收频道标识"""
    text = event.text.strip()
    if text.lower() == "/cancel":
        await state_manager.clear(event.chat_id)
        rules = await db_manager.get_watch_rules(owner_chat_id=str(event.chat_id))
        await event.respond("❌ 已取消。", buttons=_rules_keyboard(rules))
        return

    chat_id = str(event.chat_id)

    if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
        await state_manager.clear(chat_id)
        await event.respond("❌ 用户客户端未登录，请先使用 /login 完成登录。")
        return

    try:
        entity = await tg_clients.user_client.get_input_entity(text)
        full_entity = await tg_clients.user_client.get_entity(entity)
    except Exception as e:
        logger.warning(f"Failed to resolve '{text}': {e}")
        await state_manager.clear(chat_id)
        await event.respond(f"❌ 无法解析频道 `{text}`。请重试。")
        return

    channel_id = str(full_entity.id)
    channel_title = getattr(full_entity, "title", None) or getattr(full_entity, "username", None) or channel_id

    await state_manager.set(chat_id, {
        "command": "watch_add",
        "step": "keyword",
        "channel_id": channel_id,
        "channel_title": channel_title,
    })
    await event.respond(
        f"✅ 频道: **{channel_title}**\n\n请输入监控关键词（可选，留空则监控全部媒体）：\n发送 `/cancel` 取消。"
    )


async def handle_watch_add_keyword(event, state):
    """FSM: 接收关键词并创建规则"""
    text = event.text.strip()
    chat_id = str(event.chat_id)
    channel_id = state.get("channel_id", "")
    channel_title = state.get("channel_title", "")

    if text.lower() == "/cancel":
        await state_manager.clear(chat_id)
        rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
        await event.respond("❌ 已取消。", buttons=_rules_keyboard(rules))
        return

    keyword = text
    rule_id = await db_manager.add_watch_rule(
        owner_chat_id=chat_id,
        channel_id=channel_id,
        channel_title=channel_title,
        keyword=keyword,
        media_type="",
    )

    desc = f"**{channel_title}**"
    if keyword:
        desc += f" 关键词「{keyword}」"

    await state_manager.clear(chat_id)
    rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
    await event.respond(
        f"✅ 已添加监控规则（ID: `{rule_id}`）：\n{desc}",
        buttons=_rules_keyboard(rules),
    )


# ── 保留的旧方法（兼容文本命令） ──

async def _list_rules(event, chat_id: str):
    rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
    await _show_rules_message(event, chat_id, rules, is_new=True)


async def _add_rule(event, chat_id: str, args: str):
    if not args:
        await event.respond("❌ 用法：`/watch add <频道标识> [关键词]`")
        return
    add_parts = args.split(maxsplit=1)
    identifier = add_parts[0].strip()
    keyword = add_parts[1].strip() if len(add_parts) > 1 else ""

    if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
        await event.respond("❌ 用户客户端未登录。")
        return

    try:
        entity = await tg_clients.user_client.get_input_entity(identifier)
        full_entity = await tg_clients.user_client.get_entity(entity)
    except Exception:
        await event.respond(f"❌ 无法解析频道 `{identifier}`。")
        return

    channel_id = str(full_entity.id)
    channel_title = getattr(full_entity, "title", None) or getattr(full_entity, "username", None) or channel_id

    rule_id = await db_manager.add_watch_rule(
        owner_chat_id=chat_id,
        channel_id=channel_id,
        channel_title=channel_title,
        keyword=keyword,
        media_type="",
    )

    desc = f"**{channel_title}**"
    if keyword:
        desc += f" 关键词「{keyword}」"

    await event.respond(f"✅ 已添加规则（ID: `{rule_id}`）：{desc}")


async def _delete_rule(event, chat_id: str, arg: str):
    if not arg or not arg.isdigit():
        await event.respond("❌ 请指定规则 ID。")
        return
    rule_id = int(arg)
    rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
    target = next((r for r in rules if r["id"] == rule_id), None)
    if not target:
        await event.respond(f"❌ 未找到 ID 为 `{rule_id}` 的规则。")
        return
    await db_manager.delete_watch_rule(rule_id, owner_chat_id=chat_id)
    await event.respond(f"🗑️ 已删除规则 `{rule_id}`。")


async def _toggle_rule(event, chat_id: str, arg: str, enabled: bool):
    if not arg or not arg.isdigit():
        await event.respond("❌ 请指定规则 ID。")
        return
    rule_id = int(arg)
    rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
    target = next((r for r in rules if r["id"] == rule_id), None)
    if not target:
        await event.respond(f"❌ 未找到 ID 为 `{rule_id}` 的规则。")
        return
    await db_manager.toggle_watch_rule(rule_id, enabled=enabled)
    label = "✅ 已启用" if enabled else "⛔ 已禁用"
    await event.respond(f"{label} 规则 `{rule_id}`。")
