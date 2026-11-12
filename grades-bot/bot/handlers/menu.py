"""Главное меню, статистика, помощь, добавление предметов, отложенное напоминание."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from .. import clock, screens
from ..callbacks import Nav, Pick
from ..context import Ctx
from ..logic import SNOOZE_SECONDS, fmt_time, split_names
from ..states import Inp, OnbSt
from .common import NOT_FOUND, apply_grades, apply_okr, go_main, handle_free_text, looks_like_grades, subjects_notice

router = Router(name="menu")


@router.callback_query(Nav.filter(F.to == "main"))
async def nav_main(cq: CallbackQuery, ctx: Ctx) -> None:
    await go_main(cq, ctx)


@router.callback_query(Nav.filter(F.to == "noop"))
async def nav_noop(cq: CallbackQuery) -> None:
    """Кнопка-подпись (номер страницы, порог) — ничего не делает."""


@router.callback_query(Nav.filter(F.to == "stats"))
async def nav_stats(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode()
    await ctx.show(cq, await screens.stats_screen(ctx.repo, ctx.user))


@router.callback_query(Nav.filter(F.to == "help"))
async def nav_help(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode()
    await ctx.show(cq, screens.help_screen(ctx.user, ctx.is_owner))


ADD_SUBJECTS_TITLE = "➕ <b>Новые предметы</b>"
ADD_SUBJECTS_BODY = (
    "Отправь названия — можно сразу несколько, через запятую или с новой строки.\n"
    "Например: <code>Химия, Биология</code>"
)


@router.callback_query(Nav.filter(F.to == "add_subj"))
async def nav_add_subjects(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode(Inp.subjects)
    await ctx.show(cq, screens.prompt_screen(ADD_SUBJECTS_TITLE, ADD_SUBJECTS_BODY, Nav(to="main")))


@router.message(Inp.subjects, F.text)
async def add_subjects_text(message: Message, ctx: Ctx) -> None:
    text = message.text or ""
    if await looks_like_grades(ctx, text):
        # «физ 8 9» вместо названия нового предмета — это явно оценки, а не предмет.
        await ctx.mode()
        await handle_free_text(message, ctx)
        return
    added, skipped = await ctx.repo.add_subjects(ctx.user, split_names(text))
    notice = subjects_notice(added, skipped)
    if not added:
        await ctx.show(message, screens.prompt_screen(ADD_SUBJECTS_TITLE, ADD_SUBJECTS_BODY, Nav(to="main"), notice))
        return
    notice += "\nОтметь, где есть ОКР, экзамены и курсовые 👇 (у предметов без них ничего не трогай)."
    await ctx.mode(OnbSt.setup, tab=0, finish="main")
    await ctx.show(message, await screens.onboarding_setup(ctx.repo, ctx.user, 0, notice, finish="main"))


@router.callback_query(Nav.filter(F.to == "snooze"))
async def snooze(cq: CallbackQuery, ctx: Ctx) -> None:
    until = clock.now() + SNOOZE_SECONDS
    ctx.user = await ctx.repo.update_user(ctx.user.id, snooze_until=until)
    await go_main(cq, ctx, notice=f"⏰ Хорошо, напомню в {fmt_time(until, ctx.user.tz)}.")


@router.callback_query(Pick.filter())
async def pick_subject(cq: CallbackQuery, callback_data: Pick, ctx: Ctx) -> None:
    """Оценки отправили текстом без названия предмета — теперь выбрали, куда их записать."""
    subject = await ctx.repo.get_subject(ctx.user, callback_data.s)
    if subject is None:
        await go_main(cq, ctx, notice=NOT_FOUND)
        return
    values = [int(x) for x in callback_data.v.split("-") if x.isdigit()]
    values = [v for v in values if ctx.user.grade_min <= v <= ctx.user.grade_max]
    if not values:
        await go_main(cq, ctx, notice="⚠️ Не нашёл оценок — отправь их ещё раз.")
        return
    if callback_data.o:
        await apply_okr(cq, ctx, subject, values[0])
    else:
        await apply_grades(cq, ctx, subject, values)
