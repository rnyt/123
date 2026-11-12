"""Экспорт оценок в CSV, который открывается в Excel и Google Таблицах."""

from __future__ import annotations

import csv
import io

from . import clock
from .logic import Overview, fmt_date, fmt_num
from .models import CONTROL_LABEL, User
from .repo import Repo


def _final_text(v) -> str:
    final = v.final
    if final is None:
        return ""
    if final.kind == "grade":
        return str(final.value)
    if final.kind == "pass":
        return "зачтено"
    if final.kind == "debt":
        return f"задолженность ({final.note})"
    return "не аттестован"


async def build_csv(repo: Repo, user: User) -> tuple[bytes, str]:
    """Возвращает (содержимое файла, имя файла)."""
    views = await repo.views(user)
    semester = await repo.get_semester(user.id, user.semester_id)
    title = semester.label if semester else "семестр"
    ov = Overview(views)

    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow([f"Оценки — {title}", f"выгружено {fmt_date(clock.now(), user.tz, with_year=True)}"])
    writer.writerow([])
    writer.writerow(
        [
            "Предмет",
            "За семестр",
            "Средний",
            "Оценок",
            "ОКР",
            "Работы зачтены",
            "Курсовой",
            "Сессия",
            "Экзамен",
            "Итог",
            "Что не так",
        ]
    )
    for v in views:
        done = sum(w.passed for w in v.works)
        okr = ", ".join(str(g.value) if g else "—" for _, g in v.okr_slots)
        project = (
            (str(v.subject.project_grade) if v.subject.project_grade is not None else "не защищён")
            if v.subject.project
            else ""
        )
        exam = ""
        if v.control in ("exam", "diff") and v.subject.exam_grade is not None:
            exam = str(v.subject.exam_grade)
        elif v.control == "credit" and v.subject.credit is not None:
            exam = "зачтено" if v.subject.credit else "не зачтено"
        writer.writerow(
            [
                v.title,
                v.semester_grade if v.semester_grade is not None else "",
                fmt_num(v.semester_exact),
                len(v.grades),
                okr,
                f"{done} из {len(v.works)}" if v.works else "",
                project,
                CONTROL_LABEL[v.control] if v.has_session else "",
                exam,
                _final_text(v),
                "; ".join(v.reasons()),
            ]
        )
    writer.writerow(["Средний балл", fmt_num(ov.avg)])
    if ov.session_avg is not None:
        writer.writerow(["Средний балл сессии", fmt_num(ov.session_avg)])
    writer.writerow([])

    writer.writerow(["Предмет", "Что", "Оценка", "Статус", "Дата"])
    for v in views:
        works = {w.id: w for w in v.works}
        for g in v.grades:
            if g.is_okr:
                kind = v.okr_label(g.okr_no or 1)
            elif g.work_id in works:
                kind = f"Оценка за {works[g.work_id].full}"
            else:
                kind = "Оценка"
            writer.writerow([v.title, kind, g.value, "", fmt_date(g.created_at, user.tz, with_year=True)])
        for w in v.labs + v.pracs:
            status = "зачтено" if w.passed else "не зачтено" if w.failed else "не сдавал"
            when = fmt_date(w.defended_at, user.tz, with_year=True) if w.defended_at else ""
            writer.writerow([v.title, w.full, "", status, when])

    data = ("﻿" + out.getvalue()).encode("utf-8")  # BOM — чтобы Excel понял кириллицу
    safe_title = "".join(ch if ch.isalnum() or ch in " -_()" else "_" for ch in title).strip()
    return data, f"Оценки — {safe_title}.csv"
