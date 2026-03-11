from typing import List, Optional, Dict, Any
from datetime import datetime
import os
from telethon import TelegramClient, functions, types
from telethon.tl.types import Message, InputPeerChannel
from core.database import db_manager
from core.config import config
from loguru import logger

class ChannelSearcher:
    def __init__(self, client: TelegramClient):
        self.client = client
        self.connected_peer = None
        self.peers_cache = {}

    async def _get_peer(self, channel_id: str, username: str = None):
        """获取频道的输入实体，带缓存"""
        if channel_id in self.peers_cache:
            return self.peers_cache[channel_id]
        
        try:
            target = None
            # 1. 尝试使用 username
            if username:
                try:
                    target = await self.client.get_entity(username)
                except Exception:
                    pass
            
            # 2. 尝试使用 ID
            if not target:
                try:
                    cid = str(channel_id)
                    if not cid.startswith('-100'):
                        full_id = int(f"-100{cid}")
                    else:
                        full_id = int(cid)
                    target = await self.client.get_entity(full_id)
                except Exception:
                    pass
            
            if target:
                peer = await self.client.get_input_entity(target)
                self.peers_cache[channel_id] = peer
                return peer
        except Exception as e:
            logger.warning(f"获取频道 {channel_id} 实体失败: {e}")
        return None

    async def get_active_peers(self) -> List[Any]:
        """获取所有已连接频道的输入实体"""
        channels = await db_manager.get_connected_channels()
        peers = []
        for channel in channels:
            peer = await self._get_peer(channel['channel_id'], channel.get('username'))
            if peer:
                peers.append(peer)
        return peers

    async def ensure_connected(self):
        """确保至少有一个有效的连接"""
        # 如果已经有 cache 的 peers，直接返回 True
        if self.peers_cache:
            return True
            
        # 尝试从数据库恢复
        peers = await self.get_active_peers()
        if peers:
            if not self.connected_peer:
                self.connected_peer = peers[0]
            return True
            
        return False

    async def connect_channel(self, identifier: str) -> Dict[str, Any]:
        """连接到一个频道并返回频道信息"""
        try:
            entity = await self.client.get_input_entity(identifier)
            full_entity = await self.client.get_entity(entity)
            
            # 检查数据库中的连接限制
            channels = await db_manager.get_connected_channels()
            if len(channels) >= config.max_connected_channels:
                # 移除最早的一个
                oldest = channels[-1]
                await db_manager.delete_connected_channel(oldest['channel_id'])
                if oldest['channel_id'] in self.peers_cache:
                    del self.peers_cache[oldest['channel_id']]
                logger.info(f"已达到连接限制 ({config.max_connected_channels})，移除最早频道: {oldest['title']}")

            channel_info = {
                'id': full_entity.id,
                'title': getattr(full_entity, 'title', 'Unknown'),
                'username': getattr(full_entity, 'username', None)
            }
            
            # 保存到数据库
            await db_manager.connect_channel(
                channel_id=str(channel_info['id']),
                username=channel_info['username'],
                title=channel_info['title']
            )
            
            self.connected_peer = entity
            self.peers_cache[str(channel_info['id'])] = entity
            return channel_info
        except Exception as e:
            logger.error(f"连接频道失败: {e}")
            raise e

    def _get_media_filter(self, media_type: Optional[str]):
        """根据 media_type 字符串返回 Telethon 过滤器"""
        if not media_type:
            return None
            
        m_map = {
            'photo': types.InputMessagesFilterPhotos,
            'video': types.InputMessagesFilterVideo,
            'document': types.InputMessagesFilterDocument,
            'audio': types.InputMessagesFilterMusic,
            'voice': types.InputMessagesFilterVoice,
            'animation': types.InputMessagesFilterGif,
            'round_video': types.InputMessagesFilterRoundVideo
        }
        return m_map.get(media_type.lower())

    async def search_keyword(self, keyword: str, limit: int = 50, media_type: Optional[str] = None) -> List[Message]:
        """在所有已连接的频道中搜索关键词"""
        if not await self.ensure_connected():
            raise Exception("请先使用 /channel_connect 连接到一个频道。")
        
        peers = await self.get_active_peers()
        all_matches = {}
        m_filter = self._get_media_filter(media_type)
        
        for peer in peers:
            try:
                async for message in self.client.iter_messages(peer, search=keyword, limit=limit, filter=m_filter):
                    if message.media:
                        all_matches[f"{message.chat_id}_{message.id}"] = message
            except Exception as e:
                logger.error(f"在频道搜索失败: {e}")
                continue
        
        if not all_matches:
            return []
            
        matches = list(all_matches.values())
        all_messages = {f"{m.chat_id}_{m.id}": m for m in matches}
        
        # 关联 Album 消息
        grouped_tasks = []
        for m in matches:
            if m.grouped_id:
                grouped_tasks.append(m)
        
        if grouped_tasks:
            processed_groups = set()
            for m in grouped_tasks:
                group_key = f"{m.chat_id}_{m.grouped_id}"
                if group_key in processed_groups:
                    continue
                
                async for msg in self.client.iter_messages(
                    m.peer_id,
                    limit=20,
                    offset_id=m.id + 10
                ):
                    if msg.grouped_id == m.grouped_id:
                        msg_key = f"{msg.chat_id}_{msg.id}"
                        if msg_key not in all_messages:
                            all_messages[msg_key] = msg
                    elif msg.id < m.id - 10:
                        break
                processed_groups.add(group_key)
        
        return sorted(all_messages.values(), key=lambda x: x.date, reverse=True)

    async def search_by_time(self, start_date: datetime, end_date: datetime, limit: int = 100, media_type: Optional[str] = None) -> List[Message]:
        """按时间范围搜索所有频道的媒体消息"""
        if not await self.ensure_connected():
            raise Exception("请先使用 /channel_connect 连接到一个频道。")
        
        from datetime import timezone
        start_date = start_date.replace(tzinfo=timezone.utc)
        end_date = end_date.replace(tzinfo=timezone.utc)
        
        peers = await self.get_active_peers()
        all_messages = []
        m_filter = self._get_media_filter(media_type)
        
        for peer in peers:
            try:
                async for message in self.client.iter_messages(peer, offset_date=end_date, limit=limit, filter=m_filter):
                    if message.date < start_date:
                        break
                    if message.media:
                        all_messages.append(message)
            except Exception as e:
                logger.error(f"在频道按时间搜索失败: {e}")
                continue
                
        return sorted(all_messages, key=lambda x: x.date, reverse=True)

    async def get_recent(self, count: int = 50, media_type: Optional[str] = None) -> List[Message]:
        """获取所有频道的最近消息"""
        if not await self.ensure_connected():
            raise Exception("请先使用 /channel_connect 连接到一个频道。")
        
        peers = await self.get_active_peers()
        all_messages = []
        m_filter = self._get_media_filter(media_type)
        
        for peer in peers:
            try:
                async for message in self.client.iter_messages(peer, limit=count, filter=m_filter):
                    if message.media:
                        all_messages.append(message)
            except Exception as e:
                logger.error(f"获取频道最近消息失败: {e}")
                continue
                
        return sorted(all_messages, key=lambda x: x.date, reverse=True)[:count]

    async def get_dialogs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取用户的对话列表（群组、频道、个人）"""
        dialogs = []
        async for dialog in self.client.iter_dialogs(limit=limit):
            dialogs.append({
                'id': dialog.id,
                'name': dialog.name,
                'is_group': dialog.is_group,
                'is_channel': dialog.is_channel,
                'is_user': dialog.is_user
            })
        return dialogs

    async def forward_messages(self, from_peer_id: Any, message_ids: List[int], to_peer_id: Any):
        """转发消息到指定目标"""
        try:
            # from_peer 可以是实体或 ID
            # to_peer 也可以是实体或 ID
            await self.client.forward_messages(to_peer_id, message_ids, from_peer_id)
            return True
        except Exception as e:
            logger.error(f"转发消息失败: {e}")
            raise e

    async def batch_add_tasks(self, messages: List[Message], chat_id: str, formats: Optional[List[str]] = None):
        """批量添加下载任务，支持格式过滤"""
        from downloader.manager import download_manager
        count = 0
        
        # 预处理格式，转为小写并去掉点号
        if formats:
            formats = [f.lower().lstrip('.') for f in formats]
            logger.info(f"批量添加任务，过滤格式: {formats}")

        for msg in messages:
            if not msg or not msg.media:
                continue
            
            # 提取文件名和类型
            file_name = "unknown"
            media_type = "unknown"
            
            if msg.video:
                media_type = "video"
                file_name = msg.file.name or f"video_{msg.id}.mp4"
            elif msg.photo:
                media_type = "photo"
                file_name = f"photo_{msg.id}.jpg"
            elif msg.document:
                media_type = "document"
                file_name = msg.file.name or f"doc_{msg.id}"
            
            if media_type not in config.media_types:
                continue

            # 格式过滤
            if formats:
                ext = os.path.splitext(file_name)[1].lower().lstrip('.')
                if not ext or ext not in formats:
                    continue

            task = {
                'chat_id': chat_id,
                'message_id': str(msg.id),
                'file_name': file_name,
                'media_type': media_type,
                'file_size': msg.file.size or 0,
                'channel_id': str(msg.chat_id),
                'channel_title': getattr(msg.chat, 'title', ''),
                'task_data': {
                    'caption': msg.message,
                    'date': msg.date.isoformat()
                }
            }
            await download_manager.add_task(task)
            count += 1
        return count

    async def join_channel(self, link: str):
        """加入一个新频道"""
        try:
            from telethon.tl.functions.channels import JoinChannelRequest
            from telethon.tl.functions.messages import ImportChatInviteRequest
            
            if 't.me/joinchat/' in link or 't.me/+' in link:
                hash = link.split('/')[-1].replace('+', '')
                await self.client(ImportChatInviteRequest(hash))
            else:
                await self.client(JoinChannelRequest(link))
            return True
        except Exception as e:
            logger.error(f"加入频道失败: {e}")
            raise e

searcher: Optional[ChannelSearcher] = None

def init_searcher(client: TelegramClient):
    global searcher
    searcher = ChannelSearcher(client)
