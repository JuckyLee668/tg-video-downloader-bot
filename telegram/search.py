import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from telethon import TelegramClient, types
from telethon.tl.types import Message

from core.config import config
from core.database import db_manager


class ChannelSearcher:
    def __init__(self, client: TelegramClient):
        self.client = client
        self.connected_peer = None
        self.peers_cache: dict[str, Any] = {}
        self.search_concurrency = 3

    async def _get_peer(self, channel_id: str, username: str = None):
        if channel_id in self.peers_cache:
            return self.peers_cache[channel_id]

        target = None
        if username:
            try:
                target = await self.client.get_entity(username)
            except Exception:
                pass

        if not target:
            try:
                cid = str(channel_id)
                full_id = int(cid if cid.startswith("-100") else f"-100{cid}")
                target = await self.client.get_entity(full_id)
            except Exception:
                pass

        if not target:
            return None

        try:
            peer = await self.client.get_input_entity(target)
            self.peers_cache[channel_id] = peer
            return peer
        except Exception as e:
            logger.warning(f"Failed to resolve channel peer {channel_id}: {e}")
            return None

    async def get_active_peers(self) -> List[Any]:
        channels = await db_manager.get_connected_channels()
        peers = []
        for channel in channels:
            peer = await self._get_peer(channel["channel_id"], channel.get("username"))
            if peer:
                peers.append(peer)
        return peers

    async def ensure_connected(self):
        if self.peers_cache:
            return True
        peers = await self.get_active_peers()
        if peers:
            self.connected_peer = self.connected_peer or peers[0]
            return True
        return False

    async def connect_channel(self, identifier: str) -> Dict[str, Any]:
        try:
            entity = await self.client.get_input_entity(identifier)
            full_entity = await self.client.get_entity(entity)

            channels = await db_manager.get_connected_channels()
            if len(channels) >= config.max_connected_channels:
                oldest = channels[-1]
                await db_manager.delete_connected_channel(oldest["channel_id"])
                self.peers_cache.pop(oldest["channel_id"], None)
                logger.info(f"Connected channel limit reached; removed {oldest.get('title')}")

            channel_info = {
                "id": full_entity.id,
                "title": getattr(full_entity, "title", "Unknown"),
                "username": getattr(full_entity, "username", None),
            }
            await db_manager.connect_channel(
                channel_id=str(channel_info["id"]),
                username=channel_info["username"],
                title=channel_info["title"],
            )

            self.connected_peer = entity
            self.peers_cache[str(channel_info["id"])] = entity
            return channel_info
        except Exception as e:
            logger.error(f"Failed to connect channel: {e}")
            raise

    def _get_media_filter(self, media_type: Optional[str]):
        if not media_type:
            return None
        media_map = {
            "photo": types.InputMessagesFilterPhotos,
            "video": types.InputMessagesFilterVideo,
            "document": types.InputMessagesFilterDocument,
            "audio": types.InputMessagesFilterMusic,
            "voice": types.InputMessagesFilterVoice,
            "animation": types.InputMessagesFilterGif,
            "round_video": types.InputMessagesFilterRoundVideo,
        }
        return media_map.get(media_type.lower())

    async def search_keyword(self, keyword: str, limit: int = 50, media_type: Optional[str] = None) -> List[Message]:
        if not await self.ensure_connected():
            raise RuntimeError("Connect a channel before searching")

        peers = await self.get_active_peers()
        m_filter = self._get_media_filter(media_type)
        sem = asyncio.Semaphore(self.search_concurrency)

        async def search_peer(peer):
            found = []
            async with sem:
                async for message in self.client.iter_messages(peer, search=keyword, limit=limit, filter=m_filter):
                    if message.media:
                        found.append(message)
            return found

        results = await asyncio.gather(*(search_peer(peer) for peer in peers), return_exceptions=True)
        all_matches = {}
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Channel keyword search failed: {result}")
                continue
            for message in result:
                all_matches[f"{message.chat_id}_{message.id}"] = message

        if not all_matches:
            return []

        all_messages = dict(all_matches)
        await self._include_album_siblings(all_messages, list(all_matches.values()))
        return sorted(all_messages.values(), key=lambda item: item.date, reverse=True)

    async def _include_album_siblings(self, all_messages: dict[str, Message], matches: list[Message]):
        processed_groups = set()
        for message in [item for item in matches if item.grouped_id]:
            group_key = f"{message.chat_id}_{message.grouped_id}"
            if group_key in processed_groups:
                continue

            async for sibling in self.client.iter_messages(message.peer_id, limit=20, offset_id=message.id + 10):
                if sibling.grouped_id == message.grouped_id:
                    key = f"{sibling.chat_id}_{sibling.id}"
                    all_messages.setdefault(key, sibling)
                elif sibling.id < message.id - 10:
                    break
            processed_groups.add(group_key)

    async def search_by_time(
        self,
        start_date: datetime,
        end_date: datetime,
        limit: int = 100,
        media_type: Optional[str] = None,
    ) -> List[Message]:
        if not await self.ensure_connected():
            raise RuntimeError("Connect a channel before searching")

        start_date = start_date.replace(tzinfo=timezone.utc)
        end_date = end_date.replace(tzinfo=timezone.utc)
        peers = await self.get_active_peers()
        m_filter = self._get_media_filter(media_type)
        sem = asyncio.Semaphore(self.search_concurrency)

        async def search_peer(peer):
            found = []
            async with sem:
                async for message in self.client.iter_messages(peer, offset_date=end_date, limit=limit, filter=m_filter):
                    if message.date < start_date:
                        break
                    if message.media:
                        found.append(message)
            return found

        results = await asyncio.gather(*(search_peer(peer) for peer in peers), return_exceptions=True)
        messages = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Channel time search failed: {result}")
                continue
            messages.extend(result)
        return sorted(messages, key=lambda item: item.date, reverse=True)

    async def get_recent(self, count: int = 50, media_type: Optional[str] = None) -> List[Message]:
        if not await self.ensure_connected():
            raise RuntimeError("Connect a channel before searching")

        peers = await self.get_active_peers()
        m_filter = self._get_media_filter(media_type)
        sem = asyncio.Semaphore(self.search_concurrency)

        async def search_peer(peer):
            found = []
            async with sem:
                async for message in self.client.iter_messages(peer, limit=count, filter=m_filter):
                    if message.media:
                        found.append(message)
            return found

        results = await asyncio.gather(*(search_peer(peer) for peer in peers), return_exceptions=True)
        messages = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Channel recent search failed: {result}")
                continue
            messages.extend(result)
        return sorted(messages, key=lambda item: item.date, reverse=True)[:count]

    async def get_dialogs(self, limit: int = 100) -> List[Dict[str, Any]]:
        dialogs = []
        async for dialog in self.client.iter_dialogs(limit=limit):
            dialogs.append({
                "id": dialog.id,
                "name": dialog.name,
                "is_group": dialog.is_group,
                "is_channel": dialog.is_channel,
                "is_user": dialog.is_user,
            })
        return dialogs

    async def forward_messages(self, from_peer_id: Any, message_ids: List[int], to_peer_id: Any):
        try:
            await self.client.forward_messages(to_peer_id, message_ids, from_peer_id)
            return True
        except Exception as e:
            logger.error(f"Failed to forward messages: {e}")
            raise

    async def batch_add_tasks(self, messages: List[Message], chat_id: str, formats: Optional[List[str]] = None):
        from downloader.manager import download_manager

        count = 0
        normalized_formats = None
        if formats:
            normalized_formats = [item.lower().lstrip(".") for item in formats if item.strip()]
            logger.info(f"Adding batch tasks with extension filter: {normalized_formats}")

        for msg in messages:
            if not msg or not msg.media:
                continue

            file_name, media_type = self._message_file_info(msg)
            if media_type not in config.media_types:
                continue

            if normalized_formats:
                ext = os.path.splitext(file_name)[1].lower().lstrip(".")
                if not ext or ext not in normalized_formats:
                    continue

            task = {
                "chat_id": chat_id,
                "message_id": str(msg.id),
                "file_name": file_name,
                "media_type": media_type,
                "file_size": msg.file.size or 0,
                "channel_id": str(msg.chat_id),
                "channel_title": getattr(msg.chat, "title", ""),
                "task_data": {
                    "caption": msg.message,
                    "date": msg.date.isoformat(),
                    "access_hash": getattr(msg.chat, "access_hash", None),
                },
            }
            await download_manager.add_task(task)
            count += 1
        return count

    def _message_file_info(self, msg: Message):
        if msg.video:
            return msg.file.name or f"video_{msg.id}.mp4", "video"
        if msg.photo:
            return f"photo_{msg.id}.jpg", "photo"
        if msg.audio:
            return msg.file.name or f"audio_{msg.id}.mp3", "audio"
        if msg.voice:
            return msg.file.name or f"voice_{msg.id}.ogg", "voice"
        if msg.gif:
            return msg.file.name or f"animation_{msg.id}.gif", "animation"
        if msg.document:
            return msg.file.name or f"doc_{msg.id}", "document"
        return f"media_{msg.id}", "unknown"

    async def join_channel(self, link: str):
        try:
            from telethon.tl.functions.channels import JoinChannelRequest
            from telethon.tl.functions.messages import ImportChatInviteRequest

            if "t.me/joinchat/" in link or "t.me/+" in link:
                invite_hash = link.split("/")[-1].replace("+", "")
                await self.client(ImportChatInviteRequest(invite_hash))
            else:
                await self.client(JoinChannelRequest(link))
            return True
        except Exception as e:
            logger.error(f"Failed to join channel: {e}")
            raise


searcher: Optional[ChannelSearcher] = None


def init_searcher(client: TelegramClient):
    global searcher
    searcher = ChannelSearcher(client)
