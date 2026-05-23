from loguru import logger

from core.database import db_manager
from telegram.client import tg_clients


async def watch_handler(event, arg=None):
    """Main handler for /watch command.

    Subcommands:
      /watch                    — list all rules for this user
      /watch add <id> [kw]     — add a watch rule
      /watch remove <id>       — delete a rule
      /watch on <id>           — enable a rule
      /watch off <id>          — disable a rule
    """
    chat_id = str(event.chat_id)
    arg = (arg or '').strip()

    if not arg:
        return await _list_rules(event, chat_id)

    parts = arg.split(maxsplit=1)
    subcmd = parts[0].lower()

    if subcmd == 'add':
        return await _add_rule(event, chat_id, parts[1] if len(parts) > 1 else '')
    elif subcmd == 'remove' or subcmd == 'delete':
        return await _delete_rule(event, chat_id, parts[1] if len(parts) > 1 else '')
    elif subcmd == 'on':
        return await _toggle_rule(event, chat_id, parts[1] if len(parts) > 1 else '', True)
    elif subcmd == 'off':
        return await _toggle_rule(event, chat_id, parts[1] if len(parts) > 1 else '', False)
    else:
        await event.respond(
            "❌ 未知子命令。可用命令：\n"
            "• `/watch` — 列出规则\n"
            "• `/watch add <频道标识> [关键词]` — 添加规则\n"
            "• `/watch remove <id>` — 删除规则\n"
            "• `/watch on <id>` — 启用规则\n"
            "• `/watch off <id>` — 禁用规则"
        )


async def _list_rules(event, chat_id: str):
    rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
    if not rules:
        await event.respond("📡 暂无监控规则。使用 `/watch add <频道> [关键词]` 添加。")
        return

    lines = [f"📡 **监控规则 ({len(rules)}):**"]
    for _i, rule in enumerate(rules, 1):
        keyword_part = f" \"{rule['keyword']}\"" if rule.get('keyword') else ""
        media_part = f" [{rule['media_type']}]" if rule.get('media_type') else ""
        extra = keyword_part or media_part or " 全部媒体"
        status = "✅ 已启用" if rule.get('enabled') else "⛔ 已禁用"
        ch_title = rule.get('channel_title', '') or f"ID {rule['channel_id']}"
        lines.append(f"`{rule['id']}.` {ch_title}{extra} ({status})")

    await event.respond("\n".join(lines))


async def _add_rule(event, chat_id: str, args: str):
    """Parse `/watch add <channel_identifier> [keyword]` and create rule."""
    if not args:
        await event.respond(
            "❌ 用法：`/watch add <频道标识> [关键词]`\n"
            "频道标识可以是 `@username`、`https://t.me/...` 或频道 ID。"
        )
        return

    # The identifier is the first token; everything after is the keyword
    add_parts = args.split(maxsplit=1)
    identifier = add_parts[0].strip()
    keyword = add_parts[1].strip() if len(add_parts) > 1 else ""

    if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
        await event.respond("❌ 用户客户端未登录，请先使用 /login 完成登录。")
        return

    await event.respond(f"🔍 正在解析频道标识：`{identifier}` ...")
    try:
        entity = await tg_clients.user_client.get_input_entity(identifier)
        full_entity = await tg_clients.user_client.get_entity(entity)
    except Exception as e:
        logger.warning(f"Failed to resolve '{identifier}': {e}")
        await event.respond(
            f"❌ 无法解析频道 `{identifier}`。请检查标识是否正确，\n"
            "或先使用 `/cc` 连接频道后重试。"
        )
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
    else:
        desc += " 全部媒体"

    await event.respond(
        f"✅ 已添加监控规则（ID: `{rule_id}`）：\n"
        f"{desc}\n"
        f"💡 使用 `/watch` 查看所有规则。"
    )


async def _delete_rule(event, chat_id: str, arg: str):
    if not arg or not arg.isdigit():
        await event.respond("❌ 请指定要删除的规则 ID。例如：`/watch remove 1`")
        return

    rule_id = int(arg)
    rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
    target = next((r for r in rules if r["id"] == rule_id), None)
    if not target:
        await event.respond(f"❌ 未找到 ID 为 `{rule_id}` 的规则。")
        return

    await db_manager.delete_watch_rule(rule_id, owner_chat_id=chat_id)
    ch = target.get('channel_title', '') or target['channel_id']
    await event.respond(f"🗑️ 已删除规则 `{rule_id}`（{ch}）。")


async def _toggle_rule(event, chat_id: str, arg: str, enabled: bool):
    if not arg or not arg.isdigit():
        label = "启用" if enabled else "禁用"
        await event.respond(f"❌ 请指定规则 ID。例如：`/watch {label} 1`")
        return

    rule_id = int(arg)
    rules = await db_manager.get_watch_rules(owner_chat_id=chat_id)
    target = next((r for r in rules if r["id"] == rule_id), None)
    if not target:
        await event.respond(f"❌ 未找到 ID 为 `{rule_id}` 的规则。")
        return

    await db_manager.toggle_watch_rule(rule_id, enabled=enabled)
    label = "✅ 已启用" if enabled else "⛔ 已禁用"
    ch = target.get('channel_title', '') or target['channel_id']
    await event.respond(f"{label} 规则 `{rule_id}`（{ch}）。")
