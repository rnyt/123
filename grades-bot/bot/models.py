"""Модели данных (простые dataclass-ы поверх строк SQLite)."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

LAB = "lab"
PRAC = "prac"
WORK_KINDS = (LAB, PRAC)

WORK_SHORT = {LAB: "ЛР", PRAC: "ПР"}
WORK_FULL = {LAB: "Лабораторная", PRAC: "Практическая"}
WORK_ICON = {LAB: "🔬", PRAC: "🛠"}

# Формы аттестации предмета в конце семестра.
NONE, EXAM, DIFF, CREDIT = "none", "exam", "diff", "credit"
CONTROLS = (NONE, EXAM, DIFF, CREDIT)
CONTROL_LABEL = {NONE: "без экзамена", EXAM: "экзамен", DIFF: "диф. зачёт", CREDIT: "зачёт"}
CONTROL_ICON = {NONE: "▫️", EXAM: "🎓", DIFF: "📝", CREDIT: "✔️"}

# Как считать итог предмета с экзаменом / диф. зачётом.
FINAL_AVG = "avg"  # (семестровая + экзамен) / 2 — так считают во многих колледжах
FINAL_EXAM = "exam"  # отметка за экзамен окончательная (п. 20 Правил № 282)
# Как считать отметку за семестр.
CALC_SSO = "sso"  # п. 22 Правил: ОКР, курсовой и среднее текущих — отдельными слагаемыми
CALC_MEAN = "mean"  # простое среднее всех отметок


class Row:
    """Сборка dataclass из строки SQLite: поля берутся по имени, флаги — в bool."""

    BOOL: frozenset[str] = frozenset()

    @classmethod
    def from_row(cls, row: Any) -> Any:
        keys = set(row.keys())
        values = {}
        for f in fields(cls):  # type: ignore[arg-type]
            value = row[f.name] if f.name in keys else None
            if f.name in cls.BOOL:
                value = None if value is None and f.name in getattr(cls, "NULLABLE_BOOL", ()) else bool(value)
            values[f.name] = value
        return cls(**values)


@dataclass(slots=True)
class User(Row):
    id: int
    name: str
    username: str | None
    created_at: int
    onboarded: bool
    scale: int
    pass_mark: int
    semester_id: int | None
    remind_every: int
    remind_time: str
    skip_sunday: bool
    works_reminders: bool
    tz: str
    clean_chat: bool
    panel_msg_id: int | None
    last_data_at: int | None
    last_reminded_at: int | None
    snooze_until: int | None
    blocked: bool
    final_mode: str
    calc_mode: str

    BOOL = frozenset({"onboarded", "skip_sunday", "works_reminders", "clean_chat", "blocked"})

    @property
    def grade_min(self) -> int:
        return 1

    @property
    def grade_max(self) -> int:
        return self.scale


@dataclass(slots=True)
class Semester(Row):
    id: int
    user_id: int
    title: str
    created_at: int
    archived_at: int | None
    course: int | None
    term: int | None
    session_start: str | None
    session_end: str | None
    holidays_start: str | None
    holidays_end: str | None
    archive_asked_at: int | None
    archive_ask: bool

    BOOL = frozenset({"archive_ask"})

    @property
    def label(self) -> str:
        if self.course and self.term:
            return f"{self.course} курс · {self.term} семестр"
        return self.title


@dataclass(slots=True)
class Subject(Row):
    id: int
    user_id: int
    semester_id: int
    name: str
    short: str | None
    okr_count: int
    position: int
    created_at: int
    control: str
    exam_grade: int | None
    credit: bool | None
    project: bool
    project_grade: int | None
    general: bool

    BOOL = frozenset({"credit", "project", "general"})
    NULLABLE_BOOL = frozenset({"credit"})


@dataclass(slots=True)
class Grade(Row):
    id: int
    subject_id: int
    value: int
    okr_no: int | None
    work_id: int | None
    created_at: int

    @property
    def is_okr(self) -> bool:
        return self.okr_no is not None

    @property
    def is_work(self) -> bool:
        return self.work_id is not None


@dataclass(slots=True)
class Work(Row):
    id: int
    subject_id: int
    kind: str
    num: int
    title: str | None
    defended_at: int | None
    failed: bool
    created_at: int

    BOOL = frozenset({"failed"})

    @property
    def passed(self) -> bool:
        """Зачтено."""
        return self.defended_at is not None

    @property
    def status_icon(self) -> str:
        return "✅" if self.passed else "❌" if self.failed else "⏳"

    @property
    def icon(self) -> str:
        return WORK_ICON.get(self.kind, "📄")

    @property
    def short(self) -> str:
        """Короткое имя для кнопок и списков: «ЛР 3» или своё название."""
        if self.title:
            return self.title if len(self.title) <= 18 else self.title[:17] + "…"
        return f"{WORK_SHORT.get(self.kind, 'Р')} {self.num}"

    @property
    def full(self) -> str:
        """Полное имя для заголовков: «Лабораторная №3» или своё название."""
        if self.title:
            return self.title
        return f"{WORK_FULL.get(self.kind, 'Работа')} №{self.num}"
