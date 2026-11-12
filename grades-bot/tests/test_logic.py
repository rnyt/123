from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from bot.logic import (
    Overview,
    SubjectView,
    match_subjects,
    need_count,
    next_goal,
    parse_grades,
    parse_hhmm,
    parse_quick,
    parse_tz,
    plural,
    progress_bar,
    reminder_due,
    round_half_up,
    semester_title,
    split_icon,
    split_names,
    subject_icon,
)
from bot.models import LAB, PRAC, Grade, Subject, User, Work


def subj(sid: int, name: str, okr: int = 1) -> Subject:
    return Subject(sid, 1, 1, name, okr, sid, 0)


SUBJECTS = [
    subj(1, "Высшая математика"),
    subj(2, "Физика"),
    subj(3, "Основы алгоритмизации и программирования"),
    subj(4, "Английский язык"),
    subj(5, "Русский язык"),
    subj(6, "Математический анализ"),
    subj(7, "Физическая культура", okr=0),
]


def names(found: list[Subject]) -> list[str]:
    return [s.name for s in found]


# ---------------------------------------------------------------- форматирование


@pytest.mark.parametrize(
    ("n", "expected"),
    [(0, "0 оценок"), (1, "1 оценка"), (2, "2 оценки"), (5, "5 оценок"), (11, "11 оценок"), (21, "21 оценка"), (24, "24 оценки"), (112, "112 оценок")],
)  # fmt: skip
def test_plural(n: int, expected: str) -> None:
    assert plural(n, ("оценка", "оценки", "оценок")) == expected


def test_round_half_up_is_school_rounding() -> None:
    assert round_half_up(7.5) == 8
    assert round_half_up(8.5) == 9  # round() дал бы 8
    assert round_half_up(7.49) == 7


def test_progress_bar() -> None:
    assert progress_bar(0, 3) == "▱▱▱"
    assert progress_bar(3, 3) == "▰▰▰"
    assert progress_bar(1, 20) == "▰▱▱▱▱▱▱▱▱▱"  # хоть одна защищена — видно
    assert progress_bar(19, 20) == "▰▰▰▰▰▰▰▰▰▱"  # не все — не полный
    assert progress_bar(0, 0) == ""


# ---------------------------------------------------------------- разбор ввода


def test_parse_grades_any_separators() -> None:
    assert parse_grades("8, 9/10 7;6", 1, 10) == ([8, 9, 10, 7, 6], [])
    assert parse_grades("4/5", 1, 5) == ([4, 5], [])


def test_parse_grades_rejects_out_of_scale_and_fractions() -> None:
    values, bad = parse_grades("8 11 7.5 abc", 1, 10)
    assert values == [8]
    assert bad == ["11", "7.5", "abc"]


def test_split_names_handles_lists() -> None:
    text = "1. Высшая математика\n- Физика; Химия,  Английский   язык\n\n• История."
    assert split_names(text) == ["Высшая математика", "Физика", "Химия", "Английский язык", "История"]


@pytest.mark.parametrize(
    ("text", "query", "values", "okr"),
    [
        ("физ 8 9", "физ", [8, 9], False),
        ("Физика: 8,9", "Физика", [8, 9], False),
        ("8 9 физ", "физ", [8, 9], False),
        ("матан 9/8 10", "матан", [9, 8, 10], False),
        ("физ окр 7", "физ", [7], True),
        ("окр физ 7", "физ", [7], True),
        ("физика8", "физика", [8], False),
        ("8 9", "", [8, 9], False),
        ("физ", "физ", [], False),
    ],
)
def test_parse_quick(text: str, query: str, values: list[int], okr: bool) -> None:
    quick = parse_quick(text, 1, 10)
    assert (quick.query, quick.values, quick.okr, quick.bad) == (query, values, okr, [])


def test_parse_quick_prefers_full_subject_name_with_digits() -> None:
    quick = parse_quick("Физика 2 8 9", 1, 10, ["Физика", "Физика 2"])
    assert (quick.query, quick.values) == ("Физика 2", [8, 9])


def test_parse_quick_reports_bad_grades() -> None:
    quick = parse_quick("физ 8 11", 1, 10)
    assert quick.values == [8]
    assert quick.bad == ["11"]


