"""Даты сессии и каникул: разбор «22.12–14.01» / «22 декабря – 14 января» и фаза семестра."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7,
    "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}  # fmt: skip

_NUMERIC = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?!\d)")
_WORDS = re.compile(r"(?<!\d)(\d{1,2})\s+([а-я]+)(?:\s+(\d{4}))?")
MAX_RANGE_DAYS = 150


def _month(word: str) -> int | None:
    for stem, number in MONTHS.items():
        if word.startswith(stem):
            return number
    return None


def _mentions(text: str) -> list[tuple[int, int, int, int | None]]:
    """Все даты в тексте по порядку: (позиция, день, месяц, год|None)."""
    found: list[tuple[int, int, int, int | None]] = []
    for m in _NUMERIC.finditer(text):
        year = int(m.group(3)) if m.group(3) else None
        if year is not None and year < 100:
            year += 2000
        found.append((m.start(), int(m.group(1)), int(m.group(2)), year))
    for m in _WORDS.finditer(text):
        month = _month(m.group(2))
        if month:
            found.append((m.start(), int(m.group(1)), month, int(m.group(3)) if m.group(3) else None))
    return sorted(found)


def _make(day: int, month: int, year: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _closest(day: int, month: int, today: date) -> date | None:
    options = [d for y in (today.year - 1, today.year, today.year + 1) if (d := _make(day, month, y))]
    return min(options, key=lambda d: abs((d - today).days)) if options else None


def parse_range(text: str, today: date) -> tuple[date, date] | None:
    """«22.12–14.01» -> (22.12.2026, 14.01.2027). Год, если не указан, — ближайший к сегодня."""
    mentions = _mentions(text.lower().replace("ё", "е"))
    if len(mentions) != 2:
        return None
    (_, d1, m1, y1), (_, d2, m2, y2) = mentions
    start = _make(d1, m1, y1) if y1 else _closest(d1, m1, today)
    if start is None:
        return None
    if y2:
        end = _make(d2, m2, y2)
    else:
        end = _make(d2, m2, start.year)
        if end is not None and end < start:
            end = _make(d2, m2, start.year + 1)
    if end is None or end < start or (end - start).days > MAX_RANGE_DAYS:
        return None
    return start, end


def to_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def fmt_day(value: date | str | None, with_year: bool = False) -> str:
    day = to_date(value) if isinstance(value, str) or value is None else value
    if day is None:
        return "—"
    return day.strftime("%d.%m.%Y" if with_year else "%d.%m")


def fmt_range(start: str | None, end: str | None) -> str:
    if not start or not end:
        return "не указаны"
    return f"{fmt_day(start)}–{fmt_day(end)}"


@dataclass(slots=True)
class Phase:
    """Где мы в семестре: до сессии, сессия, каникулы, после."""

    kind: str  # "unknown" | "study" | "session" | "after" | "holidays"
    days: int = 0  # сколько дней до начала (study) или до конца (session, holidays)
    until: date | None = None


def phase(
    today: date,
    session_start: str | None,
    session_end: str | None,
    holidays_start: str | None = None,
    holidays_end: str | None = None,
) -> Phase:
    s_start, s_end = to_date(session_start), to_date(session_end)
    h_start, h_end = to_date(holidays_start), to_date(holidays_end)
    if h_start and h_end and h_start <= today <= h_end:
        return Phase("holidays", (h_end - today).days, h_end)
    if not s_start or not s_end:
        return Phase("unknown")
    if today < s_start:
        return Phase("study", (s_start - today).days, s_start)
    if today <= s_end:
        return Phase("session", (s_end - today).days, s_end)
    return Phase("after", (today - s_end).days, s_end)


def in_range(today: date, start: str | None, end: str | None) -> bool:
    a, b = to_date(start), to_date(end)
    return bool(a and b and a <= today <= b)


def next_term(course: int | None, term: int | None) -> tuple[int, int]:
    """После 1-го семестра — 2-й того же курса, после 2-го — 1-й следующего."""
    course = course or 1
    if (term or 1) == 1:
        return course, 2
    return min(course + 1, 5), 1


def term_for(day: date) -> int:
    """Какой семестр по календарю: сентябрь–январь — 1-й, февраль–август — 2-й."""
    return 1 if day.month >= 8 or day.month == 1 else 2
