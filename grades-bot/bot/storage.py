"""Хранилище состояний диалога (FSM) в той же SQLite — переживает перезапуск бота."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import aiosqlite
from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey


class SQLiteStorage(BaseStorage):
    def __init__(self, db: aiosqlite.Connection, lock: asyncio.Lock) -> None:
        self.db = db
        self.lock = lock

    @staticmethod
    def _key(key: StorageKey) -> str:
        return ":".join(
            str(part)
            for part in (
                key.bot_id,
                key.chat_id,
                key.user_id,
                key.thread_id or 0,
                key.business_connection_id or "",
                key.destiny,
            )
        )

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        async with self.lock:
            await self.db.execute(
                "INSERT INTO fsm (key, state) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET state = excluded.state",
                (self._key(key), value),
            )
            await self.db.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        async with self.db.execute("SELECT state FROM fsm WHERE key = ?", (self._key(key),)) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(data), ensure_ascii=False)
        async with self.lock:
            await self.db.execute(
                "INSERT INTO fsm (key, data) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET data = excluded.data",
                (self._key(key), payload),
            )
            await self.db.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self.db.execute("SELECT data FROM fsm WHERE key = ?", (self._key(key),)) as cur:
            row = await cur.fetchone()
        if not row or not row[0]:
            return {}
        try:
            data = json.loads(row[0])
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    async def close(self) -> None:  # соединение закрывает main
        return None
