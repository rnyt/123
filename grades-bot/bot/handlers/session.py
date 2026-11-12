"""Архив семестров: просмотр, «архивировать?» после сессии, переход к следующему семестру."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.utils.callback_answer import CallbackAnswer

from .. import clock, screens
from ..callbacks import Arch
from ..context import Ctx
from ..logic import SUBJECT_FORMS, plural
from ..states import OnbSt
from .common import go_main

router = Router(name="session")


@router.callback_query(Arch.filter(F.a == "list"))
async def archive_list(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode()
    await ctx.show(cq, await screens.archive_screen(ctx.repo, ctx.user))


@router.callback_query(Arch.filter(F.a == "view"))
async def archive_view(cq: CallbackQuery, callback_data: Arch, ctx: Ctx) -> None:
    screen = None
    if callback_data.id != ctx.user.semester_id:
        screen = await screens.archive_view_screen(ctx.repo, ctx.user, callback_data.id)
    await ctx.mode()
    await ctx.show(cq, screen or await screens.archive_screen(ctx.repo, ctx.user))


@router.callback_query(Arch.filter(F.a == "ask"))
async def archive_confirm(cq: CallbackQuery, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    if not await ctx.repo.list_subjects(ctx.user.id, ctx.user.semester_id):
        callback_answer.text = "В семестре нет предметов — архивировать нечего"
        callback_answer.show_alert = True
        return
    await ctx.mode()
    await ctx.show(cq, await screens.archive_confirm_screen(ctx.repo, ctx.user))


@router.callback_query(Arch.filter(F.a == "yes"))
async def archive_yes(cq: CallbackQuery, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    previous_id = ctx.user.semester_id or 0
    previous = await ctx.repo.get_semester(ctx.user.id, previous_id)
    previous_count = len(await ctx.repo.list_subjects(ctx.user.id, previous_id))
    if previous is None or not previous_count:
        callback_answer.text = "Этот семестр уже в архиве"
        await go_main(cq, ctx)
        return
    new = await ctx.repo.start_new_semester(ctx.user)
    await ctx.refresh_user()
    await ctx.mode()
    callback_answer.text = "🗂 Готово!"
    await ctx.show(cq, screens.after_archive_screen(previous.label, new, previous_id, previous_count))


@router.callback_query(Arch.filter(F.a == "later"))
async def archive_later(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.repo.update_semester(ctx.user.id, ctx.user.semester_id, archive_asked_at=clock.now())
    await go_main(cq, ctx, notice="⏳ Хорошо, спрошу через пару дней. Отправить в архив можно и самому: 🗂 Архив.")


@router.callback_query(Arch.filter(F.a == "never"))
async def archive_never(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.repo.update_semester(ctx.user.id, ctx.user.semester_id, archive_ask=False)
    await go_main(cq, ctx, notice="🙅 Больше не спрашиваю. Отправить семестр в архив можно в 🗂 Архив.")


@router.callback_query(Arch.filter(F.a == "copy"))
async def archive_copy(cq: CallbackQuery, callback_data: Arch, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    if await ctx.repo.list_subjects(ctx.user.id, ctx.user.semester_id):
        callback_answer.text = "Предметы уже есть"
        await go_main(cq, ctx)
        return
    if callback_data.id == ctx.user.semester_id:
        await go_main(cq, ctx)
        return
    copied = await ctx.repo.copy_subjects(ctx.user.id, callback_data.id, ctx.user.semester_id or 0)
    await go_main(
        cq, ctx,
        notice=f"📋 Перенёс {plural(copied, SUBJECT_FORMS)} (без оценок). Лишние убери в ⚙️ предмета. Удачи! 🍀",
    )  # fmt: skip


@router.callback_query(Arch.filter(F.a == "fresh"))
async def archive_fresh(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode(OnbSt.subjects)
    await ctx.show(cq, await screens.onboarding_subjects(ctx.repo, ctx.user))
