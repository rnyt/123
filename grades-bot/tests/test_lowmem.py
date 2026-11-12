"""Экономия памяти (bot/lowmem.py): бот должен влезать в бесплатные хостинги со 128 МБ."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from bot import lowmem

ROOT = Path(__file__).resolve().parent.parent
MESSAGE = {
    "message_id": 5,
    "date": 1700000000,
    "chat": {"id": 7, "type": "private", "first_name": "A"},
    "from": {"id": 7, "is_bot": False, "first_name": "A", "username": "x"},
    "text": "/start",
    "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
    "reply_markup": {"inline_keyboard": [[{"text": "a", "callback_data": "b"}]]},
}


def run_python(code: str, **env: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return result.stdout.strip()


def test_enabled_when_bot_imported_first() -> None:
    assert lowmem.is_enabled()


def test_lazy_types_parse_real_json() -> None:
    from aiogram.methods import SendMessage
    from aiogram.methods.base import Response
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update

    update = Update.model_validate(
        {
            "update_id": 1,
            "callback_query": {
                "id": "q",
                "from": MESSAGE["from"],
                "chat_instance": "c",
                "data": "d",
                "message": MESSAGE,
            },
        }
    )
    assert update.callback_query is not None
    assert update.callback_query.message.reply_markup.inline_keyboard[0][0].callback_data == "b"

    response = Response[list[Update]].model_validate({"ok": True, "result": [{"update_id": 2, "message": MESSAGE}]})
    assert response.result[0].message.text == "/start"
    assert Response[Message].model_validate({"ok": True, "result": MESSAGE}).result.chat.id == 7

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="a", callback_data="b")]])
    dumped = SendMessage(chat_id=7, text="hi", reply_markup=kb).model_dump(exclude_none=True)
    assert dumped["reply_markup"] == {"inline_keyboard": [[{"text": "a", "callback_data": "b"}]]}


def test_can_be_disabled() -> None:
    out = run_python("import bot.lowmem as m; print(m.is_enabled())", LOW_MEMORY="0")
    assert out == "False"


@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="нужен Linux (/proc)")
def test_import_fits_free_hosting() -> None:
    """Импорт бота со всеми библиотеками — заметно меньше 128 МБ (без экономии ~165 МБ)."""
    code = (
        "import bot.__main__, bot.handlers\n"
        "st = dict(l.split(':', 1) for l in open('/proc/self/status') if ':' in l)\n"
        "print(int(st['VmHWM'].split()[0]) // 1024)"
    )
    assert int(run_python(code)) < 100
