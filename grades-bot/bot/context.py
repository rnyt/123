"""Контекст обработчика: пользователь, база, панель и состояние диалога в одном объекте."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import CallbackQuery, Message

from .models import User
from .panel import Panel
from .repo import Repo
from .screens import Screen


@dataclass
class Ctx:
    user: User
    repo: Repo
    panel: Panel
    state: FSMContext
    bot: Bot

    async def mode(self, state: State | None = None, **data: Any) -> None:
        """Переключить режим ввода и заменить данные диалога."""
        await self.state.set_state(state)
        await self.state.set_data(data)

    async def data(self) -> dict[str, Any]:
        return await self.state.get_data()

    async def show(self, event: Message | CallbackQuery, screen: Screen) -> None:
        await self.panel.show(event, self.user, screen)

    async def fresh(self, event: Message | CallbackQuery | None, screen: Screen) -> None:
        await self.panel.fresh(event, self.bot, self.user, screen)

    async def refresh_user(self) -> User:
        user = await self.repo.get_user(self.user.id)
        if user is not None:
            self.user = user
        return self.user
