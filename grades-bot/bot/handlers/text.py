"""Всё, что не поймали другие разделы: быстрый ввод текстом и запасные варианты."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer

from ..context import Ctx
from ..screens import ex
from .common import go_main, go_subject, handle_free_text

router = Router(name="text")


@router.message(StateFilter(None), F.text)
async def free_text(message: Message, ctx: Ctx) -> None:
    await handle_free_text(message, ctx)


@router.message(F.text)
async def text_in_unknown_state(message: Message, ctx: Ctx) -> None:
    """Состояние устарело (например, после обновления бота) — работаем как с быстрым вводом."""
    data = await ctx.data()
    await ctx.mode(None, sid=data.get("sid") or 0)
    await handle_free_text(message, ctx)


@router.message()
async def not_text(message: Message, ctx: Ctx) -> None:
    e = ex(ctx.user)
    if message.voice or message.video_note:
        notice = f"🎙 Голосовые пока не понимаю — напиши текстом, например <code>физ {e.a} {e.b}</code>."
    else:
        notice = f"🙂 Я понимаю текст и кнопки. Например: <code>физ {e.a} {e.b}</code>."
    data = await ctx.data()
    if ctx.user.onboarded and data.get("sid"):
        await go_subject(message, ctx, int(data["sid"]), notice)
    else:
        await go_main(message, ctx, notice=notice)


@router.callback_query()
async def stale_button(cq: CallbackQuery, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    callback_answer.text = "Эта кнопка устарела — открываю меню"
    await go_main(cq, ctx)
