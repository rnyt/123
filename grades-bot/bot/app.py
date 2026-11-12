"""Сборка диспетчера — общая для запуска и для тестов."""

from __future__ import annotations

import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import BotCommand, ErrorEvent
from aiogram.utils.callback_answer import CallbackAnswerMiddleware

from .callbacks import Nav
from .handlers import build_router
from .middlewares import ContextMiddleware
from .panel import Panel
from .repo import Repo
from .screens import Screen, btn, markup

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="menu", description="🏠 Главное меню"),
    BotCommand(command="stats", description="📊 Статистика"),
    BotCommand(command="settings", description="⚙️ Настройки"),
    BotCommand(command="help", description="❓ Как пользоваться"),
]

DESCRIPTION = (
    "Слежу за средним баллом, обязательными контрольными (ОКР) и защитой лабораторных "
    "и практических. Сразу покажу, где ты не аттестован, и напомню внести новые оценки.\n\n"
    "Нажми «Старт» 👇"
)
SHORT_DESCRIPTION = "Средний балл, ОКР, лабораторные и аттестация — в одном чате."
ERROR_TEXT = "⚠️ Ой, что-то пошло не так. Попробуй ещё раз — или открой меню."


async def on_error(
    event: ErrorEvent,
    bot: Bot,
    repo: Repo | None = None,
    panel: Panel | None = None,
    state: FSMContext | None = None,
) -> bool:
    """Любая неожиданная ошибка: пишем в лог, а пользователю — понятное сообщение-меню."""
    log.error("Ошибка при обработке апдейта: %r", event.exception, exc_info=event.exception)
    update = event.update
    chat_id = None
    if update.message is not None:
        chat_id = update.message.chat.id
    elif update.callback_query is not None:
        chat_id = update.callback_query.from_user.id
    if chat_id is None:
        return True
    if state is not None:
        with suppress(Exception):
            await state.clear()
    screen = Screen(ERROR_TEXT, markup([[btn("🏠 Меню", Nav(to="main"))]]))
    try:
        user = await repo.get_user(chat_id) if repo else None
        if user is not None and panel is not None:
            await panel.send(bot, user, screen)  # сообщение об ошибке само становится меню
            return True
    except Exception:  # noqa: BLE001
        log.debug("Не удалось показать ошибку панелью", exc_info=True)
    with suppress(Exception):
        await bot.send_message(chat_id, ERROR_TEXT, reply_markup=screen.kb)
    return True


def build_dispatcher(repo: Repo, storage: BaseStorage, allowed_users: frozenset[int] = frozenset()) -> Dispatcher:
    panel = Panel(repo)
    # Апдейты одного пользователя — строго по очереди: быстрые нажатия не мешают друг другу.
    dp = Dispatcher(storage=storage, events_isolation=SimpleEventIsolation())
    dp["repo"] = repo
    dp["panel"] = panel
    dp.update.outer_middleware(ContextMiddleware(repo, panel, allowed_users))
    dp.callback_query.middleware(CallbackAnswerMiddleware())
    dp.include_router(build_router())
    dp.errors.register(on_error)
    return dp


async def setup_bot_profile(bot: Bot) -> None:
    """Команды в меню Telegram и описание бота (не критично, если не получится)."""
    with suppress(TelegramAPIError):
        await bot.set_my_commands(COMMANDS)
    with suppress(TelegramAPIError):
        await bot.set_my_description(DESCRIPTION)
    with suppress(TelegramAPIError):
        await bot.set_my_short_description(SHORT_DESCRIPTION)
