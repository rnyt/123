"""Настройки: семестр и сессия, напоминания, как считать, пояс, шкала, чистый чат, экспорт, удаление."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer

from .. import clock, screens
from ..callbacks import CalcSet, Nav, Rem, ScaleSet, SemSet, TzSet
from ..context import Ctx
from ..dates import fmt_range, parse_range
from ..export import build_xlsx
from ..logic import SCALES, fmt_time, parse_hhmm, parse_tz, remind_every_label
from ..models import CALC_MEAN, CALC_SSO, FINAL_AVG, FINAL_EXAM
from ..reminders import send_reminder
from ..screens import TZ_CHOICES, esc, today_for, tz_label
from ..states import Inp
from .common import Event, resume_onboarding

router = Router(name="settings")


async def _settings(event: Event, ctx: Ctx, notice: str | None = None) -> None:
    await ctx.mode()
    await ctx.show(event, await screens.settings_screen(ctx.repo, ctx.user, notice, is_owner=ctx.is_owner))


@router.callback_query(Nav.filter(F.to == "settings"))
async def settings_view(cq: CallbackQuery, ctx: Ctx) -> None:
    await _settings(cq, ctx)


# ---------------------------------------------------------------- семестр и сессия


async def _semester(event: Event, ctx: Ctx, notice: str | None = None) -> None:
    await ctx.mode()
    await ctx.show(event, await screens.semester_settings_screen(ctx.repo, ctx.user, notice))


@router.callback_query(Nav.filter(F.to == "sem"))
async def semester_view(cq: CallbackQuery, ctx: Ctx) -> None:
    await _semester(cq, ctx)


@router.callback_query(SemSet.filter())
async def semester_action(cq: CallbackQuery, callback_data: SemSet, ctx: Ctx) -> None:
    action, value = callback_data.a, callback_data.v
    sem_id = ctx.user.semester_id
    semester = await ctx.repo.get_semester(ctx.user.id, sem_id)
    if semester is None:
        await _settings(cq, ctx)
        return
    notice = None
    if action == "course" and 1 <= value <= 4:
        semester = await ctx.repo.update_semester(ctx.user.id, sem_id, course=value)
        notice = f"🎓 Теперь: {esc(semester.label)}." if semester else None
    elif action == "term" and value in (1, 2):
        semester = await ctx.repo.update_semester(ctx.user.id, sem_id, term=value)
        notice = f"🎓 Теперь: {esc(semester.label)}." if semester else None
    elif action in ("session", "holidays"):
        await ctx.mode(Inp.dates, which=action)
        await ctx.show(cq, screens.onboarding_dates(action, from_settings=True))
        return
    elif action in ("clear_session", "clear_holidays"):
        which = action.removeprefix("clear_")
        await ctx.repo.update_semester(ctx.user.id, sem_id, **{f"{which}_start": None, f"{which}_end": None})
        notice = "🗑 Даты убраны."
    elif action == "ask":
        await ctx.repo.update_semester(ctx.user.id, sem_id, archive_ask=not semester.archive_ask, archive_asked_at=None)
        notice = "🗂 После сессии спрошу про архив." if not semester.archive_ask else "🗂 Про архив спрашивать не буду."
    await _semester(cq, ctx, notice)


@router.message(Inp.dates, F.text)
async def dates_text(message: Message, ctx: Ctx) -> None:
    data = await ctx.data()
    which = data.get("which") if data.get("which") in ("session", "holidays") else "session"
    parsed = parse_range(message.text or "", today_for(ctx.user))
    if parsed is None:
        notice = "⚠️ Не понял даты. Пример: <code>22.12–14.01</code> или <code>22 декабря – 14 января</code>"
        await ctx.show(message, screens.onboarding_dates(which, notice, from_settings=True))
        return
    start, end = parsed
    await ctx.repo.update_semester(
        ctx.user.id, ctx.user.semester_id, **{f"{which}_start": start.isoformat(), f"{which}_end": end.isoformat()},
        archive_asked_at=None,
    )  # fmt: skip
    label = "Сессия" if which == "session" else "Каникулы"
    await _semester(message, ctx, f"✅ {label}: {fmt_range(start.isoformat(), end.isoformat())}")


# ---------------------------------------------------------------- как считать


@router.callback_query(Nav.filter(F.to == "calc"))
async def calc_view(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode()
    await ctx.show(cq, screens.calc_screen(ctx.user))


@router.callback_query(CalcSet.filter())
async def calc_set(cq: CallbackQuery, callback_data: CalcSet, ctx: Ctx) -> None:
    if callback_data.a == "sem":
        ctx.user = await ctx.repo.update_user(ctx.user.id, calc_mode=CALC_MEAN if callback_data.v else CALC_SSO)
        notice = "🧮 За семестр: простое среднее." if callback_data.v else "🧮 За семестр: по правилам ССО (п. 22)."
    else:
        ctx.user = await ctx.repo.update_user(ctx.user.id, final_mode=FINAL_EXAM if callback_data.v else FINAL_AVG)
        notice = "🧮 Итог: только экзамен." if callback_data.v else "🧮 Итог: (семестровая + экзамен) / 2."
    await ctx.mode()
    await ctx.show(cq, screens.calc_screen(ctx.user, notice))


# ---------------------------------------------------------------- напоминания


@router.callback_query(Nav.filter(F.to == "rem"))
async def reminders_view(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode()
    await ctx.show(cq, screens.reminders_screen(ctx.user))


TIME_TITLE = "⌨️ <b>Своё время напоминаний</b>"
TIME_BODY = "Отправь время, например <code>19:30</code> или просто <code>8</code> (это 08:00)."


@router.callback_query(Rem.filter())
async def reminders_action(cq: CallbackQuery, callback_data: Rem, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    action, value = callback_data.a, callback_data.v
    notice = None
    if action == "every" and 0 <= value <= 3:
        ctx.user = await ctx.repo.update_user(ctx.user.id, remind_every=value, snooze_until=None)
        notice = "🔕 Напоминания выключены." if value == 0 else f"🔔 Буду напоминать {remind_every_label(value)}."
    elif action == "time" and 0 <= value < 24 * 60:
        at = f"{value // 60:02d}:{value % 60:02d}"
        ctx.user = await ctx.repo.update_user(ctx.user.id, remind_time=at)
        notice = f"🕗 Время напоминаний: {at}."
    elif action == "sun":
        ctx.user = await ctx.repo.update_user(ctx.user.id, skip_sunday=not ctx.user.skip_sunday)
        notice = "🗓 По воскресеньям не беспокою." if ctx.user.skip_sunday else "🗓 Воскресенье — как обычный день."
    elif action == "works":
        ctx.user = await ctx.repo.update_user(ctx.user.id, works_reminders=not ctx.user.works_reminders)
        notice = (
            "🔬 Буду напоминать про незачтённые работы." if ctx.user.works_reminders else "🔬 Про работы не напоминаю."
        )
    elif action == "custom":
        await ctx.mode(Inp.time)
        await ctx.show(cq, screens.prompt_screen(TIME_TITLE, TIME_BODY, Nav(to="rem")))
        return
    elif action == "test":
        callback_answer.text = "Отправил пример 👇"
        await send_reminder(ctx.bot, ctx.repo, ctx.panel, ctx.user, test=True)
        await ctx.mode()
        return
    await ctx.mode()
    await ctx.show(cq, screens.reminders_screen(ctx.user, notice))


@router.message(Inp.time, F.text)
async def reminders_time_text(message: Message, ctx: Ctx) -> None:
    at = parse_hhmm(message.text or "")
    if at is None:
        await ctx.show(
            message, screens.prompt_screen(TIME_TITLE, TIME_BODY, Nav(to="rem"), "⚠️ Не похоже на время. Пример: 19:30")
        )
        return
    fields: dict[str, object] = {"remind_time": at.strftime("%H:%M")}
    notice = f"🕗 Время напоминаний: {at:%H:%M}."
    if ctx.user.remind_every == 0:
        fields["remind_every"] = 2
        notice += " Напоминания снова включены (раз в 2 дня)."
    ctx.user = await ctx.repo.update_user(ctx.user.id, **fields)
    await ctx.mode()
    await ctx.show(message, screens.reminders_screen(ctx.user, notice))


# ---------------------------------------------------------------- часовой пояс


@router.callback_query(Nav.filter(F.to == "tz"))
async def tz_view(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode(Inp.tz)
    await ctx.show(cq, screens.tz_screen(ctx.user))


def _tz_notice(ctx: Ctx) -> str:
    return f"✅ Пояс: {esc(tz_label(ctx.user.tz))} — у тебя сейчас {fmt_time(clock.now(), ctx.user.tz)}."


@router.callback_query(TzSet.filter())
async def tz_set(cq: CallbackQuery, callback_data: TzSet, ctx: Ctx) -> None:
    if 0 <= callback_data.i < len(TZ_CHOICES):
        ctx.user = await ctx.repo.update_user(ctx.user.id, tz=TZ_CHOICES[callback_data.i][1])
    await ctx.mode(Inp.tz)
    await ctx.show(cq, screens.tz_screen(ctx.user, _tz_notice(ctx)))


@router.message(Inp.tz, F.text)
async def tz_text(message: Message, ctx: Ctx) -> None:
    parsed = parse_tz(message.text or "")
    if parsed is None:
        notice = "⚠️ Не знаю такой пояс. Примеры: <code>Europe/Moscow</code>, <code>Asia/Almaty</code>, <code>+3</code>."
    else:
        ctx.user = await ctx.repo.update_user(ctx.user.id, tz=parsed[0])
        notice = _tz_notice(ctx)
    await ctx.show(message, screens.tz_screen(ctx.user, notice))


# ---------------------------------------------------------------- шкала и порог


@router.callback_query(Nav.filter(F.to == "scale"))
async def scale_view(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode()
    await ctx.show(cq, screens.scale_screen(ctx.user))


@router.callback_query(ScaleSet.filter())
async def scale_set(cq: CallbackQuery, callback_data: ScaleSet, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    notice = None
    if callback_data.a == "set" and callback_data.v in SCALES and callback_data.v != ctx.user.scale:
        top = await ctx.repo.max_grade(ctx.user)
        if top > callback_data.v:
            callback_answer.text = f"Есть оценки выше {callback_data.v} — сначала удали или исправь их."
            callback_answer.show_alert = True
            return
        ctx.user = await ctx.repo.update_user(ctx.user.id, scale=callback_data.v, pass_mark=SCALES[callback_data.v])
        notice = f"✅ Шкала: {ctx.user.scale}-балльная, сдано от {ctx.user.pass_mark}."
    elif callback_data.a == "pass":
        new = min(max(ctx.user.pass_mark + (1 if callback_data.v > 0 else -1), 1), ctx.user.scale)
        if new == ctx.user.pass_mark:
            callback_answer.text = "Дальше некуда"
        else:
            ctx.user = await ctx.repo.update_user(ctx.user.id, pass_mark=new)
            callback_answer.text = f"Сдано от: {new}"
    await ctx.mode()
    await ctx.show(cq, screens.scale_screen(ctx.user, notice))


# ---------------------------------------------------------------- чистый чат и экспорт


@router.callback_query(Nav.filter(F.to == "clean"))
async def clean_toggle(cq: CallbackQuery, ctx: Ctx) -> None:
    ctx.user = await ctx.repo.update_user(ctx.user.id, clean_chat=not ctx.user.clean_chat)
    if ctx.user.clean_chat:
        notice = "🧹 Чистый чат включён: твои сообщения убираю, меню всегда одно."
    else:
        notice = "🧹 Чистый чат выключен: все сообщения остаются в истории."
    await _settings(cq, ctx, notice)


@router.callback_query(Nav.filter(F.to == "export"))
async def export(cq: CallbackQuery, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    data, filename = await build_xlsx(ctx.repo, ctx.user)
    await ctx.bot.send_document(
        ctx.user.id,
        BufferedInputFile(data, filename=filename),
        caption="📤 Оценки, ОКР, работы, экзамены и итоги семестра — таблица Excel. "
        "Открывается в Excel, Google Таблицах и прямо на телефоне.",
    )
    callback_answer.text = "Файл отправлен 👇"
    await ctx.mode()
    screen = await screens.settings_screen(ctx.repo, ctx.user, "📤 Файл с оценками — сообщением выше.", ctx.is_owner)
    await ctx.fresh(None, screen)


# ---------------------------------------------------------------- удаление всего


@router.callback_query(Nav.filter(F.to == "wipe"))
async def wipe_confirm(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode()
    await ctx.show(cq, screens.wipe_screen())


@router.callback_query(Nav.filter(F.to == "wipe_yes"))
async def wipe(cq: CallbackQuery, ctx: Ctx) -> None:
    name, username = ctx.user.name, ctx.user.username
    await ctx.repo.delete_user(ctx.user.id)
    ctx.user = await ctx.repo.ensure_user(ctx.user.id, name, username)
    await resume_onboarding(cq, ctx, notice="🗑 Все данные удалены. Начнём заново!")
