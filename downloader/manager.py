import asyncio
import json
import re
from pathlib import Path
from typing import Any, Dict

from loguru import logger
from telethon.tl.types import InputPeerChannel

from core.config import config
from core.database import db_manager
from core.paths import safe_join_download_path
from downloader.aliyundrive_uploader import aliyundrive_uploader
from downloader.engine import download_engine


class DownloadManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.active_tasks: set[str] = set()
        self.worker_tasks: list[asyncio.Task] = []
        self.max_concurrent = max(1, int(config.max_download_task))
        self.forward_peer_cache: dict[str, Any] = {}

    async def init(self):
        pending_tasks = await db_manager.get_pending_tasks()
        for _ in pending_tasks:
            await self.queue.put("wake")

        while len(self.worker_tasks) < self.max_concurrent:
            worker_id = len(self.worker_tasks)
            self.worker_tasks.append(asyncio.create_task(self.worker(worker_id)))

        logger.info(f"Download manager initialized with {self.max_concurrent} workers")

    async def add_task(self, task: Dict[str, Any]):
        task_id = await db_manager.add_download_task(task)
        await self.queue.put("wake")
        return task_id

    async def cancel_user_tasks(self, chat_id: str):
        await db_manager.cancel_tasks(chat_id)
        logger.info(f"Cancelled pending tasks for user {chat_id}")

    async def wake_workers(self, count: int | None = None):
        for _ in range(count or self.max_concurrent):
            await self.queue.put("wake")

    async def worker(self, worker_id: int):
        while True:
            await self.queue.get()
            try:
                while True:
                    task = await db_manager.claim_next_task()
                    if not task:
                        break
                    await self.process_task(worker_id, task)
            finally:
                self.queue.task_done()

    async def process_task(self, worker_id: int, task: Dict[str, Any]):
        from telethon.errors import FloodWaitError

        from telegram.client import tg_clients

        task_id = task["task_id"]
        task_data = self._loads_task_data(task.get("task_data"))
        self.active_tasks.add(task_id)

        try:
            logger.info(f"Worker {worker_id} started task {task_id}: {task.get('file_name')}")
            save_path = safe_join_download_path(config.save_path, task.get("file_name", "download.bin"))
            save_path.parent.mkdir(parents=True, exist_ok=True)

            download_client, message_obj = await self._resolve_message(tg_clients, task, task_data)
            if not download_client or not message_obj:
                raise RuntimeError("No Telegram client could access this media message")

            await self._download_if_needed(download_client, message_obj, save_path, task)
            await self._forward_if_requested(tg_clients, save_path, task, task_data)

            # 自动上传到阿里云盘
            if config.aliyundrive_upload.enabled:
                aliyundrive_uploader.enabled = True
                aliyundrive_uploader.remote_path = config.aliyundrive_upload.remote_path
                aliyundrive_uploader.delete_after_upload = config.aliyundrive_upload.delete_after_upload
                await aliyundrive_uploader.upload(save_path)

            delete_after = bool(task_data.get("delete_after_forward", False))
            await db_manager.complete_download_task(task, {
                "download_path": "" if delete_after else str(save_path),
                "status": "completed",
            })
            await self._notify_success(tg_clients, task, task_data)

        except FloodWaitError as e:
            wait_seconds = int(getattr(e, "seconds", 30))
            logger.warning(f"Flood wait for task {task_id}: {wait_seconds}s")
            await db_manager.requeue_task(task_id, f"Flood wait {wait_seconds}s")
            asyncio.create_task(self._wake_after(wait_seconds + 1))
        except Exception as e:
            wait_seconds = self._parse_flood_wait_seconds(str(e))
            if wait_seconds:
                logger.warning(f"Flood wait text for task {task_id}: {wait_seconds}s")
                await db_manager.requeue_task(task_id, f"Flood wait {wait_seconds}s")
                asyncio.create_task(self._wake_after(wait_seconds + 1))
            else:
                logger.error(f"Task failed: {task_id}, error: {e}")
                await db_manager.update_task_status(task_id, "failed", str(e))
                await self._notify_failure(tg_clients, task, task_data, e)
        finally:
            self.active_tasks.discard(task_id)

    async def _resolve_message(self, tg_clients, task: Dict[str, Any], task_data: Dict[str, Any]):
        download_client = None
        message_obj = None

        if tg_clients.user_client and await tg_clients.user_client.is_user_authorized():
            try:
                peer_id_str = task.get("channel_id") or task.get("chat_id")
                if not peer_id_str:
                    raise ValueError("task has no chat_id/channel_id")

                access_hash = task_data.get("access_hash") or task.get("access_hash")
                entity = None
                if access_hash and str(peer_id_str).startswith("-100"):
                    cid = int(str(peer_id_str).replace("-100", ""))
                    entity = InputPeerChannel(cid, int(access_hash))
                if not entity:
                    entity = await tg_clients.user_client.get_input_entity(int(peer_id_str))

                messages = await tg_clients.user_client.get_messages(entity, ids=[int(task["message_id"])])
                if messages and messages[0] and messages[0].media:
                    download_client = tg_clients.user_client
                    message_obj = messages[0]
            except Exception as e:
                if self._parse_flood_wait_seconds(str(e)):
                    raise
                logger.debug(f"User client could not resolve message, trying bot client: {e}")

        if not message_obj and tg_clients.bot_client:
            try:
                messages = await tg_clients.bot_client.get_messages(
                    int(task.get("chat_id") or 0),
                    ids=[int(task["message_id"])],
                )
                if messages and messages[0] and messages[0].media:
                    download_client = tg_clients.bot_client
                    message_obj = messages[0]
            except Exception as e:
                logger.error(f"Bot client could not resolve message: {e}")

        return download_client, message_obj

    async def _download_if_needed(self, client, message, save_path: Path, task: Dict[str, Any]):
        expected_size = int(task.get("file_size") or 0)
        if save_path.exists() and expected_size > 0 and save_path.stat().st_size == expected_size:
            logger.info(f"Existing complete file found, skipping download: {save_path.name}")
            return

        await download_engine.download_via_telethon(
            client,
            message,
            str(save_path),
            self.create_progress_callback(task["task_id"]),
        )
        if not save_path.exists() or save_path.stat().st_size == 0:
            raise RuntimeError(f"Download finished but file is missing or empty: {save_path}")

    async def _forward_if_requested(self, tg_clients, save_path: Path, task: Dict[str, Any], task_data: Dict[str, Any]):
        forward_target = task_data.get("forward_target")
        if not forward_target:
            return
        if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
            raise RuntimeError("User client is required for forwarding")

        delete_after = bool(task_data.get("delete_after_forward", False))
        try:
            peer = await self._resolve_forward_peer(tg_clients.user_client, forward_target)
            file_size = save_path.stat().st_size
            if file_size > 2000 * 1024 * 1024:
                me = await tg_clients.user_client.get_me()
                if not getattr(me, "premium", False):
                    raise RuntimeError("File exceeds 2GB and the user account is not premium")

            force_doc = task.get("media_type") == "document"
            uploaded_file = await tg_clients.user_client.upload_file(
                str(save_path),
                part_size_kb=512 if file_size > 100 * 1024 * 1024 else None,
                progress_callback=self.create_progress_callback(task["task_id"]),
            )
            await tg_clients.user_client.send_file(
                peer,
                uploaded_file,
                caption=task_data.get("caption", "") or "",
                force_document=force_doc,
            )
        except Exception as e:
            if "SaveBigFilePartRequest" in str(e) or "file parts is invalid" in str(e):
                await asyncio.sleep(3)
                peer = await self._resolve_forward_peer(tg_clients.user_client, forward_target)
                await tg_clients.user_client.send_file(peer, str(save_path), caption=task_data.get("caption", "") or "")
            else:
                raise
        finally:
            if delete_after and save_path.exists():
                save_path.unlink(missing_ok=True)

    async def _resolve_forward_peer(self, client, target: Any):
        cache_key = str(target).strip()
        if cache_key in self.forward_peer_cache:
            return self.forward_peer_cache[cache_key]

        candidates: list[Any] = [cache_key]
        numeric_target = self._telegram_chat_id_or_none(cache_key)
        if numeric_target is not None:
            candidates.insert(0, numeric_target)

        for candidate in candidates:
            try:
                peer = await client.get_input_entity(candidate)
                self.forward_peer_cache[cache_key] = peer
                return peer
            except Exception:
                pass
            try:
                entity = await client.get_entity(candidate)
                peer = await client.get_input_entity(entity)
                self.forward_peer_cache[cache_key] = peer
                return peer
            except Exception:
                pass

        if numeric_target is not None:
            async for dialog in client.iter_dialogs(limit=None):
                if int(dialog.id) == numeric_target:
                    peer = await client.get_input_entity(dialog.entity)
                    self.forward_peer_cache[cache_key] = peer
                    return peer

                entity_id = getattr(dialog.entity, "id", None)
                if entity_id is not None and str(numeric_target).startswith("-100"):
                    channel_id = int(str(numeric_target).replace("-100", ""))
                    if int(entity_id) == channel_id:
                        peer = await client.get_input_entity(dialog.entity)
                        self.forward_peer_cache[cache_key] = peer
                        return peer

        raise RuntimeError(
            f"Cannot resolve forward target {cache_key}. "
            "Make sure the user account has joined the target channel/group, "
            "or use a @username/link that the account can access."
        )

    async def _notify_success(self, tg_clients, task: Dict[str, Any], task_data: Dict[str, Any]):
        requester = task_data.get("requester_chat_id") or task.get("chat_id")
        requester_id = self._telegram_chat_id_or_none(requester)
        if requester_id is None or not tg_clients.bot_client:
            return
        try:
            await tg_clients.bot_client.send_message(
                requester_id,
                f"Task completed: `{task.get('file_name')}`",
            )
        except Exception as e:
            logger.warning(f"Failed to notify task success: {e}")

    async def _notify_failure(self, tg_clients, task: Dict[str, Any], task_data: Dict[str, Any], error: Exception):
        requester = task_data.get("requester_chat_id")
        requester_id = self._telegram_chat_id_or_none(requester)
        if requester_id is None or not tg_clients.bot_client:
            return
        try:
            await tg_clients.bot_client.send_message(
                requester_id,
                f"Task failed: `{task.get('file_name')}`\n{error}",
            )
        except Exception as e:
            logger.warning(f"Failed to notify task failure: {e}")

    def _telegram_chat_id_or_none(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return None

    async def _wake_after(self, seconds: int):
        await asyncio.sleep(max(1, seconds))
        await self.wake_workers(1)

    def _loads_task_data(self, raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _parse_flood_wait_seconds(self, message: str) -> int | None:
        if "FLOOD_PREMIUM_WAIT" not in message and "FLOOD_WAIT" not in message:
            return None
        match = re.search(r"WAIT_?(\d+)", message)
        return int(match.group(1)) if match else 30

    def create_progress_callback(self, task_id: str):
        last_update_time = 0.0
        last_progress = -1

        async def progress_callback(downloaded, total):
            nonlocal last_update_time, last_progress
            if not total:
                return

            import time

            current_time = time.time()
            progress = int(downloaded / total * 100)
            if progress > last_progress or (current_time - last_update_time) > 2:
                last_progress = progress
                last_update_time = current_time
                await db_manager.update_task_progress(task_id, progress)

        return progress_callback


download_manager = DownloadManager()
