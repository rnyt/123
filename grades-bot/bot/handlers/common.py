"""Общие сценарии, которые нужны нескольким разделам бота."""

from __future__ import annotations

import re
from collections.abc import Sequence

from aiogram.types import CallbackQuery, Message

from .. import screens
from ..callbacks import UndoGrades, UndoOkr
from ..context import Ctx
from ..logic import (
    MAX_BATCH,
    MAX_SUBJECTS,
    display_name,
    fmt_num,
    match_subjects,
    parse_quick,
    truncate,
)
from ..models import Subject
from ..screens import Row, btn, esc
from ..states import Inp, OnbSt

Event = Message | CallbackQuery

NOT_FOUND = "⚠️ Предмет не найден — возможно, он удалён или в архиве."
HAS_LETTERS = re.compile(r"[^\W\d_]")
MAX_PICK = 15  # столько оценок влезает в кнопку выбора предмета (лимит Telegram — 64 байта)


async def go_main(
    event: Event, ctx: Ctx, notice: str | None = None, fresh: bool = False, top_rows: Sequence[Row] = ()
) -> None:
    if not ctx.user.onboarded:
        await resume_onboarding(event, ctx, fresh=fresh)
        return
    await ctx.mode()
    screen = await screens.main_screen(ctx.repo, ctx.user, notice, top_rows)
    if fresh:
        await ctx.fresh(event, screen)
    else:
        await ctx.show(event, screen)


async def go_subject(event: Event, ctx: Ctx, sid: int, notice: str | None = None, top_rows: Sequence[Row] = ()) -> None:
    screen = await screens.subject_screen(ctx.repo, ctx.user, sid, notice, top_rows)
    if screen is None:
        await go_main(event, ctx, notice=NOT_FOUND)
        return
    await ctx.mode(None, sid=sid)
    await ctx.show(event, screen)


async def resume_onboarding(event: Event, ctx: Ctx, fresh: bool = False, notice: str | None = None) -> None:
    """Продолжить знакомство с того места, где пользователь остановился."""
    ctx.user = await ctx.repo.ensure_semester(ctx.user)
    subjects = await ctx.repo.list_subjects(ctx.user.id, ctx.user.semester_id)
    if subjects:
        await ctx.mode(OnbSt.subjects)
        screen = await screens.onboarding_subjects(ctx.repo, ctx.user, notice)
    else:
        await ctx.mode(OnbSt.scale)
        screen = screens.onboarding_scale(ctx.user, notice)
    if fresh:
        await ctx.fresh(event, screen)
    else:
        await ctx.show(event, screen)


def subjects_notice(added: Sequence[Subject], skipped: Sequence[str]) -> str:
    parts = []
    if added:
        parts.append("✅ Добавлено: " + ", ".join(esc(display_name(s.name)) for s in added))
    if skipped:
        parts.append(f"↪️ Пропущено (уже есть или больше {MAX_SUBJECTS}): " + ", ".join(esc(n) for n in skipped))
    return "\n".join(parts) or "⚠️ Не нашёл названий предметов — отправь их через запятую."


def bad_grades_notice(bad: Sequence[str], ctx: Ctx) -> str:
    shown = ", ".join(f"«{esc(truncate(b, 12))}»" for b in bad[:5])
    return (
        f"⚠️ Не понял оценки: {shown}. Нужны целые числа от {ctx.user.grade_min} "
        f"до {ctx.user.grade_max}, например <code>8 9</code>."
    )


async def apply_grades(event: Event, ctx: Ctx, subject: Subject, values: Sequence[int]) -> None:
    """Добавить оценки, показать карточку предмета с итогом и кнопкой отмены."""
    if len(values) > MAX_BATCH:
        await go_subject(event, ctx, subject.id, notice=f"⚠️ Слишком много за раз — не больше {MAX_BATCH}.")
        return
    before = await ctx.repo.view(ctx.user, subject.id)
    ids = await ctx.repo.add_grades(ctx.user, subject.id, values)
    after = await ctx.repo.view(ctx.user, subject.id)
    pluses = ", ".join(f"+{v}" for v in values)
    old_avg = before.avg if before else None
    new_avg = after.avg if after else None
    if old_avg is not None and fmt_num(old_avg) == fmt_num(new_avg):
        change = f"средний не изменился: <b>{fmt_num(new_avg)}</b>"
    else:
        change = f"средний {fmt_num(old_avg)} → <b>{fmt_num(new_avg)}</b>"
    notice = f"✅ <b>{esc(display_name(subject.name))}</b>: {pluses} · {change}"
    undo = [[btn("↩️ Отменить", UndoGrades(s=subject.id, a=ids[0], b=ids[-1]))]]
    await go_subject(event, ctx, subject.id, notice, undo)


