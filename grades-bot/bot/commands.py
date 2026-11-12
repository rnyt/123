"""Меню команд Telegram и описание бота."""

from __future__ import annotations

from contextlib import suppress

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeChat

COMMANDS = [
    BotCommand(command="menu", description="🏠 Главное меню"),
    BotCommand(command="stats", description="📊 Статистика"),
    BotCommand(command="archive", description="🗂 Архив семестров"),
    BotCommand(command="settings", description="⚙️ Настройки"),
    BotCommand(command="help", description="❓ Команды и как пользоваться"),
]
OWNER_COMMANDS = [*COMMANDS, BotCommand(command="apanel", description="🛡 Админ-панель")]

DESCRIPTION = (
    "Помогаю учиться в колледже без сюрпризов: считаю средний балл и отметку за семестр по правилам ССО, "
    "слежу за ОКР, лабораторными, практическими и курсовыми, показываю допуск к экзаменам и итог. "
    "Напоминаю внести оценки и защитить работы.\n\nНажми «Старт» 👇"
)
SHORT_DESCRIPTION = "Средний балл, ОКР, работы, курсовые, экзамены и допуск — в одном чате."


async def setup_bot_profile(bot: Bot, owner_id: int | None = None) -> None:
    """Команды в меню Telegram и описание бота (не критично, если не получится)."""
    with suppress(TelegramAPIError):
        await bot.set_my_commands(COMMANDS)
    with suppress(TelegramAPIError):
        await bot.set_my_description(DESCRIPTION)
    with suppress(TelegramAPIError):
        await bot.set_my_short_description(SHORT_DESCRIPTION)
    if owner_id:
        await setup_owner_commands(bot, owner_id)


async def setup_owner_commands(bot: Bot, owner_id: int) -> None:
    """Владельцу в меню команд дополнительно видна /apanel."""
    with suppress(TelegramAPIError):
        await bot.set_my_commands(OWNER_COMMANDS, scope=BotCommandScopeChat(chat_id=owner_id))
