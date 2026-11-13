"""Общие сценарии, которые нужны нескольким разделам бота."""

from __future__ import annotations

import re
from collections.abc import Sequence

from aiogram.types import CallbackQuery, Message

from .. import screens
from ..callbacks import Arch, UndoGrades, UndoOkr
from ..context import Ctx
from ..logic import (
    MAX_BATCH,
    MAX_SUBJECTS,
    Overview,
    display_name,
    fmt_num,
    match_subjects,
    parse_marks,
    parse_quick,
    split_segments,
    subject_icon,
    truncate,
)
from ..models import Subject
from ..screens import Row, btn, esc, ex
from ..states import Inp, OnbSt

Event = Message | CallbackQuery

NOT_FOUND = "⚠️ Предмет не найден — возможно, он удалён или в архиве."
HAS_LETTERS = re.compile(r"[^\W\d_]")
HAS_DIGITS = re.compile(r"[0-9]")
MAX_PICK = 15  # столько оценок влезает в кнопку выбора предмета (лимит Telegram — 64 байта)
MAX_SEGMENTS = 15  # предметов в одном сообщении
MAX_MULTI = 40  # оценок в одном сообщении по нескольким предметам


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
        await ctx.mode(OnbSt.course)
        semester = await ctx.repo.get_semester(ctx.user.id, ctx.user.semester_id)
        screen = screens.onboarding_course(ctx.user, semester, notice)
    if fresh:
        await ctx.fresh(event, screen)
    else:
        await ctx.show(event, screen)


def subjects_notice(added: Sequence[Subject], skipped: Sequence[str]) -> str:
    parts = []
    if added:
        names = [esc(display_name(s.name)) + (f" ({esc(s.short)})" if s.short else "") for s in added]
        parts.append("✅ Добавлено: " + ", ".join(names))
        carried = carried_line(added)
        if carried:
            parts.append(carried)
    if skipped:
        parts.append(f"↪️ Пропущено (уже есть или больше {MAX_SUBJECTS}): " + ", ".join(esc(n) for n in skipped))
    return "\n".join(parts) or "⚠️ Не нашёл названий предметов — отправь их через запятую."


def carried_line(subjects: Sequence[Subject], limit: int = 8) -> str | None:
    """«📚 Отметки за прошлый семестр сохранены: ТОЭ (7), Физика (8)» — для предметов, которые продолжаются."""
    items = [
        f"{esc(s.short or display_name(s.name))} ({', '.join(map(str, parse_marks(s.prev_marks)))})"
        for s in subjects
        if parse_marks(s.prev_marks)
    ]
    if not items:
        return None
    shown = ", ".join(items[:limit]) + (f" и ещё {len(items) - limit}" if len(items) > limit else "")
    return f"📚 Отметки за прошлый семестр сохранены: {shown}. Они войдут в итог по предмету."


def quick_names(subjects: Sequence[Subject]) -> list[str]:
    """Названия и сокращения — чтобы «ТОЭ 8 9» и «Физика 2 8» разбирались правильно."""
    return [s.name for s in subjects] + [s.short for s in subjects if s.short]


async def looks_like_grades(ctx: Ctx, text: str) -> bool:
    """«физ 8 9» на экране, где ждут название, — это оценки, а не название.

    Одна строка — нужно хотя бы две оценки и однозначный предмет (чтобы «Физика 2»
    оставалась названием). Несколько строк — хватит одной строки «предмет + оценки».
    """
    subjects = await ctx.repo.list_subjects(ctx.user.id, ctx.user.semester_id)
    names = quick_names(subjects)
    segments = split_segments(text)
    for segment in segments:
        quick = parse_quick(segment, ctx.user.grade_min, ctx.user.grade_max, names)
        enough = len(quick.values) >= (1 if len(segments) > 1 else 2)
        if enough and not quick.bad and len(match_subjects(quick.query, subjects)) == 1:
            return True
    return False


def bad_grades_notice(bad: Sequence[str], ctx: Ctx) -> str:
    shown = ", ".join(f"«{esc(truncate(b, 12))}»" for b in bad[:5])
    e = ex(ctx.user)
    return (
        f"⚠️ Не понял оценки: {shown}. Нужны числа от {ctx.user.grade_min} до {ctx.user.grade_max}, "
        f"например <code>{e.a} {e.b}</code> или слитно <code>{e.glued}</code>."
    )


async def session_rows(ctx: Ctx) -> list[Row]:
    """Если все экзамены и зачёты сданы — кнопка «в архив»."""
    ov = Overview(await ctx.repo.views(ctx.user))
    return [[btn("🗂 Все экзамены сданы — в архив", Arch(a="ask"))]] if ov.session_complete else []


async def apply_grades(event: Event, ctx: Ctx, subject: Subject, values: Sequence[int]) -> None:
    """Добавить оценки, показать карточку предмета с итогом и кнопкой отмены."""
    if len(values) > MAX_BATCH:
        await go_subject(event, ctx, subject.id, notice=f"⚠️ Слишком много за раз — не больше {MAX_BATCH}.")
        return
    before = await ctx.repo.view(ctx.user, subject.id)
    ids = await ctx.repo.add_grades(ctx.user, subject.id, values)
    after = await ctx.repo.view(ctx.user, subject.id)
    pluses = ", ".join(f"+{v}" for v in values)
    old = before.semester_exact if before else None
    new = after.semester_exact if after else None
    if old is not None and fmt_num(old) == fmt_num(new):
        change = f"за семестр не изменилось: <b>{fmt_num(new)}</b>"
    else:
        change = f"за семестр {fmt_num(old)} → <b>{fmt_num(new)}</b>"
    notice = f"✅ <b>{esc(display_name(subject.name))}</b>: {pluses} · {change}"
    undo = [[btn("↩️ Отменить", UndoGrades(s=subject.id, a=ids[0], b=ids[-1]))]]
    await go_subject(event, ctx, subject.id, notice, undo)


