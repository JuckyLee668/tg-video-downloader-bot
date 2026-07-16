import asyncio
import json
import time
from typing import Any, Dict, Optional

from loguru import logger


class StateManager:
    """FSM state persistence — uses the shared DB connection (no extra connections).

    State is cached in-memory and persisted asynchronously to the same
    SQLite database that db_manager uses, sharing its single connection.
    """

    def __init__(self, ttl: int = 3600):
        self.ttl = ttl
        self._cache: Dict[int, Dict[str, Any]] = {}
        self._initialized = False

    async def _ensure_table(self):
        """Create the user_states table once (idempotent)."""
        if self._initialized:
            return
        from core.database import db_manager

        db = db_manager._db
        await db.execute("""\
            CREATE TABLE IF NOT EXISTS user_states (
                chat_id INTEGER PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        await db.commit()
        self._initialized = True

    async def set(self, chat_id: int, state: Dict[str, Any]):
        self._cache[chat_id] = {"data": dict(state), "timestamp": time.time()}
        asyncio.create_task(self._persist(chat_id, state))

    async def _persist(self, chat_id: int, state: Dict[str, Any]):
        try:
            await self._ensure_table()
            from core.database import db_manager

            db = db_manager._db
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
        recovered = await self._load_from_db(chat_id)
        if recovered is not None:
            self._cache[chat_id] = {"data": recovered, "timestamp": time.time()}
            return recovered
        return None

    async def _load_from_db(self, chat_id: int) -> Optional[Dict[str, Any]]:
        try:
            await self._ensure_table()
            from core.database import db_manager

            db = db_manager._db
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
            await self._ensure_table()
            from core.database import db_manager

            db = db_manager._db
            await db.execute("DELETE FROM user_states WHERE chat_id = ?", (chat_id,))
            await db.commit()
        except Exception as e:
            logger.debug(f"State clear failed for {chat_id}: {e}")


state_manager = StateManager()
