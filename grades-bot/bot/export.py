"""Экспорт оценок в CSV, который открывается в Excel и Google Таблицах."""

from __future__ import annotations

import csv
import io

from . import clock
from .logic import Overview, fmt_date, fmt_num
from .models import User
from .repo import Repo


async def build_csv(repo: Repo, user: User) -> tuple[bytes, str]:
    """Возвращает (содержимое файла, имя файла)."""
    views = await repo.views(user)
    semester = await repo.get_semester(user.id, user.semester_id)
    title = semester.title if semester else "семестр"
    ov = Overview(views)

    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow([f"Оценки — {title}", f"выгружено {fmt_date(clock.now(), user.tz, with_year=True)}"])
    writer.writerow([])
    writer.writerow(["Предмет", "Средний", "Оценок", "Работы защищены", "Аттестован", "Что не так"])
    for v in views:
        done = sum(w.defended for w in v.works)
        writer.writerow(
            [
                v.name,
                fmt_num(v.avg),
                len(v.grades),
                f"{done} из {len(v.works)}" if v.works else "—",
                "да" if v.ok else "нет",
                "; ".join(v.reasons()),
            ]
        )
    writer.writerow(["Средний балл", fmt_num(ov.avg)])
    writer.writerow([])

    writer.writerow(["Предмет", "Что", "Оценка", "Статус", "Дата"])
    for v in views:
        for g in v.grades:
            kind = v.okr_label(g.okr_no or 1) if g.is_okr else "Оценка"
            writer.writerow([v.name, kind, g.value, "", fmt_date(g.created_at, user.tz, with_year=True)])
        for w in v.labs + v.pracs:
            status = "защищена" if w.defended else "не защищена"
            when = fmt_date(w.defended_at, user.tz, with_year=True) if w.defended_at else ""
            writer.writerow([v.name, w.full, "", status, when])

    data = ("﻿" + out.getvalue()).encode("utf-8")  # BOM — чтобы Excel понял кириллицу
    safe_title = "".join(ch if ch.isalnum() or ch in " -_()" else "_" for ch in title).strip()
    return data, f"Оценки — {safe_title}.csv"
