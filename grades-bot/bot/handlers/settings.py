"""Настройки: напоминания, часовой пояс, шкала, чистый чат, семестры, экспорт, удаление."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer

from .. import clock, screens
from ..callbacks import Nav, Rem, ScaleSet, Sem, TzSet
from ..context import Ctx
from ..export import build_csv
from ..logic import (
    SCALES,
    SUBJECT_FORMS,
    fmt_time,
    parse_hhmm,
    parse_tz,
    plural,
    remind_every_label,
)
from ..reminders import send_reminder
from ..screens import TZ_CHOICES, esc, tz_label
from ..states import Inp, OnbSt
from .common import Event, go_main, resume_onboarding

router = Router(name="settings")


async def _settings(event: Event, ctx: Ctx, notice: str | None = None) -> None:
    await ctx.mode()
    await ctx.show(event, await screens.settings_screen(ctx.repo, ctx.user, notice))


@router.callback_query(Nav.filter(F.to == "settings"))
async def settings_view(cq: CallbackQuery, ctx: Ctx) -> None:
    await _settings(cq, ctx)


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
            message,
            screens.prompt_screen(TIME_TITLE, TIME_BODY, Nav(to="rem"), "⚠️ Не похоже на время. Пример: 19:30"),
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
        notice = f"✅ Шкала: {ctx.user.scale}-балльная, порог ОКР — {ctx.user.pass_mark}."
    elif callback_data.a == "pass":
        new = min(max(ctx.user.pass_mark + (1 if callback_data.v > 0 else -1), 1), ctx.user.scale)
        if new == ctx.user.pass_mark:
            callback_answer.text = "Дальше некуда"
        else:
            ctx.user = await ctx.repo.update_user(ctx.user.id, pass_mark=new)
            callback_answer.text = f"Порог ОКР: {new}"
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
    data, filename = await build_csv(ctx.repo, ctx.user)
    await ctx.bot.send_document(
        ctx.user.id,
        BufferedInputFile(data, filename=filename),
        caption="📤 Все оценки, ОКР и работы текущего семестра. Открывается в Excel и Google Таблицах.",
    )
    callback_answer.text = "Файл отправлен 👇"
    await ctx.mode()
    await ctx.fresh(None, await screens.settings_screen(ctx.repo, ctx.user, "📤 Файл с оценками — сообщением выше."))


# ---------------------------------------------------------------- удаление всего


@router.callback_query(Nav.filter(F.to == "wipe"))
async def wipe_confirm(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode()
    await ctx.show(cq, screens.wipe_screen())


@router.callback_query(Nav.filter(F.to == "wipe_yes"))
async def wipe(cq: CallbackQuery, ctx: Ctx) -> None:
    name = ctx.user.name
    await ctx.repo.delete_user(ctx.user.id)
    ctx.user = await ctx.repo.ensure_user(ctx.user.id, name)
    await resume_onboarding(cq, ctx, notice="🗑 Все данные удалены. Начнём заново!")


# ---------------------------------------------------------------- семестры


@router.callback_query(Sem.filter(F.a == "list"))
async def semesters_view(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode()
    await ctx.show(cq, await screens.semesters_screen(ctx.repo, ctx.user))


@router.callback_query(Sem.filter(F.a == "view"))
async def semester_view(cq: CallbackQuery, callback_data: Sem, ctx: Ctx) -> None:
    screen = None
    if callback_data.id != ctx.user.semester_id:
        screen = await screens.semester_view_screen(ctx.repo, ctx.user, callback_data.id)
    await ctx.mode()
    await ctx.show(cq, screen or await screens.semesters_screen(ctx.repo, ctx.user))


@router.callback_query(Sem.filter(F.a == "new"))
async def semester_new_confirm(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode()
    await ctx.show(cq, await screens.new_semester_confirm(ctx.repo, ctx.user))


@router.callback_query(Sem.filter(F.a == "new_yes"))
async def semester_new(cq: CallbackQuery, ctx: Ctx) -> None:
    previous_id = ctx.user.semester_id or 0
    previous_count = len(await ctx.repo.list_subjects(ctx.user.id, previous_id))
    semester = await ctx.repo.start_new_semester(ctx.user)
    await ctx.refresh_user()
    await ctx.mode()
    await ctx.show(cq, screens.new_semester_subjects(semester.title, previous_id, previous_count))


@router.callback_query(Sem.filter(F.a == "copy"))
async def semester_copy(cq: CallbackQuery, callback_data: Sem, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    if await ctx.repo.list_subjects(ctx.user.id, ctx.user.semester_id):
        callback_answer.text = "Предметы уже есть"
        await go_main(cq, ctx)
        return
    if callback_data.id == ctx.user.semester_id:
        await go_main(cq, ctx)
        return
    copied = await ctx.repo.copy_subjects(ctx.user.id, callback_data.id, ctx.user.semester_id or 0)
    await go_main(cq, ctx, notice=f"📋 Перенёс {plural(copied, SUBJECT_FORMS)}. Удачного семестра! 🍀")


@router.callback_query(Sem.filter(F.a == "fresh"))
async def semester_fresh(cq: CallbackQuery, ctx: Ctx) -> None:
    await ctx.mode(OnbSt.subjects)
    await ctx.show(cq, await screens.onboarding_subjects(ctx.repo, ctx.user))