# ---------------------------------------------------------------- поиск предмета


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("физика", ["Физика"]),
        ("физ", ["Физика", "Физическая культура"]),  # неоднозначно — бот спросит
        ("англ", ["Английский язык"]),
        ("язык", ["Английский язык", "Русский язык"]),
        ("оаип", ["Основы алгоритмизации и программирования"]),  # аббревиатура
        ("прог", ["Основы алгоритмизации и программирования"]),  # начало слова
        ("матан", ["Математический анализ"]),  # аббревиатура важнее сленга
        ("вышка", ["Высшая математика", "Математический анализ"]),  # сленг
        ("физра", ["Физическая культура"]),
        ("физикка", ["Физика"]),  # опечатка
        ("химия", []),
    ],
)
def test_match_subjects(query: str, expected: list[str]) -> None:
    assert names(match_subjects(query, SUBJECTS)) == expected


def test_match_subjects_slang_both_ways() -> None:
    own_slang = [subj(1, "Матан"), subj(2, "Физика")]
    assert names(match_subjects("вышка", own_slang)) == ["Матан"]


# ---------------------------------------------------------------- иконки


@pytest.mark.parametrize(
    ("name", "icon"),
    [
        ("Высшая математика", "📐"),
        ("Физика", "⚛️"),
        ("Физическая культура", "🏃"),
        ("Основы алгоритмизации и программирования", "💻"),
        ("Английский язык", "🇬🇧"),
        ("Русский язык", "📖"),
        ("Литература", "📖"),  # «ит» внутри слова — не IT
        ("Теория вероятностей и математическая статистика", "📊"),
        ("Основы права", "⚖️"),
        ("Что-то новое", "📘"),
        ("🧮 Матан", "🧮"),
    ],
)
def test_subject_icon(name: str, icon: str) -> None:
    assert subject_icon(name) == icon


def test_split_icon() -> None:
    assert split_icon("🧮 Матан") == ("🧮", "Матан")
    assert split_icon("Матан") == (None, "Матан")
    assert split_icon("C++ и ООП") == (None, "C++ и ООП")


# ---------------------------------------------------------------- цель «до следующей оценки»


def test_need_count() -> None:
    assert need_count([7, 7, 8], 7.5, 10) == 1
    assert need_count([6, 6, 7, 6], 6.5, 7) == 2
    assert need_count([9, 9], 8.5, 10) == 0
    assert need_count([5], 6.5, 6) is None


def test_next_goal_suggests_cheapest_options() -> None:
    goal = next_goal([6, 6, 7, 6], 10)  # 6,25 ≈ 6 → цель 7
    assert goal is not None
    assert goal.target == 7
    assert goal.options == [(8, 1), (7, 2)]  # одна «8» или две «7»


def test_next_goal_none_on_top() -> None:
    assert next_goal([10, 10, 9], 10) is None
    assert next_goal([], 10) is None


# ---------------------------------------------------------------- аттестация


def make_view(okr_count: int = 1, grades: list[Grade] | None = None, works: list[Work] | None = None) -> SubjectView:
    return SubjectView(subj(1, "Физика", okr_count), grades or [], works or [], pass_mark=4, scale=10)


def grade(value: int, okr: int | None = None, gid: int = 1) -> Grade:
    return Grade(gid, 1, value, okr, 0)


def work(num: int, defended: bool, kind: str = LAB) -> Work:
    return Work(num, 1, kind, num, None, 100 if defended else None, 0)


def test_not_certified_without_okr() -> None:
    view = make_view(grades=[grade(9)])
    assert not view.ok
    assert view.reasons() == ["нет оценки за ОКР"]


def test_not_certified_with_low_okr() -> None:
    view = make_view(grades=[grade(9), grade(3, okr=1, gid=2)])
    assert not view.ok
    assert view.reasons() == ["ОКР на 3 (нужно от 4)"]


def test_not_certified_with_pending_works() -> None:
    view = make_view(grades=[grade(7, okr=1)], works=[work(1, True), work(2, False), work(1, False, PRAC)])
    assert not view.ok
    assert view.reasons() == ["не защищены ЛР 2, ПР 1"]


def test_certified_when_all_done() -> None:
    view = make_view(grades=[grade(8), grade(6, okr=1, gid=2)], works=[work(1, True)])
    assert view.ok
    assert view.avg == 7  # ОКР входит в средний
    assert [g.value for g in view.regular] == [8]


def test_subject_without_okr_needs_only_works() -> None:
    assert make_view(okr_count=0).ok
    assert not make_view(okr_count=0, works=[work(1, False)]).ok


def test_multiple_okr_slots() -> None:
    view = make_view(okr_count=2, grades=[grade(7, okr=1)])
    assert view.reasons() == ["нет оценки за ОКР №2"]


