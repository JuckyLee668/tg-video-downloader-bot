from fastapi import APIRouter, HTTPException, Depends, Header
import asyncio
import aiosqlite
from core.database import db_manager
from downloader.manager import download_manager
from telegram.client import tg_clients
from telegram import search
from typing import List, Dict, Any
from datetime import datetime
from loguru import logger
from web.api_models import (
    ConnectRequest, JoinRequest, SearchKeywordRequest, 
    SearchTimeRequest, SearchRecentRequest, DownloadBatchRequest,
    LoginSendCodeRequest, LoginSignInRequest, TaskIdRequest,
    ProxyConfigRequest, ConfigResponse, ForwardRequest, BatchDeleteRequest,
    HistoryDeleteRequest
)
from core.config import config, ProxyConfig
import os

WEB_API_FAIL_COUNT = 0
WEB_API_LOCKED = False


def _mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep * 2)}{value[-keep:]}"


async def require_api_key(x_api_key: str | None = Header(default=None)):
    global WEB_API_FAIL_COUNT, WEB_API_LOCKED
    expected = (os.getenv("WEB_API_KEY") or "").strip()
    if not expected:
        return
    if WEB_API_LOCKED:
        raise HTTPException(status_code=423, detail="WEB_API_KEY 已锁定，请重启程序后重试。")

    provided = (x_api_key or "").strip()
    if not provided:
        raise HTTPException(status_code=401, detail="未提供 WEB_API_KEY")

    if provided != expected:
        WEB_API_FAIL_COUNT += 1
        if WEB_API_FAIL_COUNT >= 3:
            WEB_API_LOCKED = True
            raise HTTPException(status_code=401, detail="WEB_API_KEY 连续 3 次错误，已锁定，请重启程序后重试。")
        raise HTTPException(status_code=401, detail=f"WEB_API_KEY 错误（{WEB_API_FAIL_COUNT}/3）")

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
        "media_types": config.media_types
    }

@router.post("/config/proxy")
async def set_proxy(req: ProxyConfigRequest):
    try:
        proxy_cfg = ProxyConfig(**req.model_dump())
        # apply to both global and user client proxy for consistency
        config.proxy = proxy_cfg
        config.user_api.proxy = proxy_cfg
        config.save()
        return {"status": "success", "proxy": proxy_cfg.model_dump()}
    except Exception as e:
        logger.exception(f"设置代理失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/queue/delete")
async def delete_task(req: TaskIdRequest):
    await db_manager.delete_download_task(req.task_id)
    return {"status": "success"}

@router.post("/queue/batch-delete")
async def batch_delete_tasks(req: BatchDeleteRequest):
    for tid in req.task_ids:
        await db_manager.delete_download_task(tid)
    return {"status": "success"}

@router.post("/queue/clear")
async def clear_queue_route():
    async with aiosqlite.connect(db_manager.db_path) as db:
        await db.execute("DELETE FROM download_queue")
        await db.commit()
    # Reset in-memory queue
    download_manager.queue = asyncio.Queue()
    download_manager.active_tasks = set()
    return {"status": "success"}

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
    
    # Reload pending tasks into memory queue
    await download_manager.init()
    return {"status": "success"}

@router.get("/stats")
async def get_stats():
    summary = await db_manager.get_stats_summary()
    return {
        "active_tasks": len(download_manager.active_tasks),
        "queued_tasks": download_manager.queue.qsize(),
        "total_downloads": summary.get('completed', 0),
        "successful_downloads": summary.get('completed', 0),
        "failed_downloads": 0, # We should track this in DB stats
        "total_size": summary.get('total_size', 0)
    }

@router.get("/queue")
async def get_queue():
    res = await db_manager.get_download_list(1, 100)
    return res['items']

@router.get("/history")
async def get_history(page: int = 1, size: int = 50):
    res = await db_manager.get_history_list(page, size)
    return res

@router.get("/channels")
async def get_channels():
    return await db_manager.get_connected_channels()

@router.post("/channels/connect")
async def connect_channel(req: ConnectRequest):
    if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
        raise HTTPException(status_code=401, detail="User client not logged in")
    
    try:
        if not search.searcher:
            from telegram.search import init_searcher
            init_searcher(tg_clients.user_client)
        
        info = await search.searcher.connect_channel(req.identifier)
        return info
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/channels/join")
async def join_channel(req: JoinRequest):
    if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
        raise HTTPException(status_code=401, detail="User client not logged in")
    
    try:
        if not search.searcher:
            from telegram.search import init_searcher
            init_searcher(tg_clients.user_client)
        
        await search.searcher.join_channel(req.link)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

async def get_searcher():
    if not search.searcher:
        if tg_clients.user_client and await tg_clients.user_client.is_user_authorized():
            from telegram.search import init_searcher
            init_searcher(tg_clients.user_client)
        else:
            raise HTTPException(status_code=400, detail="Searcher not initialized. Please login or connect to a channel first.")
    return search.searcher

