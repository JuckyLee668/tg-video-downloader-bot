import aiosqlite
import os
import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from core.config import config
from loguru import logger

class DatabaseManager:
    def __init__(self, db_path: str = "data/telegram_downloader.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            # WAL mode
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA synchronous = NORMAL")
            
            # download_queue
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
            
            # forwarded_queue
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
            
            # download_history
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
            
            # download_stats
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
            
            # connected_channels
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
            
            # Migration: Add progress column if not exists
            cursor = await db.execute("PRAGMA table_info(download_queue)")
            columns = [column[1] for column in await cursor.fetchall()]
            if 'progress' not in columns:
                await db.execute("ALTER TABLE download_queue ADD COLUMN progress INTEGER DEFAULT 0")
                logger.info("已通过 ALTER TABLE 为 download_queue 添加 progress 列")
            
            await db.commit()
            
            # Reset stuck tasks
            await db.execute("UPDATE download_queue SET status = 'pending' WHERE status = 'downloading'")
            await db.execute("UPDATE forwarded_queue SET status = 'pending' WHERE status = 'downloading'")
            await db.commit()
            
        logger.info("SQLite 数据库初始化完成")

    async def add_download_task(self, task: Dict[str, Any]):
        task_id = f"{task.get('chat_id', 'unknown')}_{task.get('message_id', 'unknown')}"
        task_data = json.dumps(task.get('task_data', {}))
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO download_queue
                (task_id, chat_id, message_id, file_name, media_type, file_id, file_size,
                 channel_id, channel_username, channel_title, status, priority, task_data, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                task_id,
                str(task.get('chat_id', '')),
                str(task.get('message_id', '')),
                task.get('file_name', '未知文件'),
                task.get('media_type', 'unknown'),
                task.get('file_id', ''),
                task.get('file_size', 0),
                str(task.get('channel_id', '')),
                task.get('channel_username', ''),
                task.get('channel_title', ''),
                task.get('status', 'pending'),
                task.get('priority', 0),
                task_data
            ))
            await db.commit()
        return task_id

    async def get_pending_tasks(self, limit: int = 100):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM download_queue 
                WHERE status = 'pending' OR (status = 'failed' AND retry_count < max_retries)
                ORDER BY priority DESC, created_at ASC LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_task_by_id(self, task_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM download_queue WHERE task_id = ?", (task_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_task_status(self, task_id: str, status: str, error_message: str = None):
        async with aiosqlite.connect(self.db_path) as db:
            if status == 'downloading':
                await db.execute("UPDATE download_queue SET status = ?, started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE task_id = ? AND status != 'downloading'", (status, task_id))
            elif status == 'completed':
                await db.execute("UPDATE download_queue SET status = ?, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?", (status, task_id))
            elif status == 'failed':
                await db.execute("UPDATE download_queue SET status = ?, error_message = ?, retry_count = retry_count + 1, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?", (status, error_message, task_id))
            else:
                await db.execute("UPDATE download_queue SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?", (status, task_id))
            await db.commit()

    async def update_task_progress(self, task_id: str, progress: int):
        async with aiosqlite.connect(self.db_path) as db:
            # Only update if progress increased to avoid jumping back
            await db.execute("UPDATE download_queue SET progress = ?, updated_at = CURRENT_TIMESTAMP WHERE task_id = ? AND progress < ?", (progress, task_id, progress))
            await db.commit()


    async def delete_download_task(self, task_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM download_queue WHERE task_id = ?", (task_id,))
            await db.commit()

    async def complete_download_task(self, task: Dict[str, Any], completion_record: Dict[str, Any]):
        """原子地完成下载任务：记录历史、更新统计、从队列删除"""
        task_id = task.get('task_id')
        
        # 处理 task_data，确保它是 JSON 字符串
        task_data_raw = task.get('task_data', '{}')
        if isinstance(task_data_raw, (dict, list)):
            task_data = json.dumps(task_data_raw)
        else:
            task_data = str(task_data_raw) if task_data_raw else "{}"
        
        async with aiosqlite.connect(self.db_path) as db:
            # 开启事务
            await db.execute("BEGIN TRANSACTION")
            try:
                # 1. 插入到下载历史
                await db.execute("""
                    INSERT OR REPLACE INTO download_history
                    (file_id, file_name, media_type, file_size, chat_id, message_id,
                     channel_id, channel_username, channel_title, download_path, download_url, status, task_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    task.get('file_id'),
                    task.get('file_name'),
                    task.get('media_type'),
                    task.get('file_size', 0),
                    str(task.get('chat_id', '')),
                    str(task.get('message_id', '')),
                    str(task.get('channel_id', '')),
                    task.get('channel_username', ''),
                    task.get('channel_title', ''),
                    completion_record.get('download_path', ''),
                    completion_record.get('download_url', ''),
                    completion_record.get('status', 'completed'),
                    task_data
                ))
                
                # 2. 更新统计
                today = datetime.now().date().isoformat()
                await db.execute("""
                    INSERT INTO download_stats (date, total_downloads, successful_downloads, total_size)
                    VALUES (?, 1, 1, ?)
                    ON CONFLICT(date) DO UPDATE SET
                    total_downloads = total_downloads + 1,
                    successful_downloads = successful_downloads + 1,
                    total_size = total_size + ?,
                    updated_at = CURRENT_TIMESTAMP
                """, (today, task.get('file_size', 0), task.get('file_size', 0)))
                
                # 3. 从队列中删除
                await db.execute("DELETE FROM download_queue WHERE task_id = ?", (task_id,))
                
                await db.commit()
                logger.info(f"任务已成功原子化归档: {task.get('file_name')}")
            except Exception as e:
                await db.rollback()
                logger.error(f"归档任务失败，已回滚: {e}")
                raise e

    async def add_download_history(self, record: Dict[str, Any]):
        task_data_raw = record.get('task_data', {})
        if isinstance(task_data_raw, (dict, list)):
            task_data = json.dumps(task_data_raw)
        else:
            task_data = str(task_data_raw) if task_data_raw else "{}"
            
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO download_history
                (file_id, file_name, media_type, file_size, chat_id, message_id,
                 channel_id, channel_username, channel_title, download_path, download_url, status, task_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.get('file_id'),
                record.get('file_name'),
                record.get('media_type'),
                record.get('file_size', 0),
                str(record.get('chat_id', '')),
                str(record.get('message_id', '')),
                str(record.get('channel_id', '')),
                record.get('channel_username', ''),
                record.get('channel_title', ''),
                record.get('download_path', ''),
                record.get('download_url', ''),
                record.get('status', 'completed'),
                task_data
            ))
            # Update stats
            today = datetime.now().date().isoformat()
            await db.execute("""
                INSERT INTO download_stats (date, total_downloads, successful_downloads, total_size)
                VALUES (?, 1, 1, ?)
                ON CONFLICT(date) DO UPDATE SET
                total_downloads = total_downloads + 1,
                successful_downloads = successful_downloads + 1,
                total_size = total_size + ?,
                updated_at = CURRENT_TIMESTAMP
            """, (today, record.get('file_size', 0), record.get('file_size', 0)))
            await db.commit()

    async def get_download_list(self, page: int = 1, page_size: int = 10, status: str = None):
        offset = (page - 1) * page_size
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM download_queue"
            params = []
            if status:
                query += " WHERE status = ?"
                params.append(status)
            
            # Sort: downloading first, then by creation time
            query += " ORDER BY CASE WHEN status = 'downloading' THEN 0 ELSE 1 END, created_at DESC LIMIT ? OFFSET ?"
            params.extend([page_size, offset])
            
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            
            count_query = "SELECT COUNT(*) FROM download_queue"
            if status:
                count_cursor = await db.execute(count_query, [status])
            else:
                count_cursor = await db.execute(count_query)
            
            total_count = (await count_cursor.fetchone())[0]
            
            return {
                'items': [dict(row) for row in rows],
                'total': total_count,
                'page': page,
                'page_size': page_size
            }

    async def get_history_list(self, page: int = 1, page_size: int = 50):
        offset = (page - 1) * page_size
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM download_history 
                ORDER BY downloaded_at DESC LIMIT ? OFFSET ?
            """, (page_size, offset))
            rows = await cursor.fetchall()
            
            count_cursor = await db.execute("SELECT COUNT(*) FROM download_history")
            total_count = (await count_cursor.fetchone())[0]
            
            return {
                'items': [dict(row) for row in rows],
                'total': total_count,
                'page': page,
                'page_size': page_size
            }

    async def search_history(self, keyword: str, limit: int = 20):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM download_history 
                WHERE file_name LIKE ? OR channel_title LIKE ? OR channel_username LIKE ?
                ORDER BY downloaded_at DESC LIMIT ?
            """, (f'%{keyword}%', f'%{keyword}%', f'%{keyword}%', limit))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def connect_channel(self, channel_id: str, username: str = None, title: str = None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO connected_channels (channel_id, username, title, last_connected_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (str(channel_id), username, title))
            await db.commit()

    async def get_connected_channels(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM connected_channels ORDER BY last_connected_at DESC")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def delete_connected_channel(self, channel_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM connected_channels WHERE channel_id = ?", (str(channel_id),))
            await db.commit()

    async def get_stats_summary(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            # Overall stats
            cursor = await db.execute("""
                SELECT 
                    (SELECT COUNT(*) FROM download_queue WHERE status = 'pending') as pending,
                    (SELECT COUNT(*) FROM download_queue WHERE status = 'downloading') as downloading,
                    (SELECT COUNT(*) FROM download_history) as completed,
                    (SELECT SUM(file_size) FROM download_history) as total_size
            """)
            summary = dict(await cursor.fetchone())
            return summary


# Singleton instance
db_manager = DatabaseManager()
