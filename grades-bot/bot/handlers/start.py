"""Команды и знакомство с ботом (шкала → предметы → ОКР)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer

from .. import clock, screens
from ..callbacks import Onb
from ..context import Ctx
from ..logic import SCALES, remind_every_label, split_names
from ..screens import ex
from ..states import OnbSt
from .common import go_main, resume_onboarding, subjects_notice

router = Router(name="start")


# ---------------------------------------------------------------- команды


@router.message(CommandStart())
async def cmd_start(message: Message, ctx: Ctx) -> None:
    if ctx.user.onboarded:
        await go_main(message, ctx, fresh=True)
    else:
        await resume_onboarding(message, ctx, fresh=True)


@router.message(Command("menu", "cancel"))
async def cmd_menu(message: Message, ctx: Ctx) -> None:
    await go_main(message, ctx, fresh=True)


@router.message(Command("help"))
async def cmd_help(message: Message, ctx: Ctx) -> None:
    if not ctx.user.onboarded:
        await resume_onboarding(message, ctx, fresh=True)
        return
    await ctx.mode()
    await ctx.fresh(message, screens.help_screen(ctx.user))


@router.message(Command("stats"))
async def cmd_stats(message: Message, ctx: Ctx) -> None:
    if not ctx.user.onboarded:
        await resume_onboarding(message, ctx, fresh=True)
        return
    await ctx.mode()
    await ctx.fresh(message, await screens.stats_screen(ctx.repo, ctx.user))


@router.message(Command("settings"))
async def cmd_settings(message: Message, ctx: Ctx) -> None:
    if not ctx.user.onboarded:
        await resume_onboarding(message, ctx, fresh=True)
        return
    await ctx.mode()
    await ctx.fresh(message, await screens.settings_screen(ctx.repo, ctx.user))


@router.message(F.text.startswith("/"))
async def cmd_unknown(message: Message, ctx: Ctx) -> None:
    await go_main(message, ctx, notice="🤷 Не знаю такой команды. Вот меню 👇", fresh=True)


# ---------------------------------------------------------------- шаг 1: шкала


async def _choose_scale(event: Message | CallbackQuery, ctx: Ctx, scale: int) -> None:
    ctx.user = await ctx.repo.update_user(ctx.user.id, scale=scale, pass_mark=SCALES[scale])
    ctx.user = await ctx.repo.ensure_semester(ctx.user)
    await ctx.mode(OnbSt.subjects)
    await ctx.show(event, await screens.onboarding_subjects(ctx.repo, ctx.user))


@router.callback_query(Onb.filter(F.a == "scale"))
async def onb_scale(cq: CallbackQuery, callback_data: Onb, ctx: Ctx) -> None:
    if ctx.user.onboarded:
        await go_main(cq, ctx)
        return
    await _choose_scale(cq, ctx, 5 if callback_data.v == 5 else 10)


@router.message(OnbSt.scale, F.text)
async def onb_scale_text(message: Message, ctx: Ctx) -> None:
    text = (message.text or "").strip()
    if text in {"5", "10"}:
        await _choose_scale(message, ctx, int(text))
        return
    await ctx.show(message, screens.onboarding_scale(ctx.user, notice="👇 Выбери шкалу кнопкой ниже."))


@router.callback_query(Onb.filter(F.a == "back"))
async def onb_back(cq: CallbackQuery, ctx: Ctx) -> None:
    if ctx.user.onboarded:
        await go_main(cq, ctx)
        return
    await ctx.mode(OnbSt.scale)
    await ctx.show(cq, screens.onboarding_scale(ctx.user))


# ---------------------------------------------------------------- шаг 2: предметы


@router.message(OnbSt.subjects, F.text)
async def onb_subjects_text(message: Message, ctx: Ctx) -> None:
    added, skipped = await ctx.repo.add_subjects(ctx.user, split_names(message.text or ""))
    await ctx.show(message, await screens.onboarding_subjects(ctx.repo, ctx.user, subjects_notice(added, skipped)))


@router.callback_query(Onb.filter(F.a == "subjects"))
async def onb_to_subjects(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode(OnbSt.subjects)
    await ctx.show(cq, await screens.onboarding_subjects(ctx.repo, ctx.user))


@router.callback_query(Onb.filter(F.a == "reset"))
async def onb_reset(cq: CallbackQuery, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    views = await ctx.repo.views(ctx.user)
    if any(v.grades or v.works for v in views):
        callback_answer.text = "В предметах уже есть оценки — удаляй их по одному в ⚙️ предмета."
        callback_answer.show_alert = True
        return
    await ctx.repo.delete_semester_subjects(ctx.user)
    await ctx.mode(OnbSt.subjects)
    await ctx.show(cq, await screens.onboarding_subjects(ctx.repo, ctx.user, "🗑 Список очищен — отправь заново."))


@router.callback_query(Onb.filter(F.a == "next"))
async def onb_next(cq: CallbackQuery, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    if not await ctx.repo.list_subjects(ctx.user.id, ctx.user.semester_id):
        callback_answer.text = "Сначала отправь хотя бы один предмет 🙂"
        callback_answer.show_alert = True
        return
    await ctx.mode(OnbSt.okr)
    await ctx.show(cq, await screens.onboarding_okr(ctx.repo, ctx.user))


# ---------------------------------------------------------------- шаг 3: где есть ОКР


@router.callback_query(Onb.filter(F.a == "okr"))
async def onb_toggle_okr(cq: CallbackQuery, callback_data: Onb, ctx: Ctx) -> None:
    subject = await ctx.repo.get_subject(ctx.user, callback_data.v)
    if subject is not None:
        await ctx.repo.set_okr_count(ctx.user, subject.id, 0 if subject.okr_count else 1)
    await ctx.mode(OnbSt.okr)
    await ctx.show(cq, await screens.onboarding_okr(ctx.repo, ctx.user))


@router.message(OnbSt.okr, F.text)
async def onb_okr_text(message: Message, ctx: Ctx) -> None:
    """Вспомнил ещё предмет на шаге с ОКР — просто добавим его."""
    added, skipped = await ctx.repo.add_subjects(ctx.user, split_names(message.text or ""))
    await ctx.show(message, await screens.onboarding_okr(ctx.repo, ctx.user, subjects_notice(added, skipped)))


@router.callback_query(Onb.filter(F.a == "done"))
async def onb_done(cq: CallbackQuery, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    if not await ctx.repo.list_subjects(ctx.user.id, ctx.user.semester_id):
        callback_answer.text = "Сначала добавь хотя бы один предмет 🙂"
        callback_answer.show_alert = True
        return
    first_time = not ctx.user.onboarded
    ctx.user = await ctx.repo.update_user(ctx.user.id, onboarded=True, last_data_at=clock.now())
    if first_time:
        reminders = f"{remind_every_label(ctx.user.remind_every)} в {ctx.user.remind_time}"
        notice = "\n".join(
            [
                "🎉 <b>Готово!</b> Это твоё главное меню.",
                "• Нажми на предмет — там оценки, ОКР, лабораторные и практические.",
                f"• Или пиши прямо в чат: <code>физ {ex(ctx.user).a} {ex(ctx.user).b}</code>.",
                f"🔔 Напомню внести оценки ({reminders}), если сам не будешь — настроить можно в ⚙️.",
                "🧹 Твои сообщения я убираю, чтобы в чате оставалось одно аккуратное меню.",
            ]
        )
    else:
        notice = "✅ Предметы нового семестра готовы. Удачи! 🍀"
    callback_answer.text = "Готово!"
    await go_main(cq, ctx, notice=notice)