async def apply_okr(event: Event, ctx: Ctx, subject: Subject, value: int, slot: int | None = None) -> None:
    """Поставить оценку за ОКР (в первую свободную, если номер не указан)."""
    name = esc(display_name(subject.name))
    if subject.okr_count == 0:
        await go_subject(
            event, ctx, subject.id,
            notice=f"ℹ️ В «{name}» ОКР не предусмотрена. Включить: ⚙️ Предмет → «ОКР: 1».",
        )  # fmt: skip
        return
    if slot is None:
        view = await ctx.repo.view(ctx.user, subject.id)
        empty = [n for n, grade in (view.okr_slots if view else []) if grade is None]
        if empty:
            slot = empty[0]
        elif subject.okr_count == 1:
            slot = 1
        else:
            await ctx.mode(None, sid=subject.id)
            screen = await screens.okr_screen(
                ctx.repo, ctx.user, subject.id, None, notice="Все ОКР уже выставлены — выбери, какую изменить."
            )
            if screen:
                await ctx.show(event, screen)
            return
    previous = await ctx.repo.set_okr(ctx.user, subject.id, slot, value)
    label = "ОКР" if subject.okr_count == 1 else f"ОКР №{slot}"
    if value >= ctx.user.pass_mark:
        status = "✅ сдана"
    else:
        status = f"❌ ниже {ctx.user.pass_mark} — пока не аттестован"
    was = f" (было {previous})" if previous is not None else ""
    notice = f"📌 <b>{name}</b>: {label} — <b>{value}</b> {status}{was}"
    undo = [[btn("↩️ Отменить", UndoOkr(s=subject.id, n=slot, v=previous or 0))]]
    await go_subject(event, ctx, subject.id, notice, undo)


async def handle_free_text(message: Message, ctx: Ctx) -> None:
    """Быстрый ввод: «физ 8 9», «8 9», «физ окр 7», «физ»."""
    if not ctx.user.onboarded:
        await resume_onboarding(message, ctx, notice="Давай сначала закончим настройку 🙂")
        return
    subjects = await ctx.repo.list_subjects(ctx.user.id, ctx.user.semester_id)
    if not subjects:
        await go_main(message, ctx, notice="Сначала добавь предметы — кнопка «➕ Предметы».")
        return

    text = message.text or ""
    quick = parse_quick(text, ctx.user.grade_min, ctx.user.grade_max, [s.name for s in subjects])
    data = await ctx.data()
    current = next((s for s in subjects if s.id == data.get("sid")), None)

    async def back(notice: str) -> None:
        if current:
            await go_subject(message, ctx, current.id, notice)
        else:
            await go_main(message, ctx, notice=notice)

    if quick.bad:
        await back(bad_grades_notice(quick.bad, ctx))
        return

    if quick.values:
        if len(quick.values) > MAX_BATCH:
            await back(f"⚠️ Слишком много оценок за раз — не больше {MAX_BATCH}.")
            return
        target: Subject | None = None
        if quick.query:
            matches = match_subjects(quick.query, subjects)
            if len(matches) == 1:
                target = matches[0]
            elif len(quick.values) > MAX_PICK:
                await back(f"🔎 Не нашёл предмет «{esc(truncate(quick.query, 30))}» — проверь название.")
                return
            else:
                notice = None if matches else f"🔎 Не нашёл предмет «{esc(truncate(quick.query, 30))}»."
                await ctx.mode()
                await ctx.show(message, screens.pick_screen(matches or subjects, quick.values, quick.okr, notice))
                return
        elif current:
            target = current
        elif len(subjects) == 1:
            target = subjects[0]
        elif len(quick.values) > MAX_PICK:
            await back(
                f"⚠️ Это много оценок — укажи предмет в начале: <code>физ {' '.join(map(str, quick.values[:3]))} …</code>"
            )
            return
        else:
            await ctx.mode()
            await ctx.show(message, screens.pick_screen(subjects, quick.values, quick.okr))
            return
        if quick.okr:
            await apply_okr(message, ctx, target, quick.values[0])
        else:
            await apply_grades(message, ctx, target, quick.values)
        return

    if quick.query:
        matches = match_subjects(quick.query, subjects)
        if len(matches) == 1:
            subject = matches[0]
            if quick.okr:
                slot = 1 if subject.okr_count == 1 else None
                await ctx.mode(Inp.okr if slot else None, sid=subject.id, slot=slot or 0)
                screen = await screens.okr_screen(ctx.repo, ctx.user, subject.id, slot)
                if screen:
                    await ctx.show(message, screen)
                return
            await go_subject(message, ctx, subject.id)
            return
        if matches:
            await ctx.mode()
            await ctx.show(message, screens.open_pick_screen(matches, quick.query))
            return

    await back(
        f"🤔 Не понял «{esc(truncate(text.strip(), 40))}».\n"
        "Примеры: <code>физ 8 9</code> · <code>8 9</code> · <code>физ окр 7</code> · <code>физ</code>"
    )