@router.post("/search/recent")
async def search_recent(req: SearchRecentRequest):
    searcher = await get_searcher()
    try:
        messages = await searcher.get_recent(req.limit, req.media_type)
        return [serialize_message(m) for m in messages]
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/search/keyword")
async def search_keyword(req: SearchKeywordRequest):
    searcher = await get_searcher()
    try:
        messages = await searcher.search_keyword(req.keyword, req.limit, req.media_type)
        return [serialize_message(m) for m in messages]
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/search/time")
async def search_time(req: SearchTimeRequest):
    searcher = await get_searcher()
    try:
        start_date = datetime.strptime(req.start_date, "%Y-%m-%d")
        end_date = datetime.strptime(req.end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        messages = await searcher.search_by_time(start_date, end_date, req.limit, req.media_type)
        return [serialize_message(m) for m in messages]
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/download/batch")
async def download_batch(req: DownloadBatchRequest):
    if not tg_clients.user_client:
        raise HTTPException(status_code=400, detail="User client not available")
    
    try:
        peer = search.searcher.connected_peer if search.searcher else None
        if req.channel_id:
            peer = await tg_clients.user_client.get_input_entity(int(req.channel_id))
        
        if not peer:
            raise HTTPException(status_code=400, detail="No channel connected and no channel_id provided")
            
        messages = await tg_clients.user_client.get_messages(peer, ids=req.message_ids)
        if not messages:
            raise HTTPException(status_code=404, detail="No messages found")
            
        count = await search.searcher.batch_add_tasks(messages, "web_request", formats=req.formats)
        return {"status": "success", "count": count}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/login/send-code")
async def login_send_code(req: LoginSendCodeRequest):
    try:
        await tg_clients.send_code(req.phone)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/login/sign-in")
async def login_sign_in(req: LoginSignInRequest):
    try:
        # Note: Web login does NOT use the "digit - 1" transformation for simplicity
        await tg_clients.sign_in(req.code)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/me")
async def get_me():
    if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
        return {"logged_in": False}
    
    try:
        me = await tg_clients.user_client.get_me()
        
        # 处理头像
        avatar_url = None
        try:
            # 确保目录存在
            avatar_dir = os.path.join("public", "avatars")
            os.makedirs(avatar_dir, exist_ok=True)
            avatar_path = os.path.join(avatar_dir, f"user_{me.id}.jpg")
            
            # 只有当文件不存在时才下载，或者你可以添加逻辑定期更新
            if not os.path.exists(avatar_path):
                await tg_clients.user_client.download_profile_photo(me, file=avatar_path)
            
            if os.path.exists(avatar_path):
                avatar_url = f"/avatars/user_{me.id}.jpg"
        except Exception as e:
            logger.error(f"下载头像失败: {e}")

        return {
            "logged_in": True,
            "id": me.id,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "username": me.username,
            "phone": me.phone,
            "avatar_url": avatar_url
        }
    except Exception:
        return {"logged_in": False}

import os
from loguru import logger

def serialize_message(msg):
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
        has_video_attr = any(a.__class__.__name__ == "DocumentAttributeVideo" for a in doc_attrs)
        is_round_video_attr = any(
            a.__class__.__name__ == "DocumentAttributeVideo" and bool(getattr(a, "round_message", False))
            for a in doc_attrs
        )

        is_round_video = bool(msg.video_note) or is_round_video_attr
        is_animation_like = bool(msg.gif) or mime_type == "image/gif"
        is_photo_like = bool(msg.photo) or (mime_type.startswith("image/") and not is_animation_like) or file_name_lc.endswith((".jpg", ".jpeg", ".png", ".webp"))
        is_video_like = bool(msg.video) or mime_type.startswith("video/") or has_video_attr or file_name_lc.endswith((".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"))
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
        "date": msg.date.isoformat() if hasattr(msg, 'date') and msg.date else datetime.now().isoformat(),
        "text": msg.message or "",
        "file_name": file_name,
        "file_size": file_size,
        "media_type": media_type,
        "chat_id": str(msg.chat_id) if hasattr(msg, 'chat_id') else None
    }

@router.get("/dialogs")
async def get_dialogs():
    searcher = await get_searcher()
    return await searcher.get_dialogs()

@router.post("/messages/forward")
async def forward_messages(req: ForwardRequest):
    try:
        if not tg_clients.user_client or not await tg_clients.user_client.is_user_authorized():
            raise HTTPException(status_code=401, detail="User client not logged in")

        from_peer = int(req.from_channel_id)
        messages = await tg_clients.user_client.get_messages(from_peer, ids=req.message_ids)
        if not messages:
            raise HTTPException(status_code=404, detail="No messages found")

        queued = 0
        for msg in messages:
            if not msg or not msg.media:
                continue

            if msg.file and msg.file.name:
                file_name = msg.file.name
            else:
                mime = getattr(msg.file, "mime_type", "") if msg.file else ""
                ext = ".mp4" if "video" in mime else ".mp3" if "audio" in mime else ".jpg" if "image" in mime else ""
                file_name = f"media_{msg.id}{ext}"

            task = {
                "chat_id": str(msg.chat_id),
                "message_id": str(msg.id),
                "file_name": file_name,
                "media_type": msg.media.__class__.__name__ if msg.media else "unknown",
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
                },
            }
            await download_manager.add_task(task)
            queued += 1

        return {"status": "queued", "count": queued}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
