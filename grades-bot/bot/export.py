"""Экспорт оценок в таблицу Excel (.xlsx): открывается в Excel, Google Таблицах и на телефоне."""

from __future__ import annotations

from datetime import datetime, timezone

from . import clock
from .logic import Overview, SubjectView, fmt_date
from .models import CONTROL_LABEL, User
from .repo import Repo
from .xlsx import Cell, Sheet, build

SUMMARY_HEADER = (
    "Предмет", "За семестр", "Средний", "Оценок", "ОКР", "Работы зачтены",
    "Курсовой", "Сессия", "Экзамен", "Итог", "Что не так",
)  # fmt: skip


def _final(v: SubjectView) -> Cell:
    final = v.final
    if final is None:
        return None
    if final.kind == "grade":
        return final.value
    if final.kind == "pass":
        return "зачтено"
    if final.kind == "debt":
        return f"задолженность ({final.note})"
    return "не аттестован"


def _okr(v: SubjectView) -> Cell:
    slots = v.okr_slots
    if not slots:
        return None
    if len(slots) == 1:
        grade = slots[0][1]
        return grade.value if grade else "—"
    return ", ".join(str(g.value) if g else "—" for _, g in slots)


def _project(v: SubjectView) -> Cell:
    if not v.subject.project:
        return None
    return v.subject.project_grade if v.subject.project_grade is not None else "не защищён"


def _exam(v: SubjectView) -> Cell:
    if v.control in ("exam", "diff") and v.subject.exam_grade is not None:
        return v.subject.exam_grade
    if v.control == "credit" and v.subject.credit is not None:
        return "зачтено" if v.subject.credit else "не зачтено"
    return None


async def build_xlsx(repo: Repo, user: User) -> tuple[bytes, str]:
    """Возвращает (содержимое файла, имя файла)."""
    views = await repo.views(user)
    semester = await repo.get_semester(user.id, user.semester_id)
    title = semester.label if semester else "семестр"
    ov = Overview(views)
    now = clock.now()

    summary = Sheet("Итоги", widths=[24, 11, 10, 8, 8, 15, 11, 11, 9, 18, 55])
    summary.add(f"Оценки — {title}", bold=True)
    summary.add(f"выгружено {fmt_date(now, user.tz, with_year=True)}")
    summary.add()
    summary.add(*SUMMARY_HEADER, bold=True)
    for v in views:
        summary.add(
            v.title,
            v.semester_grade,
            v.semester_exact,
            len(v.grades),
            _okr(v),
            f"{sum(w.passed for w in v.works)} из {len(v.works)}" if v.works else None,
            _project(v),
            CONTROL_LABEL[v.control] if v.has_session else None,
            _exam(v),
            _final(v),
            "; ".join(v.reasons()),
        )
    summary.add()
    if ov.avg is not None:
        marks = ov.marks
        summary.add(
            "Средний балл", ov.avg, None, None, None, None, None, None, None, None,
            f"= {sum(marks)} / {len(marks)}: среднее отметок по предметам, как в ведомости",
            bold=True,
        )  # fmt: skip
        summary.add(
            "Без округления", ov.avg_exact, None, None, None, None, None, None, None, None,
            "среднее точных значений за семестр (столбец «Средний»)",
        )  # fmt: skip
    if ov.session_avg is not None:
        summary.add("Средний балл сессии", ov.session_avg)

    grades = Sheet("Все оценки", widths=[24, 30, 8, 12, 12])
    grades.add("Предмет", "Что", "Оценка", "Статус", "Дата", bold=True)
    for v in views:
        works = {w.id: w for w in v.works}
        for g in v.grades:
            if g.is_okr:
                kind = v.okr_label(g.okr_no or 1)
            elif g.work_id in works:
                kind = f"Оценка за {works[g.work_id].full}"
            else:
                kind = "Оценка"
            grades.add(v.title, kind, g.value, None, fmt_date(g.created_at, user.tz, with_year=True))
        for w in v.labs + v.pracs:
            status = "зачтено" if w.passed else "не зачтено" if w.failed else "не сдавал"
            when = fmt_date(w.defended_at, user.tz, with_year=True) if w.defended_at else None
            grades.add(v.title, w.full, None, status, when)

    data = build([summary, grades], title=f"Оценки — {title}", created=datetime.fromtimestamp(now, timezone.utc))
    safe_title = "".join(ch if ch.isalnum() or ch in " -_()" else "_" for ch in title).strip()
    return data, f"Оценки — {safe_title}.xlsx"
