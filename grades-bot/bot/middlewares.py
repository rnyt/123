"""Мидлвари: личный режим, загрузка пользователя и сборка контекста."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from aiogram.types import User as TgUser

from .context import Ctx
from .panel import Panel
from .repo import Repo

log = logging.getLogger(__name__)


class ContextMiddleware(BaseMiddleware):
    def __init__(self, repo: Repo, panel: Panel, allowed_users: frozenset[int] = frozenset()) -> None:
        self.repo = repo
        self.panel = panel
        self.allowed = allowed_users

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        chat = data.get("event_chat")
        if tg_user is None or tg_user.is_bot:
            return None
        if chat is not None and chat.type != "private":
            return None  # бот работает только в личке
        if self.allowed and tg_user.id not in self.allowed:
            await self._deny(event)
            return None

        user = await self.repo.ensure_user(tg_user.id, tg_user.full_name)
        if user.blocked:
            user = await self.repo.update_user(user.id, blocked=False)
        bot: Bot = data["bot"]
        data["ctx"] = Ctx(user=user, repo=self.repo, panel=self.panel, state=data["state"], bot=bot)
        return await handler(event, data)

    @staticmethod
    async def _deny(event: TelegramObject) -> None:
        text = "🔒 Это личный бот. Попроси владельца добавить твой id в ALLOWED_USERS."
        try:
            if isinstance(event, Update) and event.message is not None:
                await event.message.answer(text)
            elif isinstance(event, Update) and event.callback_query is not None:
                await event.callback_query.answer(text, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
        except Exception:  # noqa: BLE001 — отказ доступа не должен ронять бота
            log.debug("Не удалось ответить на запрос без доступа", exc_info=True)
