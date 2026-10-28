import asyncio
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from telethon.tl.types import Message

from core.database import db_manager
from downloader.manager import download_manager
from telegram import search as search_module
from telegram.client import tg_clients
from telegram.handlers.utils import message_file_info


class WatchManager:
    """Periodically watches configured Telegram channels for new messages
    matching user-defined rules, and auto-creates download tasks."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self.interval = 300  # 5 minutes default

    async def start(self):
        """Starts the periodic watch loop."""
        self._task = asyncio.create_task(self._loop())
        logger.info(f"WatchManager started, interval={self.interval}s")

    async def stop(self):
        """Stops the periodic watch loop."""
        if self._task:
            self._task.cancel()
            self._task = None
            logger.info("WatchManager stopped")

    async def _loop(self):
        """Main loop: check all rules every `interval` seconds."""
        while True:
            try:
                await self._check_all_rules()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Watch check failed: {e}")
            await asyncio.sleep(self.interval)

    async def _check_all_rules(self):
        """Fetch all enabled rules, group by (channel_id, owner_chat_id),
        and check each channel for new matching messages."""
        rules = await db_manager.get_all_enabled_watch_rules()
        if not rules:
            return

        # Group rules by (channel_id, owner_chat_id) to batch API calls
        groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for rule in rules:
            key = (rule["channel_id"], rule["owner_chat_id"])
            if key not in groups:
                groups[key] = {
                    "rules": [],
                    "last_read_id": 0,
                }
            groups[key]["rules"].append(rule)
            # Use the maximum last_read_id across all rules in the group
            groups[key]["last_read_id"] = max(
                groups[key]["last_read_id"],
                rule.get("last_read_id", 0),
            )

        for (channel_id, owner_chat_id), group in groups.items():
            try:
                await self._check_channel(
                    channel_id=channel_id,
                    owner_chat_id=owner_chat_id,
                    rules=group["rules"],
                    last_read_id=group["last_read_id"],
                )
            except Exception as e:
                logger.error(
                    f"Watch check failed for channel {channel_id}, "
                    f"owner {owner_chat_id}: {e}",
                )

    async def _check_channel(
        self,
        channel_id: str,
        owner_chat_id: str,
        rules: List[Dict[str, Any]],
        last_read_id: int,
    ):
        """Fetch new messages from a channel and match them against rules."""
        if not tg_clients.user_client:
            logger.warning("User client not available, skipping watch check")
            return

        peer = await self._resolve_peer(channel_id)
        if not peer:
            logger.warning(f"Could not resolve peer for channel {channel_id}")
            return

        # The very first run only initializes the cursor to the newest message.
        # Processing history here would flood the queue with old media.
        if last_read_id == 0:
            latest = await tg_clients.user_client.get_messages(peer, limit=1)
            if latest:
                await db_manager.update_watch_state(
                    owner_chat_id=owner_chat_id,
                    channel_id=channel_id,
                    last_read_id=latest[0].id,
                )
                logger.info(
                    f"Watch cursor initialised for {channel_id} at message {latest[0].id}"
                )
            return

        # Read oldest-first and stop after a batch.  If more than one batch is
        # pending, the cursor advances to the last processed message and the
        # next run continues from there (no gaps).
        batch_size = 200
        new_messages: List[Message] = []
        async for message in tg_clients.user_client.iter_messages(
            peer,
            min_id=last_read_id,
            reverse=True,
            limit=batch_size,
        ):
            new_messages.append(message)

        if not new_messages:
            return

        # Track the highest message ID seen to update watch_state
        max_message_id = last_read_id
        for msg in new_messages:
            if msg.id > max_message_id:
                max_message_id = msg.id
            await self._match_and_download(owner_chat_id, rules, msg)

        # Persist the read cursor
        if max_message_id > last_read_id:
            await db_manager.update_watch_state(
                owner_chat_id=owner_chat_id,
                channel_id=channel_id,
                last_read_id=max_message_id,
            )

    async def _resolve_peer(self, channel_id: str):
        """Resolve a channel_id string to a Telethon peer/entity.

        Tries the global searcher's cached peer resolution first,
        then falls back to direct entity lookup.
        """
        # Try using the searcher's cached resolution
        if search_module.searcher:
            try:
                peer = await search_module.searcher._get_peer(channel_id)
                if peer:
                    return peer
            except Exception:
                pass

        # Fallback: direct resolution
        try:
            cid = str(channel_id).strip()
            # Telethon channels often use the -100 prefix
            full_id = int(cid if cid.startswith("-100") else f"-100{cid}")
            return await tg_clients.user_client.get_input_entity(full_id)
        except Exception as e:
            logger.warning(f"Failed to resolve peer for channel {channel_id}: {e}")
            return None

    async def _match_and_download(
        self,
        owner_chat_id: str,
        rules: List[Dict[str, Any]],
        message: Message,
    ):
        """Check a message against all rules for this channel.

        - Empty keyword  → matches all (watch everything)
        - Empty media_type → matches any media
        - Both set → must match both (AND logic)

        On first match, creates a download task and stops checking
        remaining rules for this message.
        """
        if not message.media:
            return

        file_name, media_type = message_file_info(message)
        message_text = (message.message or "").lower()

        for rule in rules:
            rule_keyword = (rule.get("keyword") or "").strip()
            rule_media_type = (rule.get("media_type") or "").strip()

            # Keyword filter (case-insensitive)
            if rule_keyword:
                if rule_keyword.lower() not in message_text:
                    continue

            # Media type filter
            if rule_media_type:
                if rule_media_type.lower() != media_type.lower():
                    continue

            # ── Match found — create a download task ──────────────
            task = {
                "chat_id": owner_chat_id,
                "message_id": str(message.id),
                "file_name": file_name,
                "media_type": media_type,
                "file_size": message.file.size if message.file else 0,
                "channel_id": str(message.chat_id) if message.chat_id else "",
                "channel_title": getattr(message.chat, "title", ""),
                "task_data": {
                    "requester_chat_id": owner_chat_id,
                    "caption": message.message or "",
                    "date": message.date.isoformat() if message.date else "",
                    "access_hash": getattr(message.chat, "access_hash", None),
                },
            }
            await download_manager.add_task(task)
            logger.info(
                f"Watch rule matched — channel={message.chat_id}, "
                f"rule_id={rule['id']}, msg={message.id}, "
                f"file={file_name}",
            )
            break  # one rule per message is enough



watch_manager = WatchManager()
