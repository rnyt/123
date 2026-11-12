"""Лабораторные и практические: добавление, список, защита, переименование, удаление."""

from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer

from .. import screens
from ..callbacks import AddWorks, Subj, UndoWorks, WorkAct, WorkGrade
from ..context import Ctx
from ..logic import MAX_NAME_LEN, MAX_WORKS_PER_SUBJECT, WORK_FORMS, fmt_num, parse_grades, plural, split_names
from ..models import LAB, PRAC, WORK_KINDS, Subject, Work
from ..screens import btn, esc, works_range
from ..states import Inp
from .common import HAS_LETTERS, NOT_FOUND, Event, go_main, go_subject, handle_free_text, looks_like_grades

router = Router(name="works")


async def _show_works(event: Event, ctx: Ctx, sid: int, notice: str | None = None, top_rows=()) -> None:
    screen = await screens.works_screen(ctx.repo, ctx.user, sid, notice, top_rows)
    if screen is None:
        await go_main(event, ctx, notice=NOT_FOUND)
        return
    await ctx.mode(None, sid=sid)
    await ctx.show(event, screen)


async def _show_work(event: Event, ctx: Ctx, work: Work, notice: str | None = None) -> None:
    screen = await screens.work_screen(ctx.repo, ctx.user, work.id, notice)
    if screen is None:
        await go_main(event, ctx, notice=NOT_FOUND)
        return
    await ctx.mode(None, sid=work.subject_id)
    await ctx.show(event, screen)


@router.callback_query(Subj.filter(F.a == "works"))
async def works_view(cq: CallbackQuery, callback_data: Subj, ctx: Ctx) -> None:
    await _show_works(cq, ctx, callback_data.id)


# ---------------------------------------------------------------- добавление


@router.callback_query(Subj.filter(F.a.in_({"nwl", "nwp"})))
async def add_works_view(cq: CallbackQuery, callback_data: Subj, ctx: Ctx) -> None:
    kind = LAB if callback_data.a == "nwl" else PRAC
    from_list = bool(callback_data.n)
    screen = await screens.add_works_screen(ctx.repo, ctx.user, callback_data.id, kind, from_list)
    if screen is None:
        await go_main(cq, ctx, notice=NOT_FOUND)
        return
    await ctx.mode(Inp.works, sid=callback_data.id, kind=kind, back=int(from_list))
    await ctx.show(cq, screen)


async def _after_added(event: Event, ctx: Ctx, subject: Subject, works: list[Work], back: bool) -> None:
    if not works:
        notice = f"⚠️ В предмете уже {MAX_WORKS_PER_SUBJECT} работ — это максимум."
        top: list = []
    else:
        notice = f"✅ Добавлено: {works_range(works)}"
        top = [[btn("↩️ Отменить", UndoWorks(s=subject.id, a=works[0].id, b=works[-1].id))]]
    if back:
        await _show_works(event, ctx, subject.id, notice, top)
    else:
        await go_subject(event, ctx, subject.id, notice, top)


@router.callback_query(AddWorks.filter())
async def add_works_count(cq: CallbackQuery, callback_data: AddWorks, ctx: Ctx) -> None:
    subject = await ctx.repo.get_subject(ctx.user, callback_data.s)
    if subject is None:
        await go_main(cq, ctx, notice=NOT_FOUND)
        return
    if callback_data.k not in WORK_KINDS or not 1 <= callback_data.c <= 30:
        await go_subject(cq, ctx, subject.id)
        return
    works = await ctx.repo.add_works(ctx.user, subject.id, callback_data.k, [None] * callback_data.c)
    await _after_added(cq, ctx, subject, works, bool(callback_data.b))


@router.message(Inp.works, F.text)
async def add_works_text(message: Message, ctx: Ctx) -> None:
    data = await ctx.data()
    subject = await ctx.repo.get_subject(ctx.user, data.get("sid", 0))
    kind = data.get("kind")
    if subject is None or kind not in WORK_KINDS:
        await go_main(message, ctx, notice=NOT_FOUND)
        return
    text = (message.text or "").strip()
    if await looks_like_grades(ctx, text):
        await ctx.mode(None, sid=subject.id)
        await handle_free_text(message, ctx)
        return
    if re.fullmatch(r"\d{1,2}", text):
        count = int(text)
        if not 1 <= count <= 30:
            screen = await screens.add_works_screen(
                ctx.repo, ctx.user, subject.id, kind, bool(data.get("back")),
                notice="⚠️ За раз можно добавить от 1 до 30 работ.",
            )  # fmt: skip
            if screen:
                await ctx.show(message, screen)
            return
        titles: list[str | None] = [None] * count
    else:
        titles = [t[:MAX_NAME_LEN] for t in split_names(text)]
    works = await ctx.repo.add_works(ctx.user, subject.id, kind, titles) if titles else []
    if not titles:
        screen = await screens.add_works_screen(
            ctx.repo, ctx.user, subject.id, kind, bool(data.get("back")),
            notice="⚠️ Отправь число (сколько работ) или названия через запятую.",
        )  # fmt: skip
        if screen:
            await ctx.show(message, screen)
        return
    await _after_added(message, ctx, subject, works, bool(data.get("back")))


