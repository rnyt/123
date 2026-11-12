"""Команды и знакомство с ботом: курс и семестр → предметы → ОКР/экзамены/курсовые → даты."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer

from .. import clock, screens
from ..callbacks import Onb
from ..context import Ctx
from ..dates import fmt_range, parse_range
from ..logic import remind_every_label, split_names
from ..models import CONTROLS, NONE
from ..screens import ex, today_for
from ..states import OnbSt
from .common import Event, go_main, resume_onboarding, subjects_notice

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


@router.message(Command("help", "commands"))
async def cmd_help(message: Message, ctx: Ctx) -> None:
    await ctx.mode()
    await ctx.fresh(message, screens.help_screen(ctx.user, ctx.is_owner))


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
    await ctx.fresh(message, await screens.settings_screen(ctx.repo, ctx.user, is_owner=ctx.is_owner))


@router.message(Command("archive"))
async def cmd_archive(message: Message, ctx: Ctx) -> None:
    if not ctx.user.onboarded:
        await resume_onboarding(message, ctx, fresh=True)
        return
    await ctx.mode()
    await ctx.fresh(message, await screens.archive_screen(ctx.repo, ctx.user))


@router.message(F.text.startswith("/"))
async def cmd_unknown(message: Message, ctx: Ctx) -> None:
    await go_main(message, ctx, notice="🤷 Не знаю такой команды. Все команды — /help. Вот меню 👇", fresh=True)


# ---------------------------------------------------------------- шаг 1: курс и семестр


async def _course_screen(event: Event, ctx: Ctx, notice: str | None = None) -> None:
    semester = await ctx.repo.get_semester(ctx.user.id, ctx.user.semester_id)
    await ctx.mode(OnbSt.course)
    await ctx.show(event, screens.onboarding_course(ctx.user, semester, notice))


async def _choose_course(event: Event, ctx: Ctx, course: int) -> None:
    ctx.user = await ctx.repo.ensure_semester(ctx.user)
    await ctx.repo.update_semester(ctx.user.id, ctx.user.semester_id, course=course)
    await ctx.mode(OnbSt.subjects)
    await ctx.show(event, await screens.onboarding_subjects(ctx.repo, ctx.user))


@router.callback_query(Onb.filter(F.a == "term"))
async def onb_term(cq: CallbackQuery, callback_data: Onb, ctx: Ctx) -> None:
    ctx.user = await ctx.repo.ensure_semester(ctx.user)
    await ctx.repo.update_semester(ctx.user.id, ctx.user.semester_id, term=2 if callback_data.v == 2 else 1)
    await _course_screen(cq, ctx)


@router.callback_query(Onb.filter(F.a == "course"))
async def onb_course(cq: CallbackQuery, callback_data: Onb, ctx: Ctx) -> None:
    if ctx.user.onboarded:
        await go_main(cq, ctx)
        return
    await _choose_course(cq, ctx, min(max(callback_data.v, 1), 4))


@router.message(OnbSt.course, F.text)
async def onb_course_text(message: Message, ctx: Ctx) -> None:
    text = (message.text or "").strip().split()[0] if (message.text or "").strip() else ""
    if text in {"1", "2", "3", "4"}:
        await _choose_course(message, ctx, int(text))
        return
    await _course_screen(message, ctx, notice="👇 Выбери курс кнопкой ниже.")


@router.callback_query(Onb.filter(F.a == "back"))
async def onb_back(cq: CallbackQuery, ctx: Ctx) -> None:
    if ctx.user.onboarded:
        await go_main(cq, ctx)
        return
    await _course_screen(cq, ctx)


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


# ---------------------------------------------------------------- шаг 3: ОКР, экзамены, курсовые


async def _setup(event: Event, ctx: Ctx, tab: int, notice: str | None = None, finish: str | None = None) -> None:
    """finish: «dates» — дальше даты сессии (знакомство, новый семестр), «main» — сразу в меню."""
    if finish is None:
        finish = (await ctx.data()).get("finish") or "dates"
    tab = min(max(tab, 0), 2)
    await ctx.mode(OnbSt.setup, tab=tab, finish=finish)
    await ctx.show(event, await screens.onboarding_setup(ctx.repo, ctx.user, tab, notice, finish))


@router.callback_query(Onb.filter(F.a == "setup"))
async def onb_setup(cq: CallbackQuery, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    if not await ctx.repo.list_subjects(ctx.user.id, ctx.user.semester_id):
        callback_answer.text = "Сначала отправь хотя бы один предмет 🙂"
        callback_answer.show_alert = True
        return
    await _setup(cq, ctx, 0, finish="dates")


@router.callback_query(Onb.filter(F.a == "tab"))
async def onb_tab(cq: CallbackQuery, callback_data: Onb, ctx: Ctx) -> None:
    await _setup(cq, ctx, callback_data.v)


@router.callback_query(Onb.filter(F.a.in_({"tokr", "tctl", "tprj"})))
async def onb_toggle(cq: CallbackQuery, callback_data: Onb, ctx: Ctx) -> None:
    subject = await ctx.repo.get_subject(ctx.user, callback_data.v)
    tab = {"tokr": 0, "tctl": 1, "tprj": 2}[callback_data.a]
    if subject is not None:
        if tab == 0:
            await ctx.repo.set_okr_count(ctx.user, subject.id, 0 if subject.okr_count else 1)
        elif tab == 1:
            current = subject.control if subject.control in CONTROLS else NONE
            following = CONTROLS[(CONTROLS.index(current) + 1) % len(CONTROLS)]
            await ctx.repo.update_subject(ctx.user, subject.id, control=following)
        else:
            await ctx.repo.update_subject(ctx.user, subject.id, project=not subject.project)
    await _setup(cq, ctx, tab)


@router.callback_query(Onb.filter(F.a == "bulk"))
async def onb_bulk(cq: CallbackQuery, callback_data: Onb, ctx: Ctx) -> None:
    """Сразу для всех предметов: ОКР везде / нигде, все без экзамена, курсовых нет."""
    code = callback_data.v
    for subject in await ctx.repo.list_subjects(ctx.user.id, ctx.user.semester_id):
        if code == 1 and not subject.okr_count:
            await ctx.repo.set_okr_count(ctx.user, subject.id, 1)
        elif code == 2 and subject.okr_count:
            await ctx.repo.set_okr_count(ctx.user, subject.id, 0)
        elif code == 3:
            await ctx.repo.update_subject(ctx.user, subject.id, control=NONE)
        elif code == 4:
            await ctx.repo.update_subject(ctx.user, subject.id, project=False)
    await _setup(cq, ctx, {1: 0, 2: 0, 3: 1, 4: 2}.get(code, 0))


@router.message(OnbSt.setup, F.text)
async def onb_setup_text(message: Message, ctx: Ctx) -> None:
    """Вспомнил ещё предмет на шаге настройки — просто добавим его."""
    data = await ctx.data()
    added, skipped = await ctx.repo.add_subjects(ctx.user, split_names(message.text or ""))
    await _setup(message, ctx, int(data.get("tab") or 0), subjects_notice(added, skipped))


# ---------------------------------------------------------------- шаг 4: даты сессии и каникул


@router.callback_query(Onb.filter(F.a == "dates"))
async def onb_dates(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode(OnbSt.session)
    await ctx.show(cq, screens.onboarding_dates("session", step=not ctx.user.onboarded))


async def _save_dates(message: Message, ctx: Ctx, which: str) -> tuple[bool, str | None]:
    parsed = parse_range(message.text or "", today_for(ctx.user))
    if parsed is None:
        return False, "⚠️ Не понял даты. Пример: <code>22.12–14.01</code> или <code>22 декабря – 14 января</code>"
    start, end = parsed
    fields = {f"{which}_start": start.isoformat(), f"{which}_end": end.isoformat()}
    await ctx.repo.update_semester(ctx.user.id, ctx.user.semester_id, **fields)
    label = "Сессия" if which == "session" else "Каникулы"
    return True, f"✅ {label}: {fmt_range(start.isoformat(), end.isoformat())}"


@router.message(OnbSt.session, F.text)
async def onb_session_text(message: Message, ctx: Ctx) -> None:
    ok, notice = await _save_dates(message, ctx, "session")
    if not ok:
        await ctx.show(message, screens.onboarding_dates("session", notice, step=not ctx.user.onboarded))
        return
    await ctx.mode(OnbSt.holidays)
    await ctx.show(message, screens.onboarding_dates("holidays", notice, step=not ctx.user.onboarded))


@router.message(OnbSt.holidays, F.text)
async def onb_holidays_text(message: Message, ctx: Ctx) -> None:
    ok, notice = await _save_dates(message, ctx, "holidays")
    if not ok:
        await ctx.show(message, screens.onboarding_dates("holidays", notice, step=not ctx.user.onboarded))
        return
    await _finish(message, ctx, notice)


@router.callback_query(Onb.filter(F.a == "skip"))
async def onb_skip(cq: CallbackQuery, ctx: Ctx) -> None:
    if await ctx.state.get_state() == OnbSt.session.state:
        await ctx.mode(OnbSt.holidays)
        await ctx.show(cq, screens.onboarding_dates("holidays", step=not ctx.user.onboarded))
        return
    await _finish(cq, ctx)


@router.callback_query(Onb.filter(F.a == "done"))
async def onb_done(cq: CallbackQuery, ctx: Ctx) -> None:
    after_adding = (await ctx.data()).get("finish") == "main" and ctx.user.onboarded
    await _finish(cq, ctx, done="✅ Готово — предметы настроены." if after_adding else None)


async def _finish(event: Event, ctx: Ctx, extra: str | None = None, done: str | None = None) -> None:
    if not await ctx.repo.list_subjects(ctx.user.id, ctx.user.semester_id):
        await ctx.mode(OnbSt.subjects)
        await ctx.show(
            event, await screens.onboarding_subjects(ctx.repo, ctx.user, "Сначала добавь хотя бы один предмет 🙂")
        )
        return
    first_time = not ctx.user.onboarded
    ctx.user = await ctx.repo.update_user(ctx.user.id, onboarded=True, last_data_at=clock.now())
    lines = [extra] if extra else []
    if first_time:
        e = ex(ctx.user)
        reminders = f"{remind_every_label(ctx.user.remind_every)} в {ctx.user.remind_time}"
        lines += [
            "🎉 <b>Готово!</b> Это твоё главное меню.",
            "• Нажми на предмет — там оценки, ОКР, работы, экзамен и курсовой.",
            f"• Или пиши прямо в чат: <code>физ {e.a} {e.b}</code> или слитно <code>физра {e.glued}</code>.",
            f"🔔 Напомню внести оценки ({reminders}) и защитить работы — настроить можно в ⚙️.",
            "🧹 Твои сообщения я убираю, чтобы в чате оставалось одно аккуратное меню.",
        ]
    else:
        lines.append(done or "✅ Новый семестр готов. Удачи! 🍀")
    await go_main(event, ctx, notice="\n".join(lines))
