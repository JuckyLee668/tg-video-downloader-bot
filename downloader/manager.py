import asyncio
import datetime
import json
import re
from pathlib import Path
from typing import Any, Dict

from loguru import logger
from telethon.tl.types import DocumentAttributeVideo, InputPeerChannel

from core.config import config
from core.database import db_manager
from core.paths import safe_join_download_path
from downloader.aliyundrive_uploader import aliyundrive_uploader
from downloader.compressor import compressor
from downloader.engine import download_engine
from downloader.external import external_downloader


class DownloadManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.active_tasks: set[str] = set()
        self.worker_tasks: list[asyncio.Task] = []
        self.max_concurrent = max(1, int(config.max_download_task))
        self.forward_peer_cache: dict[str, Any] = {}
        self._progress_msg_ids: dict[str, list[int]] = {}
        self._video_attrs_cache: dict[str, dict] = {}

    async def init(self):
        pending_tasks = await db_manager.get_pending_tasks()
        for _ in pending_tasks:
            await self.queue.put("wake")

        while len(self.worker_tasks) < self.max_concurrent:
            worker_id = len(self.worker_tasks)
            self.worker_tasks.append(asyncio.create_task(self.worker(worker_id)))

        logger.info(f"Download manager initialized with {self.max_concurrent} workers")

    async def add_task(self, task: Dict[str, Any]):
        """Add a task to the download queue. Returns task_id, or 'duplicate' if deduped."""
        task_data = self._loads_task_data(task.get("task_data"))
        is_external = task_data.get("source_type") == "external"

        # File dedup check — skip for external tasks (message_id is a URL hash,
        # not a real Telegram message, so the user may legitimately re-request)
        if config.file_dedup.enabled and not is_external:
            chat_id = task.get("chat_id") or task.get("channel_id")
            message_id = task.get("message_id")
            file_id = task.get("file_id")

            if config.file_dedup.by_message_id and chat_id and message_id:
                if await db_manager.is_already_downloaded(str(chat_id), str(message_id)):
                    logger.info(
                        f"Dedup skipped (by_message_id): chat={chat_id}, msg={message_id}, file={task.get('file_name')}"
                    )
                    return "duplicate"

            if config.file_dedup.by_file_id and file_id:
                if await db_manager.is_file_downloaded(str(file_id)):
                    logger.info(
                        f"Dedup skipped (by_file_id): file_id={file_id}, file={task.get('file_name')}"
                    )
                    return "duplicate"

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
                task = await db_manager.claim_next_task()
                if task:
                    await self.process_task(worker_id, task)
                    # Wake another worker to claim the next pending task
                    await self.queue.put("wake")
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
            save_path = safe_join_download_path(
                config.save_path, task.get("file_name", "download.bin")
            )
            save_path.parent.mkdir(parents=True, exist_ok=True)

            is_external = task_data.get("source_type") == "external"

            # ── Progress notification setup ──────────────────────────────
            progress_msg_ref = None
            requester_id = self._telegram_chat_id_or_none(
                task_data.get("requester_chat_id") or task.get("chat_id")
            )
            if (
                config.progress_notification
                and tg_clients.bot_client
                and requester_id
                and not self._is_channel_or_group(requester_id)
            ):
                # Clean up any stale progress message from a previous attempt (retry case)
                old_ids = self._progress_msg_ids.pop(task_id, None)
                if old_ids:
                    try:
                        await tg_clients.bot_client.delete_messages(requester_id, old_ids[0])
                    except Exception:
                        pass
                try:
                    msg = await tg_clients.bot_client.send_message(
                        requester_id,
                        f"⏳ Downloading `{task.get('file_name')}` — 0%",
                    )
                    self._progress_msg_ids[task_id] = [msg.id]
                    progress_msg_ref = self._progress_msg_ids[task_id]
                except Exception as e:
                    logger.debug(f"Failed to send initial progress message: {e}")

            if is_external:
                # ── External source (yt-dlp) ────────────────────────────
                actual_path = await self._download_external(
                    task, task_data, save_path,
                    bot_client=tg_clients.bot_client if config.progress_notification else None,
                    requester_chat_id=requester_id
                    if config.progress_notification and progress_msg_ref
                    else None,
                    progress_msg_ref=progress_msg_ref,
                )
            else:
                # ── Telegram source ─────────────────────────────────────
                download_client, message_obj = await self._resolve_message(
                    tg_clients, task, task_data
                )
                if not download_client or not message_obj:
                    raise RuntimeError("No Telegram client could access this media message")

                # Cache video attributes for streaming re-upload
                video_attrs = self._extract_video_attributes(message_obj)
                if video_attrs["attributes"]:
                    self._video_attrs_cache[task_id] = video_attrs

                # Download video thumbnail for cover image on forward
                if getattr(message_obj, "video", None):
                    thumb_path = await self._download_thumb(download_client, message_obj, task_id)
                    if thumb_path:
                        self._video_attrs_cache.setdefault(task_id, {})["thumb"] = thumb_path

                actual_path = await self._download_if_needed(
                    download_client,
                    message_obj,
                    save_path,
                    task,
                    bot_client=tg_clients.bot_client if config.progress_notification else None,
                    requester_chat_id=requester_id
                    if config.progress_notification and progress_msg_ref
                    else None,
                    progress_msg_ref=progress_msg_ref,
                )

            # ── Large file compression ────────────────────────────────
            if config.large_file.enabled and actual_path.stat().st_size > config.large_file.threshold_mb * 1024 * 1024:
                actual_path = await self._handle_large_file(
                    actual_path, task, task_data,
                    progress_msg_ref=progress_msg_ref,
                    requester_id=requester_id,
                )

            # ── Shared post-download pipeline ───────────────────────────
            await self._forward_if_requested(tg_clients, actual_path, task, task_data)

            # 外部任务根据 action 决定是否上传云盘
            cloud_enabled = config.aliyundrive_upload.enabled
            local_enabled = config.local_forward.enabled and bool(config.local_forward.target_chat)
            if is_external:
                action = task_data.get("action", "download")
                cloud_enabled = action in ("cloud", "all")
                local_enabled = False  # 外部任务不使用 local_forward 配置

            if cloud_enabled:
                aliyundrive_uploader.enabled = True
                aliyundrive_uploader.remote_path = config.aliyundrive_upload.remote_path
                aliyundrive_uploader.delete_after_upload = (
                    config.aliyundrive_upload.delete_after_upload
                )
                upload_ok = await aliyundrive_uploader.upload(actual_path)
                if not upload_ok:
                    logger.warning(
                        f"AliyunDrive upload failed for task {task_id}, but download succeeded"
                    )

            if local_enabled:
                await self._auto_forward_to_local(tg_clients, actual_path, task, task_data)

            # ── Cleanup: delete local file if configured ───────────
            should_delete = bool(task_data.get("delete_after_forward", False))
            # aliyundrive may have already deleted it; check existence first
            if should_delete and actual_path.exists():
                actual_path.unlink(missing_ok=True)
                logger.info(f"Deleted local file after forward: {actual_path.name}")

            await db_manager.complete_download_task(
                task,
                {
                    "download_path": "" if should_delete else str(actual_path),
                    "status": "completed",
                },
            )
            await self._notify_success(tg_clients, task, task_data)

        except FloodWaitError as e:
            wait_seconds = int(getattr(e, "seconds", 30))
            logger.warning(f"Flood wait for task {task_id}: {wait_seconds}s")
            await db_manager.requeue_task(task_id, f"Flood wait {wait_seconds}s")
            asyncio.create_task(self._wake_after(wait_seconds + 1))
        except Exception as e:
            # 外部下载的永久错误不重试
            if is_external and ("文件为空" in str(e) or "无法提取" in str(e)):
                logger.error(f"External task failed permanently: {task_id}, error: {e}")
                await db_manager.update_task_status(task_id, "failed", str(e))
                await self._notify_failure(tg_clients, task, task_data, e)
            else:
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
            cached = self._video_attrs_cache.pop(task_id, None)
            if cached and cached.get("thumb"):
                thumb_path = cached["thumb"]
                if isinstance(thumb_path, str):
                    thumb_path = Path(thumb_path)
                try:
                    thumb_path.unlink(missing_ok=True)
                except Exception:
                    pass
            # Safety net: clean up any remaining progress message references
            self._progress_msg_ids.pop(task_id, None)

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

                messages = await tg_clients.user_client.get_messages(
                    entity, ids=[int(task["message_id"])]
                )
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

    async def _download_if_needed(
        self,
        client,
        message,
        save_path: Path,
        task: Dict[str, Any],
        bot_client=None,
        requester_chat_id=None,
        progress_msg_ref=None,
    ) -> Path:
        expected_size = int(task.get("file_size") or 0)
        if save_path.exists() and expected_size > 0 and save_path.stat().st_size == expected_size:
            logger.info(f"Existing complete file found, skipping download: {save_path.name}")
            return save_path

        actual_path = await download_engine.download_via_telethon(
            client,
            message,
            str(save_path),
            self.create_progress_callback(
                task["task_id"],
                task.get("file_name", ""),
                bot_client,
                requester_chat_id,
                progress_msg_ref,
            ),
        )
        if actual_path is None:
            raise RuntimeError(f"Download returned None: {save_path}")

        # Telethon's download_media may add extension; use the actual path returned
        actual_path = Path(actual_path)
        if not actual_path.exists() or actual_path.stat().st_size == 0:
            raise RuntimeError(f"Download finished but file is missing or empty: {actual_path}")

        # If Telethon didn't add an extension, try to infer one from the message
        if not actual_path.suffix and message.file and message.file.mime_type:
            ext = self._mime_to_ext(message.file.mime_type)
            if ext:
                new_path = actual_path.with_suffix(ext)
                actual_path.rename(new_path)
                actual_path = new_path
                logger.info(f"Added inferred extension {ext} -> {actual_path.name}")

        # If Telethon saved to a different path than save_path, remove the original placeholder
        if actual_path != save_path and save_path.exists() and save_path.stat().st_size == 0:
            save_path.unlink(missing_ok=True)

        # ── Smart rename ──────────────────────────────────────────────
        if config.file_rename.enabled:
            actual_path = await self._smart_rename(actual_path, message, task)

        return actual_path

    async def _smart_rename(self, file_path: Path, message, task: Dict[str, Any]) -> Path:
        """Rename downloaded file according to config.file_rename.pattern"""
        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M%S")

        # Extract channel info from the message object
        channel_title = ""
        channel_username = ""
        try:
            chat = getattr(message, "chat", None) or getattr(message, "peer_id", None)
            if chat:
                channel_title = getattr(chat, "title", "") or ""
                channel_username = getattr(chat, "username", "") or ""
        except Exception:
            pass

        original_name = file_path.stem
        ext = file_path.suffix  # includes the dot, e.g. ".mp4"

        pattern = config.file_rename.pattern
        replacements = {
            "{channel_title}": channel_title or "",
            "{channel_username}": channel_username or "",
            "{date}": date_str,
            "{time}": time_str,
            "{original_name}": original_name,
            "{ext}": ext,
        }

        new_name = pattern
        for placeholder, value in replacements.items():
            new_name = new_name.replace(placeholder, value)

        # Ensure extension is always preserved, even if pattern lacks {ext}
        if ext and not new_name.endswith(ext):
            new_name += ext

        new_path = file_path.parent / new_name
        new_path.parent.mkdir(parents=True, exist_ok=True)

        if new_path != file_path:
            if new_path.exists():
                counter = 1
                stem = Path(new_name).stem
                suffix = Path(new_name).suffix
                while (file_path.parent / f"{stem}_{counter}{suffix}").exists():
                    counter += 1
                new_path = file_path.parent / f"{stem}_{counter}{suffix}"

            file_path.rename(new_path)
            logger.info(f"Smart renamed: {file_path.name} -> {new_path.name}")

        return new_path

    async def _download_external(
        self,
        task: Dict[str, Any],
        task_data: Dict[str, Any],
        save_path: Path,
        bot_client=None,
        requester_chat_id=None,
        progress_msg_ref=None,
    ) -> Path:
        """Download from external source (Twitter/X etc.) via yt-dlp."""
        url = task_data.get("source_url", "")
        if not url:
            raise RuntimeError("External task missing source_url")

        progress_callback = self.create_progress_callback(
            task["task_id"],
            task.get("file_name", ""),
            bot_client,
            requester_chat_id,
            progress_msg_ref,
        )

        actual_path_str = await external_downloader.download(
            url, str(save_path), progress_callback=progress_callback
        )
        actual_path = Path(actual_path_str)

        # Smart rename if configured
        if config.file_rename.enabled and actual_path.exists():
            actual_path = await self._smart_rename_external(actual_path, task_data)

        # Build video attributes from yt-dlp info for streaming re-upload
        ext_info = task_data.get("external_info", {})
        if ext_info.get("width") and ext_info.get("height"):
            from telethon.tl.types import DocumentAttributeVideo

            video_attrs = {
                "supports_streaming": True,
                "attributes": [
                    DocumentAttributeVideo(
                        duration=float(ext_info.get("duration", 0) or 0),
                        w=int(ext_info.get("width", 0) or 0),
                        h=int(ext_info.get("height", 0) or 0),
                        supports_streaming=True,
                    )
                ],
                "nosound": False,
            }

            # Download thumbnail for cover image on forward
            thumb_url = ext_info.get("thumbnail", "") or ""
            if thumb_url:
                thumb_path = await self._download_external_thumb(thumb_url, task["task_id"])
                if thumb_path:
                    video_attrs["thumb"] = thumb_path

            self._video_attrs_cache[task["task_id"]] = video_attrs

        return actual_path

    async def _smart_rename_external(self, file_path: Path, task_data: Dict[str, Any]) -> Path:
        """Smart rename for external downloads using info from yt-dlp."""
        import datetime

        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M%S")

        ext_info = task_data.get("external_info", {})
        uploader = ext_info.get("uploader", "") or ""

        ext = file_path.suffix
        original_name = file_path.stem

        pattern = config.file_rename.pattern
        replacements = {
            "{channel_title}": uploader,
            "{channel_username}": uploader,
            "{date}": date_str,
            "{time}": time_str,
            "{original_name}": original_name,
            "{ext}": ext,
        }

        new_name = pattern
        for placeholder, value in replacements.items():
            new_name = new_name.replace(placeholder, value)

        if ext and not new_name.endswith(ext):
            new_name += ext

        new_path = file_path.parent / new_name
        new_path.parent.mkdir(parents=True, exist_ok=True)

        if new_path != file_path:
            if new_path.exists():
                counter = 1
                stem = Path(new_name).stem
                suffix = Path(new_name).suffix
                while (file_path.parent / f"{stem}_{counter}{suffix}").exists():
                    counter += 1
                new_path = file_path.parent / f"{stem}_{counter}{suffix}"

            file_path.rename(new_path)
            logger.info(f"Smart renamed (external): {file_path.name} -> {new_path.name}")

        return new_path

    async def _handle_large_file(
        self,
        actual_path: Path,
        task: Dict[str, Any],
        task_data: Dict[str, Any],
        progress_msg_ref=None,
        requester_id=None,
    ) -> Path:
        """Check file size and apply configured large-file action.

        Returns (possibly compressed) path.
        """
        threshold_bytes = config.large_file.threshold_mb * 1024 * 1024
        if actual_path.stat().st_size <= threshold_bytes:
            return actual_path

        file_name = task.get("file_name", actual_path.name)
        logger.info(
            f"Large file detected: {file_name} "
            f"({actual_path.stat().st_size / 1024 / 1024:.0f}MB > {config.large_file.threshold_mb}MB threshold)"
        )

        if not compressor.is_available():
            logger.warning("ffmpeg not available, cannot compress large file")
            return actual_path

        # Explicit compress request (from interactive choice or forward handler)
        if task_data.get("compress"):
            action = "compress"
        else:
            action = config.large_file.action
            # "ask" mode: in non-interactive contexts (batch/web), default to compress
            if action == "ask":
                action = "compress"

        if action == "skip":
            logger.info(f"Skipping compression for large file: {file_name}")
            return actual_path

        if action == "compress":
            if requester_id and progress_msg_ref:
                try:
                    from telegram.client import tg_clients
                    if tg_clients.bot_client:
                        await tg_clients.bot_client.edit_message(
                            requester_id,
                            progress_msg_ref[0],
                            f"🗜️ 正在压缩 `{file_name}` ...",
                        )
                except Exception:
                    pass

            target_size = config.large_file.threshold_mb * 1024 * 1024
            actual_path = await compressor.compress_video(
                str(actual_path),
                target_size,
                progress_callback=self.create_progress_callback(task["task_id"]),
                crf=config.large_file.crf,
                max_bitrate=config.large_file.max_bitrate,
            )
            logger.info(f"Compressed large file: {actual_path.name} ({actual_path.stat().st_size / 1024 / 1024:.0f}MB)")

        return actual_path

    async def _auto_forward_to_local(self, tg_clients, save_path: Path, task: Dict[str, Any], task_data: Dict[str, Any] = None):
        """下载完成后自动转发到配置的本地聊天"""
        target = config.local_forward.target_chat.strip()
        if not target:
            return
        if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
            logger.warning("User client not available for local forward, skipping")
            return

        try:
            peer, reply_to = await self._resolve_forward_peer(tg_clients.user_client, target)
            file_size = save_path.stat().st_size
            if file_size > 2000 * 1024 * 1024:
                me = await tg_clients.user_client.get_me()
                if not getattr(me, "premium", False):
                    # Try compression if enabled and not already tried
                    if config.large_file.enabled and compressor.is_available():
                        already_compressed = (task_data or {}).get("compressed", False)
                        if not already_compressed:
                            logger.info(f"Compressing large file before local forward: {save_path.name}")
                            save_path = await self._handle_large_file(save_path, task, task_data or {})
                            if task_data is not None:
                                task_data["compressed"] = True
                            file_size = save_path.stat().st_size
                            if file_size <= 2000 * 1024 * 1024:
                                pass  # Proceed with forward
                            else:
                                logger.warning(
                                    f"File still over 2GB after compression, skipping local forward: {save_path.name}"
                                )
                                return
                        else:
                            logger.warning(
                                f"File still over 2GB after compression, skipping local forward: {save_path.name}"
                            )
                            return
                    else:
                        logger.warning(
                            f"File over 2GB and account not premium, skipping local forward: {save_path.name}"
                        )
                        return

            video_attrs = self._video_attrs_cache.pop(task["task_id"], {})
            force_doc = task.get("media_type") == "document"
            uploaded = await tg_clients.user_client.upload_file(
                str(save_path),
                part_size_kb=512 if file_size > 100 * 1024 * 1024 else None,
                progress_callback=self.create_progress_callback(task["task_id"]),
            )
            await tg_clients.user_client.send_file(
                peer,
                uploaded,
                caption=f"📥 {task.get('file_name', '')}",
                force_document=force_doc,
                reply_to=reply_to,
                attributes=video_attrs.get("attributes"),
                supports_streaming=video_attrs.get("supports_streaming", False),
                nosound_video=video_attrs.get("nosound") or None,
                thumb=video_attrs.get("thumb"),
            )
            logger.info(f"Auto forwarded to local chat {target}: {save_path.name}")

            if config.local_forward.delete_after_forward and save_path.exists():
                save_path.unlink(missing_ok=True)
                logger.info(f"Deleted local file after local forward: {save_path.name}")
        except Exception as e:
            logger.error(f"Auto forward to local chat failed for {save_path.name}: {e}")

    async def _forward_if_requested(
        self, tg_clients, save_path: Path, task: Dict[str, Any], task_data: Dict[str, Any]
    ):
        forward_target = task_data.get("forward_target")
        if not forward_target:
            return
        if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
            raise RuntimeError("User client is required for forwarding")

        video_attrs = self._video_attrs_cache.pop(task["task_id"], {})
        try:
            peer, reply_to = await self._resolve_forward_peer(tg_clients.user_client, forward_target)
            file_size = save_path.stat().st_size
            if file_size > 2000 * 1024 * 1024:
                me = await tg_clients.user_client.get_me()
                if not getattr(me, "premium", False):
                    # Try compression if enabled and not already attempted
                    if config.large_file.enabled and compressor.is_available():
                        already_compressed = task_data.get("compressed", False)
                        if not already_compressed:
                            logger.info(f"Compressing large file before forward: {save_path.name}")
                            save_path = await self._handle_large_file(save_path, task, task_data)
                            task_data["compressed"] = True
                            file_size = save_path.stat().st_size
                            if file_size <= 2000 * 1024 * 1024:
                                pass  # Proceed with forward
                            else:
                                raise RuntimeError(
                                    "File still exceeds 2GB after compression. "
                                    "Try a lower CRF value in settings, or upgrade to Telegram Premium."
                                )
                        else:
                            raise RuntimeError(
                                "File still exceeds 2GB after compression. "
                                "Try a lower CRF value in settings, or upgrade to Telegram Premium."
                            )
                    else:
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
                reply_to=reply_to,
                attributes=video_attrs.get("attributes"),
                supports_streaming=video_attrs.get("supports_streaming", False),
                nosound_video=video_attrs.get("nosound") or None,
                thumb=video_attrs.get("thumb"),
            )
        except Exception as e:
            if "SaveBigFilePartRequest" in str(e) or "file parts is invalid" in str(e):
                await asyncio.sleep(3)
                peer, reply_to = await self._resolve_forward_peer(tg_clients.user_client, forward_target)
                await tg_clients.user_client.send_file(
                    peer,
                    str(save_path),
                    caption=task_data.get("caption", "") or "",
                    reply_to=reply_to,
                    attributes=video_attrs.get("attributes"),
                    supports_streaming=video_attrs.get("supports_streaming", False),
                    nosound_video=video_attrs.get("nosound") or None,
                    thumb=video_attrs.get("thumb"),
                )
            else:
                raise

    async def _resolve_forward_peer(self, client, target: Any):
        """Resolve a forward target to (peer, reply_to_msg_id).

        Extracts the optional message ID from t.me/c/CHANNEL/MSGID links
        so the forwarded file can be sent as a reply to that message.
        """
        cache_key = str(target).strip()
        if cache_key in self.forward_peer_cache:
            return self.forward_peer_cache[cache_key]

        candidates: list[Any] = [cache_key]
        reply_to_msg_id: int | None = None

        parsed_target = None
        if "t.me/" in cache_key or "telegram.me/" in cache_key:
            import re

            private_match = re.search(
                r"(?:https?://)?(?:t\.me|telegram\.me)/c/(\d+)(?:/(\d+))?", cache_key
            )
            if private_match:
                parsed_target = int(f"-100{private_match.group(1)}")
                if private_match.group(2):
                    reply_to_msg_id = int(private_match.group(2))
            else:
                preview_match = re.search(
                    r"(?:https?://)?(?:t\.me|telegram\.me)/s/([^/+?#\s]+)", cache_key
                )
                if preview_match:
                    parsed_target = f"@{preview_match.group(1)}"
                else:
                    public_match = re.search(
                        r"(?:https?://)?(?:t\.me|telegram\.me)/([^/+?#\s]+)(?:/(\d+))?", cache_key
                    )
                    if public_match:
                        username = public_match.group(1)
                        if public_match.group(2):
                            reply_to_msg_id = int(public_match.group(2))
                        if username.lower() not in (
                            "joinchat",
                            "contact",
                            "share",
                            "addstickers",
                            "addtheme",
                            "bg",
                            "s",
                        ):
                            parsed_target = f"@{username}"

        if parsed_target is not None:
            candidates.insert(0, parsed_target)
            numeric_target = self._telegram_chat_id_or_none(parsed_target)
        else:
            numeric_target = self._telegram_chat_id_or_none(cache_key)
            if numeric_target is not None:
                candidates.insert(0, numeric_target)

        for candidate in candidates:
            try:
                peer = await client.get_input_entity(candidate)
                result = (peer, reply_to_msg_id)
                self.forward_peer_cache[cache_key] = result
                return result
            except Exception:
                pass
            try:
                entity = await client.get_entity(candidate)
                peer = await client.get_input_entity(entity)
                result = (peer, reply_to_msg_id)
                self.forward_peer_cache[cache_key] = result
                return result
            except Exception:
                pass

        if numeric_target is not None:
            async for dialog in client.iter_dialogs(limit=None):
                if int(dialog.id) == numeric_target:
                    peer = await client.get_input_entity(dialog.entity)
                    result = (peer, reply_to_msg_id)
                    self.forward_peer_cache[cache_key] = result
                    return result

                entity_id = getattr(dialog.entity, "id", None)
                if entity_id is not None and str(numeric_target).startswith("-100"):
                    channel_id = int(str(numeric_target).replace("-100", ""))
                    if int(entity_id) == channel_id:
                        peer = await client.get_input_entity(dialog.entity)
                        result = (peer, reply_to_msg_id)
                        self.forward_peer_cache[cache_key] = result
                        return result

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
        if self._is_channel_or_group(requester_id):
            return

        # Edit the progress message to show completion, then clean up
        task_id = task.get("task_id", "")
        progress_ids = self._progress_msg_ids.pop(task_id, None)
        if progress_ids:
            try:
                await tg_clients.bot_client.edit_message(
                    requester_id,
                    progress_ids[0],
                    f"✅ Completed: `{task.get('file_name')}`",
                )
                return
            except Exception:
                pass

        try:
            await tg_clients.bot_client.send_message(
                requester_id,
                f"✅ Completed: `{task.get('file_name')}`",
            )
        except Exception as e:
            logger.warning(f"Failed to notify task success: {e}")

    async def _notify_failure(
        self, tg_clients, task: Dict[str, Any], task_data: Dict[str, Any], error: Exception
    ):
        requester = task_data.get("requester_chat_id")
        requester_id = self._telegram_chat_id_or_none(requester)
        if requester_id is None or not tg_clients.bot_client:
            return
        if self._is_channel_or_group(requester_id):
            return

        # Clean up stale progress message
        task_id = task.get("task_id", "")
        progress_ids = self._progress_msg_ids.pop(task_id, None)
        if progress_ids:
            try:
                await tg_clients.bot_client.delete_messages(requester_id, progress_ids[0])
            except Exception:
                pass

        try:
            await tg_clients.bot_client.send_message(
                requester_id,
                f"❌ Failed: `{task.get('file_name')}`\n{error}",
            )
        except Exception as e:
            logger.warning(f"Failed to notify task failure: {e}")

    @staticmethod
    def _is_channel_or_group(chat_id: Any) -> bool:
        """Return True when chat_id refers to a channel or supergroup (not a private chat)."""
        try:
            cid = int(str(chat_id))
            return cid < 0
        except (TypeError, ValueError):
            return False

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

    @staticmethod
    def _mime_to_ext(mime_type: str) -> str:
        """Map MIME type to file extension (with dot)."""
        mapping = {
            "video/mp4": ".mp4",
            "video/x-matroska": ".mkv",
            "video/webm": ".webm",
            "video/quicktime": ".mov",
            "video/x-msvideo": ".avi",
            "video/mpeg": ".mpeg",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "audio/ogg": ".ogg",
            "audio/wav": ".wav",
            "audio/flac": ".flac",
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "application/pdf": ".pdf",
            "application/zip": ".zip",
            "application/x-tar": ".tar.gz",
            "application/x-7z-compressed": ".7z",
            "application/x-rar-compressed": ".rar",
            "text/plain": ".txt",
            "application/json": ".json",
        }
        return mapping.get(mime_type.split(";")[0].strip(), "")

    @staticmethod
    def _extract_video_attributes(message) -> dict:
        """Extract video attributes from a source message for send_file reuse.

        Returns a dict with keys: supports_streaming, attributes, nosound.
        All values are None when the message does not contain a video.
        """
        result: dict = {
            "supports_streaming": False,
            "attributes": None,
            "nosound": False,
        }

        doc = getattr(message, "document", None)
        if not doc:
            return result

        for attr in doc.attributes or []:
            if isinstance(attr, DocumentAttributeVideo):
                result["attributes"] = [
                    DocumentAttributeVideo(
                        duration=attr.duration,
                        w=attr.w,
                        h=attr.h,
                        supports_streaming=True,
                        nosound=getattr(attr, "nosound", False),
                        preload_prefix_size=getattr(attr, "preload_prefix_size", None),
                    )
                ]
                result["supports_streaming"] = True
                result["nosound"] = bool(getattr(attr, "nosound", False))
                break

        return result

    @staticmethod
    async def _download_thumb(client, message, task_id: str) -> str | None:
        """Download the built-in video thumbnail from a Telegram message.

        Returns the path to the JPEG thumbnail file, or None if unavailable.
        """
        try:
            result = await client.download_media(message, thumb=-1)
            if result and Path(result).stat().st_size > 0:
                # Move to a stable location keyed by task_id so it doesn't
                # conflict with the download engine's temp paths.
                thumb_dir = Path(config.save_path).parent / ".tg_thumbs"
                thumb_dir.mkdir(parents=True, exist_ok=True)
                dest = thumb_dir / f"thumb_{task_id}.jpg"
                src = Path(result)
                if src != dest:
                    import os
                    os.rename(str(src), str(dest))
                return str(dest)
        except Exception as e:
            logger.debug(f"Failed to download thumb for task {task_id}: {e}")
        return None

    @staticmethod
    async def _download_external_thumb(url: str, task_id: str) -> str | None:
        """Download thumbnail image from a URL (e.g. Twitter video thumbnail).

        Returns the path to the JPEG file, or None on failure.
        """
        try:
            import httpx

            thumb_dir = Path(config.save_path).parent / ".tg_thumbs"
            thumb_dir.mkdir(parents=True, exist_ok=True)
            dest = thumb_dir / f"thumb_{task_id}.jpg"

            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.get(url)
                if resp.status_code == 200 and len(resp.content) > 0:
                    dest.write_bytes(resp.content)
                    return str(dest)
        except Exception as e:
            logger.debug(f"Failed to download external thumb for task {task_id}: {e}")
        return None

    def create_progress_callback(
        self,
        task_id: str,
        file_name: str = "",
        bot_client=None,
        requester_chat_id=None,
        progress_msg_ref=None,
    ):
        last_update_time = 0.0
        last_progress = -1
        last_notified_threshold = 0

        async def progress_callback(downloaded, total):
            nonlocal last_update_time, last_progress, last_notified_threshold
            if not total:
                return

            import time

            current_time = time.time()
            progress = int(downloaded / total * 100)
            if progress > last_progress or (current_time - last_update_time) > 2:
                last_progress = progress
                last_update_time = current_time
                await db_manager.update_task_progress(task_id, progress)

            # Progress push — edit message at 20% thresholds
            if bot_client and requester_chat_id and progress_msg_ref is not None:
                threshold = (progress // 20) * 20
                if threshold > last_notified_threshold and threshold > 0:
                    last_notified_threshold = threshold
                    msg_id = progress_msg_ref[0]
                    try:
                        await bot_client.edit_message(
                            requester_chat_id,
                            msg_id,
                            f"⏳ Downloading `{file_name}` — {threshold}%",
                        )
                    except Exception:
                        pass

        return progress_callback


download_manager = DownloadManager()
