import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List

import aiosqlite
from loguru import logger


class DatabaseManager:
    def __init__(self, db_path: str = "data/telegram_downloader.db"):
        self.db_path = db_path
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    @asynccontextmanager
    async def _get_db(self):
        """Create a database connection with proper timeout settings."""
        db = await aiosqlite.connect(self.db_path, timeout=30)
        await db.execute("PRAGMA busy_timeout = 30000")
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA synchronous = NORMAL")
        try:
            yield db
        finally:
            await db.close()

    async def init(self):
        async with self._get_db() as db:

            await db.execute("""
                CREATE TABLE IF NOT EXISTS download_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT UNIQUE NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_id TEXT,
                    file_name TEXT NOT NULL,
                    media_type TEXT,
                    file_id TEXT,
                    file_size INTEGER,
                    progress INTEGER DEFAULT 0,
                    channel_id TEXT,
                    channel_username TEXT,
                    channel_title TEXT,
                    status TEXT DEFAULT 'pending',
                    priority INTEGER DEFAULT 0,
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 3,
                    error_message TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    started_at DATETIME,
                    completed_at DATETIME,
                    task_data TEXT
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS forwarded_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    queue_key TEXT UNIQUE NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    media_type TEXT,
                    file_id TEXT,
                    forward_info TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    started_at DATETIME,
                    completed_at DATETIME
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS download_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT,
                    file_name TEXT NOT NULL,
                    media_type TEXT,
                    file_size INTEGER,
                    chat_id TEXT,
                    message_id TEXT,
                    channel_id TEXT,
                    channel_username TEXT,
                    channel_title TEXT,
                    download_path TEXT,
                    download_url TEXT,
                    status TEXT DEFAULT 'completed',
                    error_message TEXT,
                    downloaded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    task_data TEXT,
                    UNIQUE(chat_id, message_id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS download_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    total_downloads INTEGER DEFAULT 0,
                    successful_downloads INTEGER DEFAULT 0,
                    failed_downloads INTEGER DEFAULT 0,
                    total_size INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS connected_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT UNIQUE NOT NULL,
                    username TEXT,
                    title TEXT,
                    last_connected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'active'
                )
            """)

            cursor = await db.execute("PRAGMA table_info(download_queue)")
            columns = [column[1] for column in await cursor.fetchall()]
            if "progress" not in columns:
                await db.execute("ALTER TABLE download_queue ADD COLUMN progress INTEGER DEFAULT 0")
                logger.info("Added progress column to download_queue")

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_download_queue_status_priority_created
                ON download_queue(status, priority DESC, created_at ASC)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_download_queue_chat_status
                ON download_queue(chat_id, status)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_download_history_downloaded_at
                ON download_history(downloaded_at DESC)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_download_history_channel
                ON download_history(channel_id, downloaded_at DESC)
            """)

            await db.execute("UPDATE download_queue SET status = 'pending' WHERE status = 'downloading'")
            await db.execute("UPDATE forwarded_queue SET status = 'pending' WHERE status = 'downloading'")
            await db.commit()

        logger.info("SQLite database initialized")

    async def add_download_task(self, task: Dict[str, Any]):
        task_id = f"{task.get('chat_id', 'unknown')}_{task.get('message_id', 'unknown')}"
        task_data = json.dumps(task.get("task_data", {}), ensure_ascii=False)

        async with self._get_db() as db:
            await db.execute("""
                INSERT INTO download_queue
                (task_id, chat_id, message_id, file_name, media_type, file_id, file_size,
                 channel_id, channel_username, channel_title, status, priority, task_data, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(task_id) DO UPDATE SET
                    file_name = excluded.file_name,
                    media_type = excluded.media_type,
                    file_id = excluded.file_id,
                    file_size = excluded.file_size,
                    channel_id = excluded.channel_id,
                    channel_username = excluded.channel_username,
                    channel_title = excluded.channel_title,
                    status = CASE
                        WHEN download_queue.status = 'downloading' THEN download_queue.status
                        ELSE excluded.status
                    END,
                    priority = excluded.priority,
                    task_data = excluded.task_data,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                task_id,
                str(task.get("chat_id", "")),
                str(task.get("message_id", "")),
                task.get("file_name", "unknown_file"),
                task.get("media_type", "unknown"),
                task.get("file_id", ""),
                task.get("file_size", 0),
                str(task.get("channel_id", "")),
                task.get("channel_username", ""),
                task.get("channel_title", ""),
                task.get("status", "pending"),
                task.get("priority", 0),
                task_data,
            ))
            await db.commit()
        return task_id

    async def get_pending_tasks(self, limit: int = 100):
        async with self._get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM download_queue
                WHERE status = 'pending'
                   OR (status = 'failed' AND retry_count < max_retries)
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def claim_next_task(self):
        async with self._get_db() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute("""
                    SELECT task_id FROM download_queue
                    WHERE status = 'pending'
                       OR (status = 'failed' AND retry_count < max_retries)
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                """)
                row = await cursor.fetchone()
                if not row:
                    await db.commit()
                    return None

                task_id = row["task_id"]
                result = await db.execute("""
                    UPDATE download_queue
                    SET status = 'downloading',
                        started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                      AND (status = 'pending' OR (status = 'failed' AND retry_count < max_retries))
                """, (task_id,))
                if result.rowcount != 1:
                    await db.rollback()
                    return None

                cursor = await db.execute("SELECT * FROM download_queue WHERE task_id = ?", (task_id,))
                claimed = await cursor.fetchone()
                await db.commit()
                return dict(claimed) if claimed else None
            except Exception:
                await db.rollback()
                raise

    async def get_task_by_id(self, task_id: str):
        async with self._get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM download_queue WHERE task_id = ?", (task_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_task_status(self, task_id: str, status: str, error_message: str = None):
        async with self._get_db() as db:
            if status == "downloading":
                await db.execute("""
                    UPDATE download_queue
                    SET status = ?, started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND status != 'downloading'
                """, (status, task_id))
            elif status == "completed":
                await db.execute("""
                    UPDATE download_queue
                    SET status = ?, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                """, (status, task_id))
            elif status == "failed":
                await db.execute("""
                    UPDATE download_queue
                    SET status = ?, error_message = ?, retry_count = retry_count + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                """, (status, error_message, task_id))
            else:
                await db.execute("""
                    UPDATE download_queue
                    SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ?
                """, (status, error_message, task_id))
            await db.commit()

    async def requeue_task(self, task_id: str, error_message: str = None):
        await self.update_task_status(task_id, "pending", error_message)

    async def update_task_progress(self, task_id: str, progress: int):
        progress = max(0, min(100, int(progress)))
        async with self._get_db() as db:
            await db.execute("""
                UPDATE download_queue
                SET progress = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ? AND progress < ?
            """, (progress, task_id, progress))
            await db.commit()

    async def delete_download_task(self, task_id: str):
        async with self._get_db() as db:
            await db.execute("DELETE FROM download_queue WHERE task_id = ?", (task_id,))
            await db.commit()

    async def complete_download_task(self, task: Dict[str, Any], completion_record: Dict[str, Any]):
        task_id = task.get("task_id")
        task_data = self._json_dump(task.get("task_data", "{}"))

        async with self._get_db() as db:
            await db.execute("BEGIN TRANSACTION")
            try:
                await self._upsert_history(db, task, completion_record, task_data)
                await self._increment_stats(db, task.get("file_size", 0), success=True)
                await db.execute("DELETE FROM download_queue WHERE task_id = ?", (task_id,))
                await db.commit()
                logger.info(f"Download task archived atomically: {task.get('file_name')}")
            except Exception as e:
                await db.rollback()
                logger.error(f"Failed to archive task; transaction rolled back: {e}")
                raise

    async def add_download_history(self, record: Dict[str, Any]):
        task_data = self._json_dump(record.get("task_data", {}))

        async with self._get_db() as db:
            await self._upsert_history(db, record, record, task_data)
            await self._increment_stats(db, record.get("file_size", 0), success=True)
            await db.commit()

    async def _upsert_history(self, db, task: Dict[str, Any], completion_record: Dict[str, Any], task_data: str):
        await db.execute("""
            INSERT INTO download_history
            (file_id, file_name, media_type, file_size, chat_id, message_id,
             channel_id, channel_username, channel_title, download_path, download_url, status, task_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                file_id = excluded.file_id,
                file_name = excluded.file_name,
                media_type = excluded.media_type,
                file_size = excluded.file_size,
                channel_id = excluded.channel_id,
                channel_username = excluded.channel_username,
                channel_title = excluded.channel_title,
                download_path = excluded.download_path,
                download_url = excluded.download_url,
                status = excluded.status,
                task_data = excluded.task_data,
                downloaded_at = CURRENT_TIMESTAMP
        """, (
            task.get("file_id"),
            task.get("file_name"),
            task.get("media_type"),
            task.get("file_size", 0),
            str(task.get("chat_id", "")),
            str(task.get("message_id", "")),
            str(task.get("channel_id", "")),
            task.get("channel_username", ""),
            task.get("channel_title", ""),
            completion_record.get("download_path", ""),
            completion_record.get("download_url", ""),
            completion_record.get("status", "completed"),
            task_data,
        ))

    async def _increment_stats(self, db, file_size: int, success: bool):
        today = datetime.now().date().isoformat()
        if success:
            await db.execute("""
                INSERT INTO download_stats (date, total_downloads, successful_downloads, total_size)
                VALUES (?, 1, 1, ?)
                ON CONFLICT(date) DO UPDATE SET
                    total_downloads = total_downloads + 1,
                    successful_downloads = successful_downloads + 1,
                    total_size = total_size + ?,
                    updated_at = CURRENT_TIMESTAMP
            """, (today, file_size or 0, file_size or 0))
        else:
            await db.execute("""
                INSERT INTO download_stats (date, total_downloads, failed_downloads)
                VALUES (?, 1, 1)
                ON CONFLICT(date) DO UPDATE SET
                    total_downloads = total_downloads + 1,
                    failed_downloads = failed_downloads + 1,
                    updated_at = CURRENT_TIMESTAMP
            """, (today,))

    def _json_dump(self, value: Any) -> str:
        if isinstance(value, str):
            return value or "{}"
        return json.dumps(value or {}, ensure_ascii=False)

    async def get_download_list(self, page: int = 1, page_size: int = 10, status: str = None):
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        offset = (page - 1) * page_size
        async with self._get_db() as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM download_queue"
            params = []
            if status:
                query += " WHERE status = ?"
                params.append(status)

            query += """
                ORDER BY CASE WHEN status = 'downloading' THEN 0 ELSE 1 END,
                         priority DESC, created_at DESC
                LIMIT ? OFFSET ?
            """
            params.extend([page_size, offset])

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()

            count_query = "SELECT COUNT(*) FROM download_queue"
            if status:
                count_query += " WHERE status = ?"
                count_cursor = await db.execute(count_query, [status])
            else:
                count_cursor = await db.execute(count_query)
            total_count = (await count_cursor.fetchone())[0]

            return {"items": [dict(row) for row in rows], "total": total_count, "page": page, "page_size": page_size}

    async def get_history_list(self, page: int = 1, page_size: int = 50):
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        offset = (page - 1) * page_size
        async with self._get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM download_history
                ORDER BY downloaded_at DESC LIMIT ? OFFSET ?
            """, (page_size, offset))
            rows = await cursor.fetchall()

            count_cursor = await db.execute("SELECT COUNT(*) FROM download_history")
            total_count = (await count_cursor.fetchone())[0]
            return {"items": [dict(row) for row in rows], "total": total_count, "page": page, "page_size": page_size}

    async def search_history(self, keyword: str, limit: int = 20):
        limit = max(1, min(100, int(limit)))
        async with self._get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM download_history
                WHERE file_name LIKE ? OR channel_title LIKE ? OR channel_username LIKE ?
                ORDER BY downloaded_at DESC LIMIT ?
            """, (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def delete_history_items(self, ids: List[int]):
        clean_ids = [int(item) for item in ids if int(item) > 0]
        if not clean_ids:
            return 0
        async with self._get_db() as db:
            qmarks = ",".join("?" for _ in clean_ids)
            cursor = await db.execute(f"DELETE FROM download_history WHERE id IN ({qmarks})", clean_ids)
            await db.commit()
            return cursor.rowcount

    async def clear_history(self):
        async with self._get_db() as db:
            await db.execute("DELETE FROM download_history")
            await db.commit()

    async def connect_channel(self, channel_id: str, username: str = None, title: str = None):
        async with self._get_db() as db:
            await db.execute("""
                INSERT INTO connected_channels (channel_id, username, title, last_connected_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(channel_id) DO UPDATE SET
                    username = excluded.username,
                    title = excluded.title,
                    status = 'active',
                    last_connected_at = CURRENT_TIMESTAMP
            """, (str(channel_id), username, title))
            await db.commit()

    async def get_connected_channels(self):
        async with self._get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM connected_channels ORDER BY last_connected_at DESC")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def delete_connected_channel(self, channel_id: str):
        async with self._get_db() as db:
            await db.execute("DELETE FROM connected_channels WHERE channel_id = ?", (str(channel_id),))
            await db.commit()

    async def cancel_tasks(self, chat_id: str):
        async with self._get_db() as db:
            await db.execute("""
                UPDATE download_queue
                SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP,
                    retry_count = max_retries
                WHERE (chat_id = ? OR json_extract(task_data, '$.requester_chat_id') = ?)
                  AND status IN ('pending', 'failed', 'downloading')
            """, (str(chat_id), str(chat_id)))
            await db.commit()

    async def cancel_all_tasks(self):
        async with self._get_db() as db:
            await db.execute("""
                UPDATE download_queue
                SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                WHERE status IN ('pending', 'failed')
            """)
            await db.commit()

    async def clear_pending_tasks(self):
        async with self._get_db() as db:
            cursor = await db.execute("DELETE FROM download_queue WHERE status != 'downloading'")
            await db.commit()
            return cursor.rowcount

    async def get_stats_summary(self):
        async with self._get_db() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT
                    (SELECT COUNT(*) FROM download_queue WHERE status = 'pending') AS pending,
                    (SELECT COUNT(*) FROM download_queue WHERE status = 'downloading') AS downloading,
                    (SELECT COUNT(*) FROM download_queue WHERE status = 'failed') AS failed,
                    (SELECT COUNT(*) FROM download_history) AS completed,
                    COALESCE((SELECT SUM(file_size) FROM download_history), 0) AS total_size
            """)
            return dict(await cursor.fetchone())


db_manager = DatabaseManager()
