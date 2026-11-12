"""Мидлварь: доступ по приглашению, загрузка пользователя и сборка контекста."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import TelegramObject, Update
from aiogram.types import User as TgUser

from . import screens
from .access import Access
from .callbacks import ACCESS_REQUEST
from .commands import setup_owner_commands
from .context import Ctx
from .panel import Panel
from .repo import Repo

log = logging.getLogger(__name__)


class ContextMiddleware(BaseMiddleware):
    def __init__(self, repo: Repo, panel: Panel, access: Access) -> None:
        self.repo = repo
        self.panel = panel
        self.access = access

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
        bot: Bot = data["bot"]
        username = tg_user.username.lower() if tg_user.username else None

        is_owner, claimed = await self.access.check_owner(tg_user.id, username)
        if claimed:
            log.info("Владелец бота закреплён: id %s (@%s)", tg_user.id, username)
            await setup_owner_commands(bot, tg_user.id)
        if not await self.access.allowed(tg_user.id, username, is_owner):
            await self._deny(event, bot, tg_user)
            return None

        user = await self.repo.ensure_user(tg_user.id, tg_user.full_name, tg_user.username)
        if user.blocked:
            user = await self.repo.update_user(user.id, blocked=False)
        data["ctx"] = Ctx(
            user=user,
            repo=self.repo,
            panel=self.panel,
            state=data["state"],
            bot=bot,
            access=self.access,
            is_owner=is_owner,
        )
        return await handler(event, data)

    async def _deny(self, event: TelegramObject, bot: Bot, tg_user: TgUser) -> None:
        """Чужим — вежливый отказ и кнопка «Попросить доступ», владельцу — заявка с кнопками."""
        if not isinstance(event, Update):
            return
        try:
            query = event.callback_query
            if query is not None and query.data == ACCESS_REQUEST:
                await query.answer(await self._request(bot, tg_user), show_alert=True)
            elif query is not None:
                await query.answer("🔒 Это личный бот", show_alert=True)
            elif event.message is not None:
                screen = screens.deny_screen()
                await event.message.answer(screen.text, reply_markup=screen.kb)
        except TelegramAPIError:
            log.debug("Не удалось ответить пользователю без доступа", exc_info=True)

    async def _request(self, bot: Bot, tg_user: TgUser) -> str:
        owner = await self.access.owner_id()
        if owner is None:
            return "Владелец ещё не запускал бота — попроси его написать боту /start."
        created = await self.repo.add_request(tg_user.id, tg_user.username, tg_user.full_name)
        if not created:
            return "📨 Заявка уже у владельца — дождись ответа."
        card = screens.request_card(tg_user.id, tg_user.full_name, tg_user.username)
        try:
            await bot.send_message(owner, card.text, reply_markup=card.kb)
        except TelegramAPIError:
            log.warning("Не удалось отправить заявку владельцу", exc_info=True)
        return "📨 Заявка отправлена владельцу. Как только он разрешит — я напишу."
