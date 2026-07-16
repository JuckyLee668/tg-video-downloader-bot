import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import aiosqlite
from loguru import logger


class StateManager:
    """
    管理用户的交互状态 (FSM)，持久化到 SQLite 防止重启丢失。
    """

    def __init__(self, ttl: int = 3600, db_path: str = "data/telegram_downloader.db"):
        self.ttl = ttl
        self.db_path = db_path
        self._cache: Dict[int, Dict[str, Any]] = {}
        self._initialized = False

    @asynccontextmanager
    async def _get_db(self):
        db = await aiosqlite.connect(self.db_path)
        await db.execute("PRAGMA busy_timeout = 5000")
        if not self._initialized:
            await db.execute("""\
                CREATE TABLE IF NOT EXISTS user_states (
                    chat_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            await db.commit()
            self._initialized = True
        try:
            yield db
        finally:
            await db.close()

    async def set(self, chat_id: int, state: Dict[str, Any]):
        self._cache[chat_id] = {"data": dict(state), "timestamp": time.time()}
        asyncio.create_task(self._persist(chat_id, state))

    async def _persist(self, chat_id: int, state: Dict[str, Any]):
        try:
            async with self._get_db() as db:
                await db.execute(
                    "INSERT OR REPLACE INTO user_states (chat_id, state_json, updated_at) VALUES (?, ?, ?)",
                    (chat_id, json.dumps(state, ensure_ascii=False), time.time()),
                )
                await db.commit()
        except Exception as e:
            logger.debug(f"State persist failed for {chat_id}: {e}")

    async def get(self, chat_id: int) -> Optional[Dict[str, Any]]:
        item = self._cache.get(chat_id)
        if item:
            if time.time() - item["timestamp"] > self.ttl:
                await self.clear(chat_id)
                return None
            return item["data"]
        # 缓存未命中，从 DB 恢复
        recovered = await self._load_from_db(chat_id)
        if recovered is not None:
            self._cache[chat_id] = {"data": recovered, "timestamp": time.time()}
            return recovered
        return None

    async def _load_from_db(self, chat_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self._get_db() as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT state_json, updated_at FROM user_states WHERE chat_id = ?",
                    (chat_id,),
                )
                row = await cursor.fetchone()
                if row:
                    updated_at = row["updated_at"]
                    if time.time() - updated_at > self.ttl:
                        await db.execute("DELETE FROM user_states WHERE chat_id = ?", (chat_id,))
                        await db.commit()
                        return None
                    return json.loads(row["state_json"])
        except Exception as e:
            logger.debug(f"State load failed for {chat_id}: {e}")
        return None

    async def update(self, chat_id: int, **kwargs):
        state = await self.get(chat_id)
        if state is None:
            state = {}
        state.update(kwargs)
        await self.set(chat_id, state)

    async def clear(self, chat_id: int):
        self._cache.pop(chat_id, None)
        asyncio.create_task(self._clear_db(chat_id))

    async def _clear_db(self, chat_id: int):
        try:
            async with self._get_db() as db:
                await db.execute("DELETE FROM user_states WHERE chat_id = ?", (chat_id,))
                await db.commit()
        except Exception as e:
            logger.debug(f"State clear failed for {chat_id}: {e}")


state_manager = StateManager()
