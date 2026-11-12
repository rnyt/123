"""Модели данных (простые dataclass-ы поверх строк SQLite)."""

from __future__ import annotations

from dataclasses import dataclass

LAB = "lab"
PRAC = "prac"
WORK_KINDS = (LAB, PRAC)

WORK_SHORT = {LAB: "ЛР", PRAC: "ПР"}
WORK_FULL = {LAB: "Лабораторная", PRAC: "Практическая"}
WORK_ICON = {LAB: "🔬", PRAC: "🛠"}


@dataclass(slots=True)
class User:
    id: int
    name: str
    created_at: int
    onboarded: bool
    scale: int
    pass_mark: int
    semester_id: int | None
    remind_every: int
    remind_time: str
    skip_sunday: bool
    tz: str
    clean_chat: bool
    panel_msg_id: int | None
    last_data_at: int | None
    last_reminded_at: int | None
    snooze_until: int | None
    blocked: bool

    @property
    def grade_min(self) -> int:
        return 1

    @property
    def grade_max(self) -> int:
        return self.scale


@dataclass(slots=True)
class Semester:
    id: int
    user_id: int
    title: str
    created_at: int
    archived_at: int | None


@dataclass(slots=True)
class Subject:
    id: int
    user_id: int
    semester_id: int
    name: str
    okr_count: int
    position: int
    created_at: int


@dataclass(slots=True)
class Grade:
    id: int
    subject_id: int
    value: int
    okr_no: int | None
    created_at: int

    @property
    def is_okr(self) -> bool:
        return self.okr_no is not None


@dataclass(slots=True)
class Work:
    id: int
    subject_id: int
    kind: str
    num: int
    title: str | None
    defended_at: int | None
    created_at: int

    @property
    def defended(self) -> bool:
        return self.defended_at is not None

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
