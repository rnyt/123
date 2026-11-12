"""«Живая панель»: бот держит одно сообщение-меню и обновляет его, а не спамит новыми.

* Нажатие кнопки — редактируется то сообщение, где была кнопка.
* Текстовый ввод — сообщение пользователя убирается (в режиме «чистый чат»),
  а панель обновляется на месте.
* Команды и напоминания — панель отправляется заново внизу чата, старая убирается.
"""

from __future__ import annotations

import logging
from contextlib import suppress

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from .models import User
from .repo import Repo
from .screens import Screen

log = logging.getLogger(__name__)


class Panel:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo

    async def show(self, event: Message | CallbackQuery, user: User, screen: Screen) -> None:
        """Показать экран «на месте»."""
        if isinstance(event, CallbackQuery):
            await self._from_callback(event, user, screen)
        else:
            await self._from_message(event, user, screen)

    async def fresh(self, event: Message | CallbackQuery | None, bot: Bot, user: User, screen: Screen) -> None:
        """Отправить панель заново внизу чата (после команды, файла, напоминания)."""
        if isinstance(event, Message) and user.clean_chat:
            await self._delete(bot, event.chat.id, event.message_id)
        await self.send(bot, user, screen)

    async def send(self, bot: Bot, user: User, screen: Screen) -> None:
        old = user.panel_msg_id
        sent = await bot.send_message(user.id, screen.text, reply_markup=screen.kb)
        await self._remember(user, sent.message_id)
        if old and old != sent.message_id:
            await self._retire(bot, user, old)

    # ------------------------------------------------------------ internals

    async def _from_callback(self, cq: CallbackQuery, user: User, screen: Screen) -> None:
        bot = cq.bot
        assert bot is not None
        msg = cq.message
        if isinstance(msg, Message) and await self._edit(bot, msg.chat.id, msg.message_id, screen):
            if user.panel_msg_id != msg.message_id:
                old = user.panel_msg_id
                await self._remember(user, msg.message_id)
                if old:
                    await self._retire(bot, user, old)
            return
        await self.send(bot, user, screen)

    async def _from_message(self, message: Message, user: User, screen: Screen) -> None:
        bot = message.bot
        assert bot is not None
        if user.clean_chat:
            await self._delete(bot, message.chat.id, message.message_id)
            if user.panel_msg_id and await self._edit(bot, user.id, user.panel_msg_id, screen):
                return
        await self.send(bot, user, screen)

    async def _edit(self, bot: Bot, chat_id: int, message_id: int, screen: Screen) -> bool:
        try:
            await bot.edit_message_text(
                text=screen.text, chat_id=chat_id, message_id=message_id, reply_markup=screen.kb
            )
            return True
        except TelegramBadRequest as exc:
            if "message is not modified" in exc.message:
                return True
            log.debug("Не удалось отредактировать панель %s: %s", message_id, exc.message)
            return False

    async def _remember(self, user: User, message_id: int) -> None:
        user.panel_msg_id = message_id
        await self.repo.set_panel(user.id, message_id)

    async def _retire(self, bot: Bot, user: User, message_id: int) -> None:
        """Старая панель: в чистом чате удаляем, иначе просто снимаем кнопки."""
        if user.clean_chat and await self._delete(bot, user.id, message_id):
            return
        with suppress(TelegramAPIError):
            await bot.edit_message_reply_markup(chat_id=user.id, message_id=message_id, reply_markup=None)

    @staticmethod
    async def _delete(bot: Bot, chat_id: int, message_id: int) -> bool:
        try:
            return bool(await bot.delete_message(chat_id=chat_id, message_id=message_id))
        except TelegramAPIError:
            return False
