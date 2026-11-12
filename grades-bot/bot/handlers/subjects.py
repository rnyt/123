"""Карточка предмета: оценки, ОКР, правка оценок, настройки предмета."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer

from .. import screens
from ..callbacks import AddGrade, DelGrade, OkrSet, Subj, UndoDelGrade, UndoGrades, UndoOkr
from ..context import Ctx
from ..logic import (
    GRADE_FORMS,
    MAX_BATCH,
    MAX_NAME_LEN,
    display_name,
    fmt_date,
    fmt_num,
    norm,
    parse_grades,
    plural,
)
from ..models import Subject
from ..screens import btn, esc, ex
from ..states import Inp
from .common import (
    HAS_LETTERS,
    MAX_MULTI,
    NOT_FOUND,
    Event,
    apply_grades,
    apply_okr,
    bad_grades_notice,
    go_main,
    go_subject,
    handle_free_text,
)

router = Router(name="subjects")


async def _subject(event: Event, ctx: Ctx, sid: int) -> Subject | None:
    subject = await ctx.repo.get_subject(ctx.user, sid)
    if subject is None:
        await go_main(event, ctx, notice=NOT_FOUND)
    return subject


@router.callback_query(Subj.filter(F.a == "v"))
async def subject_view(cq: CallbackQuery, callback_data: Subj, ctx: Ctx) -> None:
    await go_subject(cq, ctx, callback_data.id)


# ---------------------------------------------------------------- добавление оценок


@router.callback_query(Subj.filter(F.a == "add"))
async def add_grades_view(cq: CallbackQuery, callback_data: Subj, ctx: Ctx) -> None:
    view = await ctx.repo.view(ctx.user, callback_data.id)
    if view is None:
        await go_main(cq, ctx, notice=NOT_FOUND)
        return
    await ctx.mode(Inp.grades, sid=view.id, added=[], before=view.avg)
    screen = await screens.add_grades_screen(ctx.repo, ctx.user, view.id)
    if screen:
        await ctx.show(cq, screen)


@router.callback_query(AddGrade.filter())
async def add_grade_tap(cq: CallbackQuery, callback_data: AddGrade, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    subject = await _subject(cq, ctx, callback_data.s)
    if subject is None:
        return
    value = callback_data.v
    if not ctx.user.grade_min <= value <= ctx.user.grade_max:
        callback_answer.text = "Такой оценки нет в твоей шкале"
        return
    data = await ctx.data()
    if data.get("sid") != subject.id or not isinstance(data.get("added"), list):
        view = await ctx.repo.view(ctx.user, subject.id)
        data = {"sid": subject.id, "added": [], "before": view.avg if view else None}
    if len(data["added"]) >= 50:
        callback_answer.text = "Многовато за раз — нажми «Готово» 🙂"
        return
    ids = await ctx.repo.add_grades(ctx.user, subject.id, [value])
    data["added"].append([ids[0], value])
    await ctx.mode(Inp.grades, **data)
    screen = await screens.add_grades_screen(
        ctx.repo, ctx.user, subject.id, [v for _, v in data["added"]], data["before"]
    )
    view = await ctx.repo.view(ctx.user, subject.id)
    callback_answer.text = f"+{value} → средний {fmt_num(view.avg if view else None)}"
    if screen:
        await ctx.show(cq, screen)


@router.callback_query(Subj.filter(F.a == "addundo"))
async def add_grade_undo_last(
    cq: CallbackQuery, callback_data: Subj, ctx: Ctx, callback_answer: CallbackAnswer
) -> None:
    data = await ctx.data()
    added = data.get("added") if data.get("sid") == callback_data.id else None
    if added:
        grade_id, value = added.pop()
        await ctx.repo.delete_grades(ctx.user, callback_data.id, [grade_id])
        await ctx.mode(Inp.grades, **data)
        callback_answer.text = f"Убрал {value}"
    else:
        callback_answer.text = "Отменять нечего"
        data = {"sid": callback_data.id, "added": [], "before": data.get("before")}
        await ctx.mode(Inp.grades, **data)
    screen = await screens.add_grades_screen(
        ctx.repo, ctx.user, callback_data.id, [v for _, v in data["added"]], data.get("before")
    )
    if screen is None:
        await go_main(cq, ctx, notice=NOT_FOUND)
        return
    await ctx.show(cq, screen)


@router.callback_query(Subj.filter(F.a == "done"))
async def add_grades_done(cq: CallbackQuery, callback_data: Subj, ctx: Ctx) -> None:
    data = await ctx.data()
    added = [v for _, v in data.get("added", [])] if data.get("sid") == callback_data.id else []
    notice = None
    if added:
        view = await ctx.repo.view(ctx.user, callback_data.id)
        notice = (
            f"✅ Добавлено: {', '.join(map(str, added))} · средний {fmt_num(data.get('before'))} → "
            f"<b>{fmt_num(view.avg if view else None)}</b>"
        )
    await go_subject(cq, ctx, callback_data.id, notice)


@router.message(Inp.grades, F.text)
async def add_grades_text(message: Message, ctx: Ctx) -> None:
    data = await ctx.data()
    subject = await ctx.repo.get_subject(ctx.user, data.get("sid", 0))
    text = message.text or ""
    if subject is None or HAS_LETTERS.search(text):
        # «физ 8 9» прямо с экрана оценок — это быстрый ввод, пусть разберётся он.
        await ctx.mode(None, sid=subject.id if subject else 0)
        await handle_free_text(message, ctx)
        return
    values, bad = parse_grades(text, ctx.user.grade_min, ctx.user.grade_max)
    if bad or not values:
        screen = await screens.add_grades_screen(
            ctx.repo, ctx.user, subject.id,
            [v for _, v in data.get("added", [])], data.get("before"),
            notice=bad_grades_notice(bad or [text], ctx),
        )  # fmt: skip
        if screen:
            await ctx.show(message, screen)
        return
    await apply_grades(message, ctx, subject, values)


@router.callback_query(UndoGrades.filter())
async def undo_grades(cq: CallbackQuery, callback_data: UndoGrades, ctx: Ctx) -> None:
    several = callback_data.s == 0  # оценки в нескольких предметах сразу
    limit = MAX_MULTI if several else MAX_BATCH
    if not 0 <= callback_data.b - callback_data.a < limit:
        await go_main(cq, ctx)
        return
    ids = list(range(callback_data.a, callback_data.b + 1))
    removed = await ctx.repo.delete_grades(ctx.user, None if several else callback_data.s, ids)
    notice = f"↩️ Отменено: убрал {plural(removed, GRADE_FORMS)}." if removed else "↩️ Уже отменено."
    if several:
        await go_main(cq, ctx, notice=notice)
    else:
        await go_subject(cq, ctx, callback_data.s, notice)


# ---------------------------------------------------------------- ОКР


@router.callback_query(Subj.filter(F.a == "okr"))
async def okr_view(cq: CallbackQuery, callback_data: Subj, ctx: Ctx) -> None:
    subject = await _subject(cq, ctx, callback_data.id)
    if subject is None:
        return
    slot = 1 if subject.okr_count == 1 else (callback_data.n or None)
    if slot and not 1 <= slot <= subject.okr_count:
        slot = None
    await ctx.mode(Inp.okr if slot else None, sid=subject.id, slot=slot or 0)
    screen = await screens.okr_screen(ctx.repo, ctx.user, subject.id, slot)
    if screen:
        await ctx.show(cq, screen)


@router.callback_query(OkrSet.filter())
async def okr_set(cq: CallbackQuery, callback_data: OkrSet, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    subject = await _subject(cq, ctx, callback_data.s)
    if subject is None:
        return
    slot, value = callback_data.n, callback_data.v
    if not 1 <= slot <= subject.okr_count:
        await go_subject(cq, ctx, subject.id, notice="ℹ️ Такой ОКР в предмете больше нет.")
        return
    if value == 0:
        previous = await ctx.repo.set_okr(ctx.user, subject.id, slot, None)
        label = "ОКР" if subject.okr_count == 1 else f"ОКР №{slot}"
        notice = f"🗑 Оценка за {label} убрана" + (f" (была {previous})." if previous else ".")
        top = [[btn("↩️ Вернуть", UndoOkr(s=subject.id, n=slot, v=previous))]] if previous else []
        await go_subject(cq, ctx, subject.id, notice, top)
        return
    if not ctx.user.grade_min <= value <= ctx.user.grade_max:
        callback_answer.text = "Такой оценки нет в твоей шкале"
        return
    await apply_okr(cq, ctx, subject, value, slot)


@router.message(Inp.okr, F.text)
async def okr_text(message: Message, ctx: Ctx) -> None:
    data = await ctx.data()
    subject = await ctx.repo.get_subject(ctx.user, data.get("sid", 0))
    text = message.text or ""
    if subject is None or HAS_LETTERS.search(text):
        await ctx.mode(None, sid=subject.id if subject else 0)
        await handle_free_text(message, ctx)
        return
    slot = int(data.get("slot") or 1)
    values, bad = parse_grades(text, ctx.user.grade_min, ctx.user.grade_max)
    if bad or len(values) != 1:
        screen = await screens.okr_screen(
            ctx.repo, ctx.user, subject.id, slot,
            notice=f"⚠️ Отправь одну оценку числом от {ctx.user.grade_min} до {ctx.user.grade_max}, например <code>{ex(ctx.user).okr}</code>.",
        )  # fmt: skip
        if screen:
            await ctx.show(message, screen)
        return
    await apply_okr(message, ctx, subject, values[0], slot)


@router.callback_query(UndoOkr.filter())
async def undo_okr(cq: CallbackQuery, callback_data: UndoOkr, ctx: Ctx) -> None:
    subject = await _subject(cq, ctx, callback_data.s)
    if subject is None:
        return
    if not 1 <= callback_data.n <= subject.okr_count:
        await go_subject(cq, ctx, subject.id)
        return
    previous = callback_data.v if ctx.user.grade_min <= callback_data.v <= ctx.user.grade_max else None
    await ctx.repo.set_okr(ctx.user, subject.id, callback_data.n, previous)
    notice = f"↩️ Вернул прежнюю оценку за ОКР: {previous}." if previous else "↩️ Оценка за ОКР отменена."
    await go_subject(cq, ctx, subject.id, notice)


@router.callback_query(Subj.filter(F.a == "okron"))
async def okr_enable(cq: CallbackQuery, callback_data: Subj, ctx: Ctx) -> None:
    """«В предмете есть ОКР» с экрана ОКР — включаем и сразу даём поставить оценку."""
    subject = await _subject(cq, ctx, callback_data.id)
    if subject is None:
        return
    await ctx.repo.set_okr_count(ctx.user, subject.id, 1)
    await ctx.mode(Inp.okr, sid=subject.id, slot=1)
    screen = await screens.okr_screen(ctx.repo, ctx.user, subject.id, 1, notice="📌 ОКР включена.")
    if screen:
        await ctx.show(cq, screen)


# ---------------------------------------------------------------- правка оценок


@router.callback_query(Subj.filter(F.a == "edit"))
async def edit_view(cq: CallbackQuery, callback_data: Subj, ctx: Ctx) -> None:
    screen = await screens.edit_grades_screen(ctx.repo, ctx.user, callback_data.id, callback_data.n)
    if screen is None:
        await go_main(cq, ctx, notice=NOT_FOUND)
        return
    await ctx.mode(None, sid=callback_data.id)
    await ctx.show(cq, screen)


@router.callback_query(DelGrade.filter())
async def delete_grade(cq: CallbackQuery, callback_data: DelGrade, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    grade = await ctx.repo.delete_grade(ctx.user, callback_data.id)
    if grade is None:
        callback_answer.text = "Эта оценка уже удалена"
        notice, top = None, []
    else:
        what = "ОКР " if grade.is_okr else ""
        notice = f"🗑 Удалена {what}оценка {grade.value} от {fmt_date(grade.created_at, ctx.user.tz)}."
        undo = UndoDelGrade(
            s=grade.subject_id, v=grade.value, o=grade.okr_no or 0, t=grade.created_at, p=callback_data.p
        )
        top = [[btn(f"↩️ Вернуть {grade.value}", undo)]]
        callback_answer.text = f"Удалил {grade.value}"
    screen = await screens.edit_grades_screen(ctx.repo, ctx.user, callback_data.s, callback_data.p, notice, top)
    if screen is None:
        await go_main(cq, ctx, notice=NOT_FOUND)
        return
    await ctx.mode(None, sid=callback_data.s)
    await ctx.show(cq, screen)


@router.callback_query(UndoDelGrade.filter())
async def undo_delete_grade(cq: CallbackQuery, callback_data: UndoDelGrade, ctx: Ctx) -> None:
    subject = await _subject(cq, ctx, callback_data.s)
    if subject is None:
        return
    value = callback_data.v
    notice = None
    if ctx.user.grade_min <= value <= ctx.user.grade_max:
        slot = callback_data.o if 0 < callback_data.o <= subject.okr_count else None
        await ctx.repo.restore_grade(ctx.user, subject.id, value, slot, callback_data.t)
        notice = f"↩️ Оценка {value} возвращена."
    screen = await screens.edit_grades_screen(ctx.repo, ctx.user, subject.id, callback_data.p, notice)
    if screen:
        await ctx.show(cq, screen)


# ---------------------------------------------------------------- настройки предмета


async def _show_settings(event: Event, ctx: Ctx, sid: int, notice: str | None = None) -> None:
    screen = await screens.subject_settings_screen(ctx.repo, ctx.user, sid, notice)
    if screen is None:
        await go_main(event, ctx, notice=NOT_FOUND)
        return
    await ctx.mode(None, sid=sid)
    await ctx.show(event, screen)


@router.callback_query(Subj.filter(F.a == "cfg"))
async def settings_view(cq: CallbackQuery, callback_data: Subj, ctx: Ctx) -> None:
    await _show_settings(cq, ctx, callback_data.id)


@router.callback_query(Subj.filter(F.a == "okrn"))
async def set_okr_count(cq: CallbackQuery, callback_data: Subj, ctx: Ctx) -> None:
    subject = await _subject(cq, ctx, callback_data.id)
    if subject is None:
        return
    count = min(max(callback_data.n, 0), 3)
    if count == subject.okr_count:
        await _show_settings(cq, ctx, subject.id)
        return
    moved = await ctx.repo.set_okr_count(ctx.user, subject.id, count)
    notice = f"📌 ОКР в предмете: {count if count else 'нет'}."
    if moved:
        notice += f"\nℹ️ {plural(moved, GRADE_FORMS)} за убранные ОКР теперь обычные."
    await _show_settings(cq, ctx, subject.id, notice)


@router.callback_query(Subj.filter(F.a.in_({"up", "down"})))
async def move_subject(cq: CallbackQuery, callback_data: Subj, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    moved = await ctx.repo.move_subject(ctx.user, callback_data.id, -1 if callback_data.a == "up" else 1)
    callback_answer.text = "Передвинул" if moved else "Дальше двигать некуда"
    await _show_settings(cq, ctx, callback_data.id)


RENAME_TITLE = "✏️ <b>Новое название</b>"


def _rename_body(subject: Subject) -> str:
    return (
        f"Сейчас: «{esc(display_name(subject.name))}».\n"
        "Отправь новое название. Можно с эмодзи в начале: <code>🧮 Матан</code>"
    )


@router.callback_query(Subj.filter(F.a == "ren"))
async def rename_prompt(cq: CallbackQuery, callback_data: Subj, ctx: Ctx) -> None:
    subject = await _subject(cq, ctx, callback_data.id)
    if subject is None:
        return
    await ctx.mode(Inp.rename, sid=subject.id)
    await ctx.show(cq, screens.prompt_screen(RENAME_TITLE, _rename_body(subject), Subj(a="cfg", id=subject.id)))


@router.message(Inp.rename, F.text)
async def rename_text(message: Message, ctx: Ctx) -> None:
    data = await ctx.data()
    subject = await ctx.repo.get_subject(ctx.user, data.get("sid", 0))
    if subject is None:
        await go_main(message, ctx, notice=NOT_FOUND)
        return
    name = " ".join((message.text or "").split())[:MAX_NAME_LEN].strip()
    others = await ctx.repo.list_subjects(ctx.user.id, ctx.user.semester_id)
    duplicate = any(s.id != subject.id and norm(display_name(s.name)) == norm(display_name(name)) for s in others)
    if not norm(display_name(name)) or duplicate:
        notice = "⚠️ Такой предмет уже есть." if duplicate else "⚠️ Название не может быть пустым."
        await ctx.show(
            message,
            screens.prompt_screen(RENAME_TITLE, _rename_body(subject), Subj(a="cfg", id=subject.id), notice),
        )
        return
    await ctx.repo.rename_subject(ctx.user, subject.id, name)
    await _show_settings(
        message, ctx, subject.id,
        f"✅ Переименовано: «{esc(display_name(subject.name))}» → «{esc(display_name(name))}».",
    )  # fmt: skip


@router.callback_query(Subj.filter(F.a == "del"))
async def delete_confirm(cq: CallbackQuery, callback_data: Subj, ctx: Ctx) -> None:
    screen = await screens.delete_subject_screen(ctx.repo, ctx.user, callback_data.id)
    if screen is None:
        await go_main(cq, ctx, notice=NOT_FOUND)
        return
    await ctx.mode()
    await ctx.show(cq, screen)


@router.callback_query(Subj.filter(F.a == "del_yes"))
async def delete_subject(cq: CallbackQuery, callback_data: Subj, ctx: Ctx) -> None:
    subject = await _subject(cq, ctx, callback_data.id)
    if subject is None:
        return
    await ctx.repo.delete_subject(ctx.user, subject.id)
    await go_main(cq, ctx, notice=f"🗑 Предмет «{esc(display_name(subject.name))}» удалён.")
