"""Фейковый Telegram для сквозных тестов: бот работает как настоящий, но без сети.

FakeTelegram подменяет HTTP-сессию aiogram и хранит «чат» в памяти,
а Client изображает пользователя: пишет сообщения и нажимает кнопки.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    DeleteWebhook,
    EditMessageReplyMarkup,
    EditMessageText,
    GetMe,
    SendDocument,
    SendMessage,
    SetMyCommands,
    SetMyDescription,
    SetMyShortDescription,
    TelegramMethod,
)
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update, User

BOT_ID = 42

ALLOWED_TAGS = {
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ins",
    "s",
    "strike",
    "del",
    "a",
    "code",
    "pre",
    "tg-spoiler",
    "blockquote",
}
_TAG = re.compile(r"<(/?)([a-z-]+)(?:\s[^<>]*)?>")
_ENTITY = re.compile(r"&(?:lt|gt|amp|quot|#\d+);")


def check_html(text: str) -> None:
    """Проверка, как у Telegram: разрешённые теги, правильная вложенность, экранированные < и &."""
    stack: list[str] = []
    pos = 0
    while (start := text.find("<", pos)) != -1:
        match = _TAG.match(text, start)
        if not match or match.group(2) not in ALLOWED_TAGS:
            raise TelegramBadRequest(
                method=None,
                message=f"Bad Request: can't parse entities: bad tag at {start}: {text[start : start + 20]!r}",
            )  # type: ignore[arg-type]
        closing, tag = match.group(1), match.group(2)
        if closing:
            if not stack or stack.pop() != tag:
                raise TelegramBadRequest(method=None, message=f"Bad Request: can't parse entities: unexpected </{tag}>")  # type: ignore[arg-type]
        else:
            stack.append(tag)
        pos = match.end()
    if stack:
        raise TelegramBadRequest(method=None, message=f"Bad Request: can't parse entities: unclosed <{stack[-1]}>")  # type: ignore[arg-type]
    for amp in re.finditer("&", text):
        if not _ENTITY.match(text, amp.start()):
            raise TelegramBadRequest(method=None, message="Bad Request: can't parse entities: unescaped &")  # type: ignore[arg-type]
    visible = _ENTITY.sub("x", _TAG.sub("", text))
    if len(visible) > 4096:
        raise TelegramBadRequest(method=None, message="Bad Request: message is too long")  # type: ignore[arg-type]


@dataclass
class ChatMessage:
    id: int
    text: str
    from_bot: bool
    kb: InlineKeyboardMarkup | None = None
    deleted: bool = False
    document: bytes | None = None
    filename: str | None = None

    def buttons(self) -> list[tuple[str, str]]:
        if not self.kb:
            return []
        return [(b.text, b.callback_data or "") for row in self.kb.inline_keyboard for b in row]


class FakeTelegram(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.chats: dict[int, dict[int, ChatMessage]] = {}
        self.answers: list[AnswerCallbackQuery] = []
        self.calls: list[TelegramMethod[Any]] = []
        self.blocked: set[int] = set()
        self._ids = itertools.count(100)

    def new_id(self) -> int:
        return next(self._ids)

    def chat(self, chat_id: int) -> dict[int, ChatMessage]:
        return self.chats.setdefault(chat_id, {})

    def _message(self, chat_id: int, mid: int, text: str | None = None) -> Message:
        return Message(
            message_id=mid,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type="private"),
            text=text,
        )

    async def close(self) -> None:
        return None

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        if False:  # pragma: no cover
            yield b""

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None) -> Any:
        self.calls.append(method)
        if isinstance(method, SendMessage | EditMessageText):
            check_html(method.text)
        if isinstance(method, SendDocument) and method.caption:
            check_html(method.caption)
        if isinstance(method, SendMessage):
            chat_id = int(method.chat_id)
            if chat_id in self.blocked:
                raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
            mid = self.new_id()
            kb = method.reply_markup if isinstance(method.reply_markup, InlineKeyboardMarkup) else None
            self.chat(chat_id)[mid] = ChatMessage(mid, method.text, True, kb)
            return self._message(chat_id, mid, method.text)
        if isinstance(method, EditMessageText):
            chat_id, mid = int(method.chat_id or 0), int(method.message_id or 0)
            msg = self.chat(chat_id).get(mid)
            if msg is None or msg.deleted:
                raise TelegramBadRequest(method=method, message="Bad Request: message to edit not found")
            if not msg.from_bot or msg.document is not None:
                raise TelegramBadRequest(method=method, message="Bad Request: message can't be edited")
            kb = method.reply_markup if isinstance(method.reply_markup, InlineKeyboardMarkup) else None
            if msg.text == method.text and _same_kb(msg.kb, kb):
                raise TelegramBadRequest(
                    method=method,
                    message="Bad Request: message is not modified: specified new message content and "
                    "reply markup are exactly the same as a current content and reply markup of the message",
                )
            msg.text, msg.kb = method.text, kb
            return self._message(chat_id, mid, method.text)
        if isinstance(method, EditMessageReplyMarkup):
            chat_id, mid = int(method.chat_id or 0), int(method.message_id or 0)
            msg = self.chat(chat_id).get(mid)
            if msg is None or msg.deleted:
                raise TelegramBadRequest(method=method, message="Bad Request: message to edit not found")
            msg.kb = method.reply_markup if isinstance(method.reply_markup, InlineKeyboardMarkup) else None
            return self._message(chat_id, mid, msg.text)
        if isinstance(method, DeleteMessage):
            msg = self.chat(int(method.chat_id)).get(method.message_id)
            if msg is None or msg.deleted:
                raise TelegramBadRequest(method=method, message="Bad Request: message to delete not found")
            msg.deleted = True
            return True
        if isinstance(method, SendDocument):
            chat_id = int(method.chat_id)
            mid = self.new_id()
            doc = method.document
            data = getattr(doc, "data", b"")
            self.chat(chat_id)[mid] = ChatMessage(
                mid, method.caption or "", True, None, document=data, filename=getattr(doc, "filename", None)
            )
            return self._message(chat_id, mid)
        if isinstance(method, AnswerCallbackQuery):
            self.answers.append(method)
            return True
        if isinstance(method, GetMe):
            return User(id=BOT_ID, is_bot=True, first_name="Оценки", username="grades_test_bot")
        if isinstance(method, SetMyCommands | SetMyDescription | SetMyShortDescription | DeleteWebhook):
            return True
        raise NotImplementedError(type(method).__name__)


def _same_kb(a: InlineKeyboardMarkup | None, b: InlineKeyboardMarkup | None) -> bool:
    dump = lambda m: m.model_dump(exclude_none=True) if m else None  # noqa: E731
    return dump(a) == dump(b)


def make_bot(session: FakeTelegram) -> Bot:
    return Bot(
        token=f"{BOT_ID}:TEST-TOKEN",
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


class Client:
    """Пользователь Telegram, который пишет боту и жмёт кнопки."""

    _updates = itertools.count(1)

    def __init__(
        self,
        dp: Dispatcher,
        bot: Bot,
        tg: FakeTelegram,
        user_id: int = 777,
        name: str = "Вася Пупкин",
        username: str | None = "ztpst",
    ):
        self.dp, self.bot, self.tg = dp, bot, tg
        self.user = User(
            id=user_id,
            is_bot=False,
            first_name=name.split()[0],
            last_name=" ".join(name.split()[1:]) or None,
            username=username,
        )

    @property
    def id(self) -> int:
        return self.user.id

    # ------------------------------------------------------------ действия

    async def send(self, text: str) -> None:
        mid = self.tg.new_id()
        self.tg.chat(self.id)[mid] = ChatMessage(mid, text, from_bot=False)
        message = Message(
            message_id=mid,
            date=datetime.now(timezone.utc),
            chat=Chat(id=self.id, type="private"),
            from_user=self.user,
            text=text,
        )
        await self.dp.feed_update(self.bot, Update(update_id=next(self._updates), message=message))

    async def send_sticker(self) -> None:
        from aiogram.types import Sticker

        mid = self.tg.new_id()
        self.tg.chat(self.id)[mid] = ChatMessage(mid, "<sticker>", from_bot=False)
        message = Message(
            message_id=mid,
            date=datetime.now(timezone.utc),
            chat=Chat(id=self.id, type="private"),
            from_user=self.user,
            sticker=Sticker(
                file_id="x", file_unique_id="y", type="regular", width=1, height=1, is_animated=False, is_video=False
            ),
        )
        await self.dp.feed_update(self.bot, Update(update_id=next(self._updates), message=message))

    async def press_data(self, data: str, message_id: int | None = None) -> None:
        msg = self.tg.chat(self.id)[message_id] if message_id else self.panel
        query = CallbackQuery(
            id=str(next(self._updates)),
            from_user=self.user,
            chat_instance="test",
            message=Message(
                message_id=msg.id,
                date=datetime.now(timezone.utc),
                chat=Chat(id=self.id, type="private"),
                text=msg.text,
            ),
            data=data,
        )
        await self.dp.feed_update(self.bot, Update(update_id=next(self._updates), callback_query=query))

    async def press(self, label: str, exact: bool = False) -> None:
        """Нажать кнопку текущей панели, в подписи которой есть `label`."""
        panel = self.panel
        found = [data for text, data in panel.buttons() if (text == label if exact else label in text)]
        if not found:
            raise AssertionError(f"Нет кнопки «{label}». Есть: {[t for t, _ in panel.buttons()]}\n{panel.text}")
        await self.press_data(found[0], panel.id)

    # ------------------------------------------------------------ что видно в чате

    @property
    def visible(self) -> list[ChatMessage]:
        return [m for m in self.tg.chat(self.id).values() if not m.deleted]

    @property
    def panel(self) -> ChatMessage:
        bot_messages = [m for m in self.visible if m.from_bot and m.document is None]
        if not bot_messages:
            raise AssertionError("В чате нет сообщений бота")
        return bot_messages[-1]

    @property
    def text(self) -> str:
        return self.panel.text

    @property
    def labels(self) -> list[str]:
        return [t for t, _ in self.panel.buttons()]

    @property
    def last_answer(self) -> AnswerCallbackQuery | None:
        return self.tg.answers[-1] if self.tg.answers else None

    def dump(self) -> str:
        """Как экран выглядит для человека — удобно при отладке."""
        rows = []
        if self.panel.kb:
            for row in self.panel.kb.inline_keyboard:
                rows.append(" | ".join(f"[{b.text}]" for b in row))
        return self.panel.text + "\n" + "\n".join(rows)
