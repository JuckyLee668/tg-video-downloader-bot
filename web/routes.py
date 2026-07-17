import asyncio
import hashlib
import hmac
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import aiosqlite
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from core.config import ProxyConfig, config
from core.database import db_manager
from downloader.manager import download_manager
from telegram import search
from telegram.client import tg_clients
from telegram.handlers.thumbnail import ensure_thumb_dir, generate_thumbnails
from telegram.handlers.utils import format_size, message_file_name
from web.api_models import (
    AliyunSettingsUpdate,
    BatchDeleteRequest,
    ConnectRequest,
    DefaultActionUpdate,
    DownloadBatchRequest,
    DownloadSettingsUpdate,
    FileSettingsUpdate,
    ForwardRequest,
    HistoryDeleteRequest,
    JoinRequest,
    LoginSendCodeRequest,
    LoginSignInRequest,
    ProxyConfigRequest,
    SearchKeywordRequest,
    SearchRecentRequest,
    SearchTimeRequest,
    TaskIdRequest,
)

WEB_API_FAIL_COUNT = 0
WEB_API_LOCKED = False


def _mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep * 2)}{value[-keep:]}"


def _validate_telegram_init_data(init_data: str) -> dict | None:
    """Validate Telegram Mini App initData signature.

    Returns the parsed user dict on success, None on failure.
    See: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    bot_token = config.bot_token
    if not bot_token:
        return None

    # Parse the init_data query string
    parsed = parse_qs(init_data)
    # parse_qs returns {key: [value, ...]} — flatten to single values
    params = {k: v[0] for k, v in parsed.items()}

    received_hash = params.pop("hash", None)
    if not received_hash:
        return None

    # Build data-check-string: sorted keys, "key=value" pairs joined by \n
    data_check_string = "\n".join(
        f"{k}={params[k]}" for k in sorted(params.keys())
    )

    # secret_key = HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    # computed_hash = hex(HMAC-SHA256(data_check_string, secret_key))
    computed_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    # Parse user info from the init data (JSON-encoded in the "user" param)
    import json
    try:
        user = json.loads(params.get("user", "{}"))
        return user
    except (json.JSONDecodeError, TypeError):
        return {"id": params.get("user", "")}


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
):
    global WEB_API_FAIL_COUNT, WEB_API_LOCKED

    # ── Mini App auth: validate Telegram initData ──
    if x_telegram_init_data:
        user = _validate_telegram_init_data(x_telegram_init_data)
        if user is not None:
            # Store user id in request state for downstream use
            request.state.tg_user = user
            return
        else:
            raise HTTPException(status_code=401, detail="Invalid Telegram initData signature")

    # ── Legacy WEB_API_KEY auth ──
    expected = (os.getenv("WEB_API_KEY") or "").strip()
    if config.environment.lower() in {"prod", "production"} and not expected:
        raise HTTPException(status_code=503, detail="WEB_API_KEY is required in production")
    if not expected:
        return
    if WEB_API_LOCKED:
        raise HTTPException(status_code=423, detail="WEB_API_KEY locked; restart the service to retry")

    provided = (x_api_key or "").strip()
    if not provided:
        raise HTTPException(status_code=401, detail="WEB_API_KEY missing")

    if not hmac.compare_digest(provided, expected):
        WEB_API_FAIL_COUNT += 1
        if WEB_API_FAIL_COUNT >= 3:
            WEB_API_LOCKED = True
            raise HTTPException(status_code=401, detail="WEB_API_KEY locked after 3 failed attempts")
        raise HTTPException(status_code=401, detail=f"WEB_API_KEY invalid ({WEB_API_FAIL_COUNT}/3)")

    WEB_API_FAIL_COUNT = 0


router = APIRouter(dependencies=[Depends(require_api_key)])


@router.get("/config")
async def get_config():
    return {
        "bot_token": _mask_secret(config.bot_token),
        "user_api_id": config.user_api.api_id,
        "user_api_hash": _mask_secret(config.user_api.api_hash or ""),
        "proxy": config.proxy.model_dump() if config.proxy else None,
        "save_path": config.save_path,
        "max_download_task": config.max_download_task,
        "media_types": config.media_types,
        "default_action": config.default_action.model_dump(),
        "local_forward": config.local_forward.model_dump(),
        "file_rename": config.file_rename.model_dump(),
        "file_dedup": config.file_dedup.model_dump(),
        "aliyundrive_upload": config.aliyundrive_upload.model_dump(),
        "progress_notification": config.progress_notification,
        "batch_size": config.batch_size,
        "adaptive_concurrency": config.adaptive_concurrency,
        "always_fresh_download": config.always_fresh_download,
    }


@router.post("/config/proxy")
async def set_proxy(req: ProxyConfigRequest):
    try:
        proxy_cfg = ProxyConfig(**req.model_dump())
        config.proxy = proxy_cfg
        config.user_api.proxy = proxy_cfg
        config.save()
        return {"status": "success", "proxy": proxy_cfg.model_dump()}
    except Exception as e:
        logger.exception(f"Failed to set proxy: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/config/default-action")
async def set_default_action(req: DefaultActionUpdate):
    try:
        updates = req.model_dump(exclude_none=True, exclude_unset=True)
        for key, value in updates.items():
            setattr(config.default_action, key, value)
        config.save()
        return {"status": "success", "default_action": config.default_action.model_dump()}
    except Exception as e:
        logger.exception(f"Failed to set default action: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/config/download")
async def set_download_settings(req: DownloadSettingsUpdate):
    try:
        updates = req.model_dump(exclude_none=True, exclude_unset=True)
        for key, value in updates.items():
            if hasattr(config, key):
                setattr(config, key, value)
        config.save()
        return {"status": "success"}
    except Exception as e:
        logger.exception(f"Failed to set download settings: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/config/file")
async def set_file_settings(req: FileSettingsUpdate):
    try:
        updates = req.model_dump(exclude_none=True, exclude_unset=True)
        if "file_rename_enabled" in updates:
            config.file_rename.enabled = updates["file_rename_enabled"]
        if "file_rename_pattern" in updates:
            config.file_rename.pattern = updates["file_rename_pattern"]
        if "file_dedup_enabled" in updates:
            config.file_dedup.enabled = updates["file_dedup_enabled"]
        if "file_dedup_by_message_id" in updates:
            config.file_dedup.by_message_id = updates["file_dedup_by_message_id"]
        if "file_dedup_by_file_id" in updates:
            config.file_dedup.by_file_id = updates["file_dedup_by_file_id"]
        config.save()
        return {"status": "success", "file_rename": config.file_rename.model_dump(), "file_dedup": config.file_dedup.model_dump()}
    except Exception as e:
        logger.exception(f"Failed to set file settings: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/config/aliyun")
async def set_aliyun_settings(req: AliyunSettingsUpdate):
    try:
        updates = req.model_dump(exclude_none=True, exclude_unset=True)
        for key, value in updates.items():
            if hasattr(config.aliyundrive_upload, key):
                setattr(config.aliyundrive_upload, key, value)
        config.save()
        return {"status": "success", "aliyundrive_upload": config.aliyundrive_upload.model_dump()}
    except Exception as e:
        logger.exception(f"Failed to set aliyun settings: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/queue/delete")
async def delete_task(req: TaskIdRequest):
    await db_manager.delete_download_task(req.task_id)
    return {"status": "success"}


@router.post("/queue/batch-delete")
async def batch_delete_tasks(req: BatchDeleteRequest):
    for task_id in req.task_ids:
        await db_manager.delete_download_task(task_id)
    return {"status": "success", "deleted": len(req.task_ids)}


@router.post("/queue/clear")
async def clear_queue_route():
    deleted = await db_manager.clear_pending_tasks()
    return {"status": "success", "deleted": deleted}


@router.post("/history/delete")
async def delete_history(req: HistoryDeleteRequest):
    deleted = await db_manager.delete_history_items(req.ids)
    return {"status": "success", "deleted": deleted}


@router.post("/history/clear")
async def clear_history():
    await db_manager.clear_history()
    return {"status": "success"}


@router.post("/queue/retry-failed")
async def retry_failed():
    async with aiosqlite.connect(db_manager.db_path) as db:
        await db.execute("UPDATE download_queue SET status = 'pending', retry_count = 0 WHERE status = 'failed'")
        await db.commit()
    await download_manager.wake_workers()
    return {"status": "success"}


@router.get("/stats")
async def get_stats():
    summary = await db_manager.get_stats_summary()
    return {
        "active_tasks": len(download_manager.active_tasks),
        "queued_tasks": summary.get("pending", 0),
        "total_downloads": summary.get("completed", 0),
        "successful_downloads": summary.get("completed", 0),
        "failed_downloads": summary.get("failed", 0),
        "total_size": summary.get("total_size", 0),
    }


@router.get("/queue")
async def get_queue():
    result = await db_manager.get_download_list(1, 100)
    return result["items"]


@router.get("/history")
async def get_history(page: int = 1, size: int = 50):
    return await db_manager.get_history_list(page, size)


@router.get("/channels")
async def get_channels():
    return await db_manager.get_connected_channels()


@router.post("/channels/connect")
async def connect_channel(req: ConnectRequest):
    await _ensure_user_client()
    try:
        searcher = await get_searcher()
        return await searcher.connect_channel(req.identifier)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/channels/join")
async def join_channel(req: JoinRequest):
    await _ensure_user_client()
    try:
        searcher = await get_searcher()
        await searcher.join_channel(req.link)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


async def _ensure_user_client():
    if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
        raise HTTPException(status_code=401, detail="User client not logged in")


async def get_searcher():
    if not search.searcher:
        if tg_clients.user_client and await tg_clients.user_client.is_user_authorized():
            from telegram.search import init_searcher

            init_searcher(tg_clients.user_client)
        else:
            raise HTTPException(status_code=400, detail="Searcher is not initialized; login first")
    return search.searcher


@router.post("/search/recent")
async def search_recent(req: SearchRecentRequest):
    searcher = await get_searcher()
    try:
        messages = await searcher.get_recent(req.limit, req.media_type, offset_id=req.offset_id or 0)
        return [serialize_message(message) for message in messages]
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/search/keyword")
async def search_keyword(req: SearchKeywordRequest):
    searcher = await get_searcher()
    try:
        messages = await searcher.search_keyword(req.keyword, req.limit, req.media_type, offset_id=req.offset_id or 0)
        return [serialize_message(message) for message in messages]
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/search/time")
async def search_time(req: SearchTimeRequest):
    searcher = await get_searcher()
    try:
        start_date = datetime.strptime(req.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(req.end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        if end_date < start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        messages = await searcher.search_by_time(start_date, end_date, req.limit, req.media_type, offset_id=req.offset_id or 0)
        return [serialize_message(message) for message in messages]
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ── 异步缩略图生成 ────────────────────────────────────────

_thumb_tasks: dict[str, dict] = {}  # task_id -> {"status": "running"|"done", "thumbs": [{msg_id, url, name}, ...]}


class ThumbnailRequest(BaseModel):
    """缩略图生成请求"""
    messages: list[dict]  # [{"id": int, "chat_id": str, "media_type": str}, ...]


@router.post("/search/thumbnails/start")
async def start_thumbnail_gen(req: ThumbnailRequest):
    """异步启动缩略图生成，返回 task_id"""
    import uuid

    if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
        raise HTTPException(status_code=401, detail="User client not logged in")

    task_id = str(uuid.uuid4())[:8]
    _thumb_tasks[task_id] = {"status": "running", "thumbs": [], "total": len(req.messages)}

    async def _gen():
        try:
            await ensure_thumb_dir()

            # Fetch messages and keep index mapping back to request items
            msg_items: list[tuple] = []  # [(msg, req_item), ...]
            for _idx, item in enumerate(req.messages):
                try:
                    peer = await tg_clients.user_client.get_input_entity(int(item["chat_id"]))
                    result = await tg_clients.user_client.get_messages(peer, ids=item["id"])
                    # get_messages with single id returns a single Message, not a list
                    msg = result[0] if isinstance(result, (list, tuple)) else result
                    if msg:
                        msg_items.append((msg, item))
                except Exception as e:
                    logger.debug(f"Thumb fetch msg {item['id']} failed: {e}")

            logger.info(f"Thumbnail gen: fetched {len(msg_items)}/{len(req.messages)} messages")
            if not msg_items:
                _thumb_tasks[task_id]["status"] = "done"
                return

            # generate_thumbnails preserves input order
            msgs_only = [m for m, _ in msg_items]
            results = await generate_thumbnails(tg_clients.user_client, msgs_only, max_thumbs=200)

            thumbs = []
            # results[i] corresponds to msgs_only[i] corresponds to msg_items[i]
            for i, (path, name) in enumerate(results):
                if i >= len(msg_items):
                    break
                _, req_item = msg_items[i]
                thumbs.append({
                    "url": f"/thumbs/{path.name}",
                    "name": name,
                    "chat_id": req_item["chat_id"],
                    "msg_id": req_item["id"],
                })

            _thumb_tasks[task_id]["thumbs"] = thumbs
            _thumb_tasks[task_id]["status"] = "done"
        except Exception as e:
            logger.exception(f"Thumbnail gen failed: {e}")
            _thumb_tasks[task_id]["status"] = "error"
            _thumb_tasks[task_id]["error"] = str(e)

    asyncio.ensure_future(_gen())
    return {"task_id": task_id, "total": len(req.messages)}


@router.get("/search/thumbnails/{task_id}")
async def poll_thumbnails(task_id: str):
    """轮询缩略图生成进度"""
    task = _thumb_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "status": task["status"],
        "thumbs": task["thumbs"],
        "total": task.get("total", 0),
        "generated": len(task["thumbs"]),
    }


@router.post("/download/batch")
async def download_batch(req: DownloadBatchRequest):
    await _ensure_user_client()
    searcher = await get_searcher()
    try:
        peer = searcher.connected_peer
        if req.channel_id:
            peer = await tg_clients.user_client.get_input_entity(int(req.channel_id))
        if not peer:
            raise HTTPException(status_code=400, detail="No channel connected and no channel_id provided")

        messages = await tg_clients.user_client.get_messages(peer, ids=req.message_ids)
        if not messages:
            raise HTTPException(status_code=404, detail="No messages found")

        count = await searcher.batch_add_tasks(messages, "web_request", formats=req.formats)
        return {"status": "success", "count": count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/login/send-code")
async def login_send_code(req: LoginSendCodeRequest):
    try:
        await tg_clients.send_code(req.phone)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/login/sign-in")
async def login_sign_in(req: LoginSignInRequest):
    try:
        await tg_clients.sign_in(req.code)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/me")
async def get_me():
    if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
        return {"logged_in": False}

    try:
        me = await tg_clients.user_client.get_me()
        avatar_url = await _download_avatar(me)
        return {
            "logged_in": True,
            "id": me.id,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "username": me.username,
            "phone": me.phone,
            "avatar_url": avatar_url,
        }
    except Exception:
        return {"logged_in": False}


async def _download_avatar(me) -> str | None:
    avatar_dir = Path("public") / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    avatar_path = avatar_dir / f"user_{me.id}.jpg"
    try:
        if not avatar_path.exists():
            await tg_clients.user_client.download_profile_photo(me, file=str(avatar_path))
        return f"/avatars/user_{me.id}.jpg" if avatar_path.exists() else None
    except Exception as e:
        logger.error(f"Failed to download avatar: {e}")
        return None


def serialize_message(msg) -> dict[str, Any]:
    if not msg:
        return {}

    media_type = "text"
    file_name = None
    file_size = 0
    if msg.media:
        file_obj = getattr(msg, "file", None)
        doc_obj = getattr(msg, "document", None)
        mime_type = (getattr(file_obj, "mime_type", "") or getattr(doc_obj, "mime_type", "") or "").lower()
        file_name_lc = (getattr(file_obj, "name", "") or "").lower()
        doc_attrs = getattr(doc_obj, "attributes", None) or []
        has_video_attr = any(attr.__class__.__name__ == "DocumentAttributeVideo" for attr in doc_attrs)
        is_round_video_attr = any(
            attr.__class__.__name__ == "DocumentAttributeVideo" and bool(getattr(attr, "round_message", False))
            for attr in doc_attrs
        )

        is_round_video = bool(msg.video_note) or is_round_video_attr
        is_animation_like = bool(msg.gif) or mime_type == "image/gif"
        is_photo_like = (
            bool(msg.photo)
            or (mime_type.startswith("image/") and not is_animation_like)
            or file_name_lc.endswith((".jpg", ".jpeg", ".png", ".webp"))
        )
        is_video_like = (
            bool(msg.video)
            or mime_type.startswith("video/")
            or has_video_attr
            or file_name_lc.endswith((".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"))
        )
        is_audio_like = bool(msg.audio) or mime_type.startswith("audio/")
        is_voice_like = bool(msg.voice)

        if is_photo_like:
            media_type = "photo"
        elif is_video_like and not is_round_video and not is_animation_like:
            media_type = "video"
        elif is_voice_like:
            media_type = "voice"
        elif is_audio_like and not is_voice_like:
            media_type = "audio"
        elif is_animation_like:
            media_type = "animation"
        elif msg.document:
            media_type = "document"

        if msg.file:
            file_name = msg.file.name
            file_size = msg.file.size or 0

    return {
        "id": msg.id,
        "date": msg.date.isoformat() if hasattr(msg, "date") and msg.date else datetime.now().isoformat(),
        "text": msg.message or "",
        "file_name": file_name,
        "file_size": file_size,
        "media_type": media_type,
        "chat_id": str(msg.chat_id) if hasattr(msg, "chat_id") else None,
    }


@router.get("/dialogs")
async def get_dialogs():
    searcher = await get_searcher()
    return await searcher.get_dialogs()


@router.post("/messages/forward")
async def forward_messages(req: ForwardRequest):
    await _ensure_user_client()
    try:
        from_peer = int(req.from_channel_id)
        messages = await tg_clients.user_client.get_messages(from_peer, ids=req.message_ids)
        if not messages:
            raise HTTPException(status_code=404, detail="No messages found")

        queued = 0
        for msg in messages:
            if not msg or not msg.media:
                continue

            file_name = message_file_name(msg)
            task = {
                "chat_id": "web_request",
                "message_id": str(msg.id),
                "file_name": file_name,
                "media_type": serialize_message(msg).get("media_type", "unknown"),
                "file_size": msg.file.size if msg.file else 0,
                "channel_id": str(msg.chat_id),
                "channel_username": getattr(msg.chat, "username", "") if msg.chat else "",
                "channel_title": getattr(msg.chat, "title", "") if msg.chat else "",
                "task_data": {
                    "original_file_name": file_name,
                    "forward_target": str(req.to_chat_id),
                    "delete_after_forward": False,
                    "caption": msg.message or "",
                    "access_hash": getattr(msg.chat, "access_hash", None),
                    "requester_chat_id": "web_request",
                },
            }
            await download_manager.add_task(task)
            queued += 1

        return {"status": "queued", "count": queued}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e



# ── 本地文件管理 ──────────────────────────────────────────

@router.get("/storage/files")
async def get_local_files():
    """列出本地下载目录中的文件"""
    downloads_dir = Path(config.save_path).expanduser().resolve()
    if not downloads_dir.exists():
        return {"files": [], "total_size": 0, "total_files": 0}

    files = sorted(
        [f for f in downloads_dir.iterdir() if f.is_file()],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    result = []
    for f in files:
        stat = f.stat()
        result.append({
            "name": f.name,
            "size": stat.st_size,
            "size_formatted": format_size(stat.st_size),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "is_del_marked": f.name.startswith("[DEL]"),
        })

    total_size = sum(f.stat().st_size for f in files)
    return {"files": result, "total_size": total_size, "total_files": len(files)}


class FileDeleteRequest(BaseModel):
    names: list[str]


@router.post("/storage/delete")
async def delete_local_files(req: FileDeleteRequest):
    """删除指定的本地文件"""
    downloads_dir = Path(config.save_path).expanduser().resolve()
    deleted = 0
    freed = 0
    errors = []
    for name in req.names:
        fpath = downloads_dir / name
        try:
            safe = fpath.resolve()
            if not str(safe).startswith(str(downloads_dir.resolve())):
                errors.append(f"{name}: path escape")
                continue
            if safe.exists() and safe.is_file():
                freed += int(safe.stat().st_size)
                safe.unlink()
                deleted += 1
                logger.info(f"Web UI deleted local file: {name}")
            else:
                errors.append(f"{name}: not found")
        except Exception as e:
            errors.append(f"{name}: {e}")
    return {"status": "success", "deleted": deleted, "freed": freed, "freed_formatted": format_size(freed), "errors": errors}


@router.post("/storage/clear")
async def clear_local_files():
    """清空本地下载目录"""
    downloads_dir = Path(config.save_path).expanduser().resolve()
    if not downloads_dir.exists():
        return {"status": "success", "deleted": 0, "freed": 0}

    files = [f for f in downloads_dir.iterdir() if f.is_file()]
    freed = int(sum(f.stat().st_size for f in files))
    for f in files:
        f.unlink()
    logger.info(f"Web UI cleared all local files, freed {format_size(freed)}")
    return {"status": "success", "deleted": len(files), "freed": freed, "freed_formatted": format_size(freed)}


# ── 缩略图缓存管理 ────────────────────────────────────────

@router.get("/storage/thumbs")
async def get_thumb_cache():
    """查看缩略图缓存状态"""
    from telegram.handlers.thumbnail import THUMB_DIR

    if not THUMB_DIR.exists():
        return {"total_files": 0, "total_size": 0, "size_formatted": "0 B"}

    files = [f for f in THUMB_DIR.iterdir() if f.is_file()]
    total = len(files)
    size = int(sum(f.stat().st_size for f in files))
    return {"total_files": total, "total_size": size, "size_formatted": format_size(size)}


@router.post("/storage/thumbs/clear")
async def clear_thumb_cache():
    """清空缩略图缓存"""
    from telegram.handlers.thumbnail import THUMB_DIR

    if not THUMB_DIR.exists():
        return {"status": "success", "deleted": 0, "freed": 0}

    files = [f for f in THUMB_DIR.iterdir() if f.is_file()]
    freed = int(sum(f.stat().st_size for f in files))
    for f in files:
        f.unlink()
    logger.info(f"Web UI cleared thumb cache: {len(files)} files, freed {format_size(freed)}")
    return {"status": "success", "deleted": len(files), "freed": freed, "freed_formatted": format_size(freed)}