async def free_okr_slot(ctx: Ctx, subject: Subject) -> int | None:
    """Первая ОКР без оценки; если ОКР одна — она; если все выставлены — None."""
    view = await ctx.repo.view(ctx.user, subject.id)
    empty = [n for n, grade in (view.okr_slots if view else []) if grade is None]
    if empty:
        return empty[0]
    return 1 if subject.okr_count == 1 else None


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
        slot = await free_okr_slot(ctx, subject)
        if slot is None:
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
        status = f"❌ ниже {ctx.user.pass_mark} — пересдача в течение двух недель"
    was = f" (было {previous})" if previous is not None else ""
    notice = f"📌 <b>{name}</b>: {label} — <b>{value}</b> {status}{was}"
    undo = [[btn("↩️ Отменить", UndoOkr(s=subject.id, n=slot, v=previous or 0))]]
    await go_subject(event, ctx, subject.id, notice, undo)


async def handle_free_text(message: Message, ctx: Ctx) -> None:
    """Быстрый ввод: «физ 8 9», «8 9», «физра 10910», «физ окр 7», «физ», несколько строк."""
    if not ctx.user.onboarded:
        await resume_onboarding(message, ctx, notice="Давай сначала закончим настройку 🙂")
        return
    subjects = await ctx.repo.list_subjects(ctx.user.id, ctx.user.semester_id)
    if not subjects:
        await go_main(message, ctx, notice="Сначала добавь предметы — кнопка «➕ Предметы».")
        return

    text = message.text or ""
    segments = split_segments(text)
    if sum(1 for seg in segments if HAS_DIGITS.search(seg) and HAS_LETTERS.search(seg)) >= 2:
        await handle_multi(message, ctx, segments, subjects)
        return
    quick = parse_quick(text, ctx.user.grade_min, ctx.user.grade_max, quick_names(subjects))
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

    e = ex(ctx.user)
    await back(
        f"🤔 Не понял «{esc(truncate(text.strip(), 40))}».\n"
        f"Примеры: <code>физ {e.a} {e.b}</code> · <code>физ {e.glued}</code> · <code>{e.a} {e.b}</code> · "
        f"<code>физ окр {e.okr}</code> · <code>физ</code>"
    )


async def handle_multi(message: Message, ctx: Ctx, segments: Sequence[str], subjects: Sequence[Subject]) -> None:
    """Оценки сразу по нескольким предметам: каждая строка (или «, предмет») — свой предмет."""
    names = quick_names(subjects)
    plan: list[tuple[Subject, list[int], bool]] = []
    problems: list[str] = []
    for segment in segments[:MAX_SEGMENTS]:
        quick = parse_quick(segment, ctx.user.grade_min, ctx.user.grade_max, names)
        shown = f"<code>{esc(truncate(segment, 40))}</code>"
        matches = match_subjects(quick.query, subjects) if quick.query else []
        if quick.bad:
            problems.append(f"{shown} — оценки от {ctx.user.grade_min} до {ctx.user.grade_max}")
        elif not quick.values:
            problems.append(f"{shown} — нет оценок")
        elif not quick.query:
            problems.append(f"{shown} — не указан предмет")
        elif len(matches) != 1:
            problems.append(f"{shown} — " + ("подходит несколько предметов" if matches else "нет такого предмета"))
        else:
            plan.append((matches[0], quick.values, quick.okr))
    if len(segments) > MAX_SEGMENTS:
        problems.append(f"…и ещё {len(segments) - MAX_SEGMENTS} — за раз не больше {MAX_SEGMENTS} строк")

    total = sum(len(values) for _, values, okr in plan if not okr)
    if total > MAX_MULTI:
        await go_main(message, ctx, notice=f"⚠️ Слишком много оценок за раз — не больше {MAX_MULTI}.")
        return

    lines: list[str] = []
    batches = [(subject.id, values) for subject, values, okr in plan if not okr]
    ids = await ctx.repo.add_grades_multi(ctx.user, batches) if batches else []
    for subject, values, okr in plan:
        label = subject.short or display_name(subject.name)
        name = f"{subject_icon(subject.name)} {esc(label)}"
        if not okr:
            lines.append(f"{name}: " + ", ".join(f"+{v}" for v in values))
            continue
        if subject.okr_count == 0:
            problems.append(f"<code>{esc(label)} окр</code> — в предмете нет ОКР")
            continue
        slot = await free_okr_slot(ctx, subject)
        if slot is None:
            problems.append(f"{name} — все ОКР уже выставлены, поменяй в карточке предмета")
            continue
        previous = await ctx.repo.set_okr(ctx.user, subject.id, slot, values[0])
        mark_ = "✅" if values[0] >= ctx.user.pass_mark else "❌"
        label_okr = "ОКР" if subject.okr_count == 1 else f"ОКР №{slot}"
        was = f" (было {previous})" if previous is not None else ""
        lines.append(f"{name}: {label_okr} — {values[0]} {mark_}{was}")

    notice_lines = []
    if lines:
        notice_lines += ["✅ <b>Записал:</b>", *lines]
    if problems:
        if notice_lines:
            notice_lines.append("")
        notice_lines += ["⚠️ <b>Не понял</b> (можно нажать, скопировать и поправить):", *problems]
    top = [[btn("↩️ Отменить оценки", UndoGrades(s=0, a=min(ids), b=max(ids)))]] if ids else []
    await go_main(message, ctx, notice="\n".join(notice_lines), top_rows=top)