@router.callback_query(UndoWorks.filter())
async def undo_works(cq: CallbackQuery, callback_data: UndoWorks, ctx: Ctx) -> None:
    if not 0 <= callback_data.b - callback_data.a < MAX_WORKS_PER_SUBJECT:
        await go_subject(cq, ctx, callback_data.s)
        return
    ids = list(range(callback_data.a, callback_data.b + 1))
    removed = await ctx.repo.delete_works(ctx.user, callback_data.s, ids)
    notice = f"↩️ Отменено: убрал {plural(removed, WORK_FORMS)}." if removed else "↩️ Уже отменено."
    await go_subject(cq, ctx, callback_data.s, notice)


# ---------------------------------------------------------------- одна работа


async def _work(event: Event, ctx: Ctx, wid: int) -> Work | None:
    work = await ctx.repo.get_work(ctx.user, wid)
    if work is None:
        data = await ctx.data()
        if data.get("sid"):
            await _show_works(event, ctx, int(data["sid"]), notice="⚠️ Эта работа уже удалена.")
        else:
            await go_main(event, ctx, notice="⚠️ Эта работа уже удалена.")
    return work


@router.callback_query(WorkAct.filter(F.a == "v"))
async def work_view(cq: CallbackQuery, callback_data: WorkAct, ctx: Ctx) -> None:
    work = await _work(cq, ctx, callback_data.id)
    if work:
        await _show_work(cq, ctx, work)


async def _grade_screen(event: Event, ctx: Ctx, work: Work, notice: str | None = None) -> None:
    screen = await screens.work_grade_screen(ctx.repo, ctx.user, work.id, notice)
    if screen is None:
        await go_main(event, ctx, notice=NOT_FOUND)
        return
    await ctx.mode(Inp.wgrade, sid=work.subject_id, wid=work.id)
    await ctx.show(event, screen)