def test_overview_average_of_averages() -> None:
    a = SubjectView(subj(1, "A"), [grade(10), grade(10), grade(10), grade(10)], [], 4, 10)
    b = SubjectView(subj(2, "B"), [grade(6)], [], 4, 10)
    empty = SubjectView(subj(3, "C"), [], [], 4, 10)
    ov = Overview([a, b, empty])
    assert ov.avg == 8  # (10 + 6) / 2 — у каждого предмета равный вес
    assert ov.avg_all == pytest.approx(9.2)
    assert ov.grades_count == 5


# ---------------------------------------------------------------- время и напоминания


def ts(y: int, m: int, d: int, hh: int, mm: int = 0, tz: str = "Europe/Minsk") -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(tz)).timestamp())


def make_user(**kw: object) -> User:
    base = dict(
        id=1, name="", created_at=ts(2026, 11, 1, 10), onboarded=True, scale=10, pass_mark=4, semester_id=1,
        remind_every=2, remind_time="20:00", skip_sunday=True, tz="Europe/Minsk", clean_chat=True,
        panel_msg_id=None, last_data_at=ts(2026, 11, 9, 18), last_reminded_at=None, snooze_until=None, blocked=False,
    )  # fmt: skip
    base.update(kw)
    return User(**base)  # type: ignore[arg-type]


def test_reminder_waits_for_time_of_day() -> None:
    user = make_user()  # данные вносили в понедельник 9.11
    assert reminder_due(user, ts(2026, 11, 11, 19, 59)) is None  # среда, ещё рано
    assert reminder_due(user, ts(2026, 11, 11, 20, 0)) == "regular"
    assert reminder_due(user, ts(2026, 11, 12, 1, 0)) is None  # ночью не пишем


def test_reminder_skips_when_recently_active() -> None:
    user = make_user(last_data_at=ts(2026, 11, 10, 12))  # вчера вносил
    assert reminder_due(user, ts(2026, 11, 11, 20, 5)) is None
    assert reminder_due(make_user(remind_every=1, last_data_at=ts(2026, 11, 10, 12)), ts(2026, 11, 11, 20, 5)) == "regular"


def test_reminder_not_twice_in_a_row() -> None:
    user = make_user(last_reminded_at=ts(2026, 11, 11, 20, 0))
    assert reminder_due(user, ts(2026, 11, 11, 21, 0)) is None
    assert reminder_due(user, ts(2026, 11, 12, 20, 0)) is None  # раз в 2 дня
    assert reminder_due(user, ts(2026, 11, 13, 20, 0)) == "regular"


def test_reminder_skips_sunday() -> None:
    user = make_user()
    sunday = ts(2026, 11, 15, 20, 0)
    assert reminder_due(user, sunday) is None
    assert reminder_due(make_user(skip_sunday=False), sunday) == "regular"


def test_reminder_off_and_blocked() -> None:
    assert reminder_due(make_user(remind_every=0), ts(2026, 11, 11, 20, 0)) is None
    assert reminder_due(make_user(blocked=True), ts(2026, 11, 11, 20, 0)) is None


def test_reminder_respects_timezone() -> None:
    user = make_user(tz="Asia/Novosibirsk")  # UTC+7
    assert reminder_due(user, ts(2026, 11, 11, 20, 0, tz="Asia/Novosibirsk")) == "regular"
    assert reminder_due(user, ts(2026, 11, 11, 20, 0, tz="Europe/Minsk")) is None  # там уже ночь


def test_snooze() -> None:
    until = ts(2026, 11, 11, 22, 0)
    user = make_user(snooze_until=until)
    assert reminder_due(user, until - 60) is None
    assert reminder_due(user, until) == "snooze"
    active = make_user(snooze_until=until, last_data_at=until - 600)
    assert reminder_due(active, until) == "clear_snooze"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("20:30", time(20, 30)), ("7.45", time(7, 45)), ("8", time(8, 0)), ("2130", time(21, 30)), ("25:00", None), ("abc", None)],
)  # fmt: skip
def test_parse_hhmm(text: str, expected: time | None) -> None:
    assert parse_hhmm(text) == expected


def test_parse_tz() -> None:
    assert parse_tz("Europe/Moscow")[0] == "Europe/Moscow"  # type: ignore[index]
    assert parse_tz("europe/moscow")[0] == "Europe/Moscow"  # type: ignore[index]
    assert parse_tz("+3")[0] == "UTC+03:00"  # type: ignore[index]
    assert parse_tz("UTC-05:30")[0] == "UTC-05:30"  # type: ignore[index]
    assert parse_tz("Марс/Олимп") is None
    assert parse_tz("+20") is None


def test_semester_title() -> None:
    assert semester_title(date(2026, 9, 1)) == "Осень 2026"
    assert semester_title(date(2027, 1, 15)) == "Осень 2026"
    assert semester_title(date(2027, 2, 9)) == "Весна 2027"
