import asyncio
import os
import json
from typing import Dict, Any, List
from loguru import logger
from core.config import config
from core.database import db_manager
from downloader.engine import download_engine
from telethon.tl.types import InputPeerChannel

class DownloadManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.active_tasks = set() # Store task_ids currently being processed
        self.workers_started = False
        self.max_concurrent = config.max_download_task

    async def init(self):
        # Restore pending tasks from DB
        pending_tasks = await db_manager.get_pending_tasks()
        
        # Current items in queue (roughly) to avoid duplication
        # asyncio.Queue doesn't allow easy peek, so we rely on active_tasks + fresh get from DB
        
        added_count = 0
        for task in pending_tasks:
            task_id = task['task_id']
            if task_id not in self.active_tasks:
                await self.queue.put(task)
                added_count += 1
        
        logger.info(f"下载管理器已初始化，已恢复 {added_count} 个待下载任务")
        
        # Start workers only once
        if not self.workers_started:
            for i in range(self.max_concurrent):
                asyncio.create_task(self.worker(i))
            self.workers_started = True

    async def add_task(self, task: Dict[str, Any]):
        task_id = await db_manager.add_download_task(task)
        if task_id in self.active_tasks:
            return task_id
            
        # Refresh task from DB by task_id
        db_task = await db_manager.get_task_by_id(task_id)
        if db_task:
            await self.queue.put(db_task)
        return task_id

    async def worker(self, worker_id: int):
        from telegram.client import tg_clients
        from telethon.errors import FloodWaitError, RPCError
        
        while True:
            task = await self.queue.get()
            task_id = task['task_id']
            # normalize task_data
            task_data_raw = task.get('task_data') or "{}"
            if isinstance(task_data_raw, str):
                try:
                    task_data = json.loads(task_data_raw)
                except Exception:
                    task_data = {}
            elif isinstance(task_data_raw, dict):
                task_data = task_data_raw
            else:
                task_data = {}
            
            # Double check if another worker is already on it
            if task_id in self.active_tasks:
                self.queue.task_done()
                continue
                
            try:
                self.active_tasks.add(task_id)
                await db_manager.update_task_status(task_id, 'downloading')
                logger.info(f"Worker {worker_id} 开始处理任务: {task['file_name']}")
                
                save_path = os.path.join(config.save_path, task['file_name'])
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                
                downloaded_success = False
                
                if tg_clients.user_client and await tg_clients.user_client.is_user_authorized():
                    try:
                        peer_id_str = task.get('channel_id') or task.get('chat_id')
                        if not peer_id_str:
                            raise Exception("任务数据中缺失有效的 chat_id 或 channel_id")
                        
                        access_hash = task_data.get('access_hash') or task.get('access_hash')
                        entity = None
                        if access_hash and str(peer_id_str).startswith('-100'):
                            try:
                                cid = int(str(peer_id_str).replace('-100', ''))
                                entity = InputPeerChannel(cid, int(access_hash))
                            except Exception as e:
                                logger.warning(f"使用 access_hash 构造 InputPeerChannel 失败: {e}")
                        if not entity:
                            peer_id = int(peer_id_str)
                            entity = await tg_clients.user_client.get_input_entity(peer_id)
                        message_id = int(task['message_id'])
                        
                        messages = await tg_clients.user_client.get_messages(entity, ids=[message_id])
                        if messages and messages[0] and messages[0].media:
                            # --- 优化：检查本地文件是否已存在且大小匹配 ---
                            expected_size = task.get('file_size') or 0
                            if os.path.exists(save_path) and expected_size > 0:
                                actual_size = os.path.getsize(save_path)
                                if actual_size == expected_size:
                                    logger.info(f"本地文件已存在且完整，跳过下载直接进入转发阶段: {task['file_name']}")
                                    downloaded_success = True
                                else:
                                    logger.info(f"本地文件大小不匹配 ({actual_size} != {expected_size})，重新下载: {task['file_name']}")
                            
                            if not downloaded_success:
                                await download_engine.download_via_telethon(
                                    tg_clients.user_client, 
                                    messages[0], 
                                    save_path,
                                    self.create_progress_callback(task_id)
                                )
                                downloaded_success = True
                        else:
                            raise Exception(f"在实体 {peer_id} 中找不到消息 {message_id} 或消息不包含媒体")
                            
                    except FloodWaitError as e:
                        logger.warning(f"触发 Telegram 限速，需要等待 {e.seconds} 秒: {task['file_name']}")
                        await db_manager.update_task_status(task_id, 'pending', f"限速等待: {e.seconds}s")
                        await asyncio.sleep(e.seconds + 1)
                        # 重新放入队列头部优先重试
                        await self.queue.put(task)
                        continue
                        
                    except Exception as e:
                        # 检查是否是由于 Flood 导致的通用错误（有时 Telethon 不会封装成 FloodWaitError）
                        if "FLOOD_PREMIUM_WAIT" in str(e) or "FLOOD_WAIT" in str(e):
                            import re
                            wait_seconds = 30 # 默认等待
                            match = re.search(r'WAIT_(\d+)', str(e))
                            if match:
                                wait_seconds = int(match.group(1))
                            
                            logger.warning(f"检测到限速错误字符串，等待 {wait_seconds} 秒: {e}")
                            await db_manager.update_task_status(task_id, 'pending', f"限速等待: {wait_seconds}s")
                            await asyncio.sleep(wait_seconds + 1)
                            await self.queue.put(task)
                            continue
                        
                        logger.error(f"MTProto 下载失败: {e}")
                        raise e
                else:
                    raise Exception("用户客户端尚未授权，请通过 Bot 发送 /login 登录")

                if downloaded_success:
                    # sanity check: 文件存在且非空
                    if (not os.path.exists(save_path)) or os.path.getsize(save_path) == 0:
                        raise Exception(f"下载完成但未找到文件: {save_path}")

                    # forward if requested
                    forward_target = task_data.get('forward_target')
                    delete_after = bool(task_data.get('delete_after_forward', False))
                    caption = task_data.get('caption', '') or ""
                    if forward_target:
                        try:
                            peer = await tg_clients.user_client.get_entity(str(forward_target))
                            try:
                                # 对于大文件且走代理的环境，send_file 自动分片偶尔失败
                                # 我们先手动上传文件，获得 InputFile
                                file_size = os.path.getsize(save_path)
                                force_doc = task.get('media_type') == 'document'
                                
                                # --- 增加：2GB 限制检查 ---
                                if file_size > 2000 * 1024 * 1024:
                                    me = await tg_clients.user_client.get_me()
                                    if not getattr(me, 'premium', False):
                                        raise Exception(f"文件大小 ({file_size / 1024 / 1024:.2f} MB) 超过了非会员 2GB 的限制，请使用会员账号或手动分割文件。")
                                
                                logger.info(f"正在上传转发文件: {task['file_name']} ({file_size / 1024 / 1024:.2f} MB)")
                                
                                # 强制使用 512KB 分片以减少分片数量，避免超过 4000 个分片的限制
                                uploaded_file = await tg_clients.user_client.upload_file(
                                    save_path,
                                    part_size_kb=512 if file_size > 100 * 1024 * 1024 else None,
                                    progress_callback=self.create_progress_callback(task_id)
                                )
                                
                                await tg_clients.user_client.send_file(
                                    peer,
                                    uploaded_file,
                                    caption=caption,
                                    force_document=force_doc
                                )
                            except Exception as fe:
                                if "SaveBigFilePartRequest" in str(fe) or "file parts is invalid" in str(fe):
                                    logger.warning(f"手动上传转发失败，尝试最终降级方案: {fe}")
                                    await asyncio.sleep(3)
                                    # 最终降级：直接由 Telethon 托管最原始的上传
                                    await tg_clients.user_client.send_file(peer, save_path, caption=caption)
                                else:
                                    raise fe
                            
                            # 清理文件
                            if delete_after and os.path.exists(save_path):
                                try:
                                    os.remove(save_path)
                                except Exception as de:
                                    logger.warning(f"删除转发后文件失败: {de}")
                        except Exception as fe:
                            logger.error(f"下载完成但转发失败: {fe}")
                            # 删除文件避免堆积
                            if delete_after and os.path.exists(save_path):
                                try:
                                    os.remove(save_path)
                                except Exception as de:
                                    logger.warning(f"删除失败文件时出错: {de}")
                            raise fe

                    await db_manager.complete_download_task(task, {
                        'download_path': save_path if not delete_after else '',
                        'status': 'completed'
                    })
                else:
                    raise Exception("下载失败，未能在指定引擎中完成下载")

            except Exception as e:
                logger.error(f"任务处理出错: {task_id}, 错误: {e}")
                await db_manager.update_task_status(task_id, 'failed', str(e))

                # 通知触发者（如果记录了 requester_chat_id 且 bot_client 可用）
                requester = task_data.get('requester_chat_id')
                try:
                    if requester and tg_clients.bot_client:
                        await tg_clients.bot_client.send_message(requester, f"❌ 任务 {task.get('file_name')} 失败: {e}")
                except Exception as ne:
                    logger.warning(f"通知用户失败: {ne}")

                # 不再直接删除任务，允许在数据库中保留状态以便重试或查看
                # await db_manager.delete_download_task(task_id)
            
            finally:
                if task_id in self.active_tasks:
                    self.active_tasks.remove(task_id)
                self.queue.task_done()

    def create_progress_callback(self, task_id: str):
        last_update_time = 0
        last_progress = -1

        async def progress_callback(downloaded, total):
            nonlocal last_update_time, last_progress
            if not total:
                return
                
            import time
            current_time = time.time()
            progress = int(downloaded / total * 100)
            
            # Update every 1% or every 2 seconds
            if progress > last_progress or (current_time - last_update_time) > 2:
                last_progress = progress
                last_update_time = current_time
                await db_manager.update_task_progress(task_id, progress)
                
        return progress_callback

download_manager = DownloadManager()