@router.callback_query(WorkAct.filter(F.a == "pass"))
async def work_pass(cq: CallbackQuery, callback_data: WorkAct, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    """«Зачтено» — и сразу предложить поставить оценку (её иногда ставят, особенно за практические)."""
    work = await _work(cq, ctx, callback_data.id)
    if work is None:
        return
    await ctx.repo.set_work_status(ctx.user, work.id, "passed")
    view = await ctx.repo.view(ctx.user, work.subject_id)
    callback_answer.text = f"🎉 {work.short} — зачтено!"
    lines = [f"🎉 <b>{esc(work.full)}</b> — зачтено!"]
    if view and not view.open_works:
        lines.append("🏆 Все работы по предмету зачтены!")
    await _grade_screen(cq, ctx, work, "\n".join(lines))


@router.callback_query(WorkAct.filter(F.a == "fail"))
async def work_fail(cq: CallbackQuery, callback_data: WorkAct, ctx: Ctx) -> None:
    work = await _work(cq, ctx, callback_data.id)
    if work is None:
        return
    await ctx.repo.set_work_status(ctx.user, work.id, "failed")
    await _show_works(
        cq, ctx, work.subject_id, f"❌ <b>{esc(work.full)}</b> — не зачтено. Пересдашь — отметь «Зачтено»."
    )


@router.callback_query(WorkAct.filter(F.a == "unpass"))
async def work_reset(cq: CallbackQuery, callback_data: WorkAct, ctx: Ctx) -> None:
    work = await _work(cq, ctx, callback_data.id)
    if work is None:
        return
    await ctx.repo.set_work_status(ctx.user, work.id, "pending")
    work = await ctx.repo.get_work(ctx.user, work.id) or work
    await _show_work(cq, ctx, work, "↩️ Отметка снята — работа снова «ещё не сдавал».")


@router.callback_query(WorkAct.filter(F.a == "grade"))
async def work_grade_view(cq: CallbackQuery, callback_data: WorkAct, ctx: Ctx) -> None:
    work = await _work(cq, ctx, callback_data.id)
    if work:
        await _grade_screen(cq, ctx, work)


async def _set_work_grade(event: Event, ctx: Ctx, work: Work, value: int | None) -> None:
    before = await ctx.repo.view(ctx.user, work.subject_id)
    await ctx.repo.set_work_grade(ctx.user, work.id, value)
    after = await ctx.repo.view(ctx.user, work.subject_id)
    if value is None:
        notice = f"🗑 Оценка за «{esc(work.full)}» убрана."
    else:
        notice = f"🔢 <b>{esc(work.short)}</b>: оценка {value}"
    if before and after and fmt_num(before.semester_exact) != fmt_num(after.semester_exact):
        notice += f" · за семестр {fmt_num(before.semester_exact)} → <b>{fmt_num(after.semester_exact)}</b>"
    await _show_works(event, ctx, work.subject_id, notice)


@router.callback_query(WorkGrade.filter())
async def work_grade_set(
    cq: CallbackQuery, callback_data: WorkGrade, ctx: Ctx, callback_answer: CallbackAnswer
) -> None:
    work = await _work(cq, ctx, callback_data.w)
    if work is None:
        return
    if callback_data.v and not ctx.user.grade_min <= callback_data.v <= ctx.user.grade_max:
        callback_answer.text = "Такой оценки нет в твоей шкале"
        return
    await _set_work_grade(cq, ctx, work, callback_data.v or None)


@router.message(Inp.wgrade, F.text)
async def work_grade_text(message: Message, ctx: Ctx) -> None:
    data = await ctx.data()
    work = await _work(message, ctx, int(data.get("wid") or 0))
    if work is None:
        return
    text = message.text or ""
    if HAS_LETTERS.search(text):
        await ctx.mode(None, sid=work.subject_id)
        await handle_free_text(message, ctx)
        return
    values, bad = parse_grades(text, ctx.user.grade_min, ctx.user.grade_max)
    if bad or len(values) != 1:
        await _grade_screen(
            message, ctx, work, f"⚠️ Отправь одну оценку от {ctx.user.grade_min} до {ctx.user.grade_max}."
        )
        return
    await _set_work_grade(message, ctx, work, values[0])


WORK_RENAME_TITLE = "✏️ <b>Название работы</b>"


def _work_rename_body(work: Work) -> str:
    return (
        f"Сейчас: «{esc(work.full)}».\nОтправь новое название. Чтобы вернуть обычное («ЛР 3»), отправь <code>-</code>"
    )


@router.callback_query(WorkAct.filter(F.a == "ren"))
async def work_rename_prompt(cq: CallbackQuery, callback_data: WorkAct, ctx: Ctx) -> None:
    work = await _work(cq, ctx, callback_data.id)
    if work is None:
        return
    await ctx.mode(Inp.wrename, sid=work.subject_id, wid=work.id)
    await ctx.show(cq, screens.prompt_screen(WORK_RENAME_TITLE, _work_rename_body(work), WorkAct(a="v", id=work.id)))


@router.message(Inp.wrename, F.text)
async def work_rename_text(message: Message, ctx: Ctx) -> None:
    data = await ctx.data()
    work = await _work(message, ctx, int(data.get("wid") or 0))
    if work is None:
        return
    title = " ".join((message.text or "").split())[:MAX_NAME_LEN].strip()
    new_title = None if title in {"", "-", "—", "–"} else title
    await ctx.repo.rename_work(ctx.user, work.id, new_title)
    work.title = new_title
    await _show_work(message, ctx, work, f"✅ Теперь работа называется «{esc(work.full)}».")


@router.callback_query(WorkAct.filter(F.a == "del"))
async def work_delete_confirm(cq: CallbackQuery, callback_data: WorkAct, ctx: Ctx) -> None:
    work = await _work(cq, ctx, callback_data.id)
    if work is None:
        return
    screen = await screens.delete_work_screen(ctx.repo, ctx.user, work.id)
    if screen:
        await ctx.mode(None, sid=work.subject_id)
        await ctx.show(cq, screen)


@router.callback_query(WorkAct.filter(F.a == "del_yes"))
async def work_delete(cq: CallbackQuery, callback_data: WorkAct, ctx: Ctx) -> None:
    work = await _work(cq, ctx, callback_data.id)
    if work is None:
        return
    await ctx.repo.delete_works(ctx.user, work.subject_id, [work.id])
    await _show_works(cq, ctx, work.subject_id, f"🗑 «{esc(work.full)}» удалена.")
