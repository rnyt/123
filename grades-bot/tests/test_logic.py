from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from bot.dates import next_term, parse_range, phase, term_for
from bot.logic import (
    Overview,
    SubjectView,
    fmt_num,
    match_subjects,
    need_count,
    next_goal,
    parse_grades,
    parse_hhmm,
    parse_marks,
    parse_quick,
    parse_subject_entry,
    parse_tz,
    plural,
    progress_bar,
    reminder_due,
    round_half_up,
    semester_title,
    split_digits,
    split_icon,
    split_names,
    split_segments,
    subject_icon,
)
from bot.models import CALC_MEAN, CREDIT, DIFF, EXAM, FINAL_EXAM, LAB, NONE, PRAC, Grade, Subject, User, Work


def subj(sid: int, name: str, okr: int = 1, short: str | None = None, **kw) -> Subject:
    fields = dict(
        id=sid, user_id=1, semester_id=1, name=name, short=short, okr_count=okr, position=sid, created_at=0,
        control=NONE, exam_grade=None, credit=None, project=False, project_grade=None, general=False,
    )  # fmt: skip
    fields.update(kw)
    return Subject(**fields)


SUBJECTS = [
    subj(1, "Высшая математика"),
    subj(2, "Физика"),
    subj(3, "Основы алгоритмизации и программирования"),
    subj(4, "Английский язык"),
    subj(5, "Русский язык"),
    subj(6, "Математический анализ"),
    subj(7, "Физическая культура", okr=0),
    subj(8, "Теоретические основы электротехники", short="ТОЭ"),
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
    assert round_half_up(8.5) == 9  # round() дал бы 8 — а по правилам ССО (п. 8) нужно 9
    assert round_half_up(6.7) == 7
    assert round_half_up(7.49) == 7


def test_progress_bar() -> None:
    assert progress_bar(0, 3) == "▱▱▱"
    assert progress_bar(3, 3) == "▰▰▰"
    assert progress_bar(1, 20) == "▰▱▱▱▱▱▱▱▱▱"
    assert progress_bar(19, 20) == "▰▰▰▰▰▰▰▰▰▱"
    assert progress_bar(0, 0) == ""


# ---------------------------------------------------------------- разбор ввода


def test_parse_grades_any_separators() -> None:
    assert parse_grades("8, 9/10 7;6", 1, 10) == ([8, 9, 10, 7, 6], [])
    assert parse_grades("4/5", 1, 5) == ([4, 5], [])


@pytest.mark.parametrize(
    ("text", "values"),
    [("1078105", [10, 7, 8, 10, 5]), ("10910", [10, 9, 10]), ("99910", [9, 9, 9, 10]), ("101010", [10, 10, 10]), ("10", [10])],
)  # fmt: skip
def test_glued_digits_are_split(text: str, values: list[int]) -> None:
    assert parse_grades(text, 1, 10) == (values, [])


def test_glued_digits_on_five_point_scale() -> None:
    assert parse_grades("4545", 1, 5) == ([4, 5, 4, 5], [])
    assert parse_grades("10", 1, 5) == ([], ["10"])


def test_split_digits_rejects_zero() -> None:
    assert split_digits("100", 10) is None
    assert split_digits("1078105", 10) == [10, 7, 8, 10, 5]


def test_parse_grades_rejects_garbage() -> None:
    values, bad = parse_grades("8 100 7.5 abc", 1, 10)
    assert values == [8]
    assert bad == ["100", "7.5", "abc"]


def test_split_names_handles_lists() -> None:
    text = "1. Высшая математика\n- Физика; Химия,  Английский   язык\n\n• История."
    assert split_names(text) == ["Высшая математика", "Физика", "Химия", "Английский язык", "История"]


def test_subject_entry_with_abbreviation() -> None:
    assert parse_subject_entry("Теоретические основы электротехники (ТОЭ)") == (
        "Теоретические основы электротехники",
        "ТОЭ",
    )
    assert parse_subject_entry("Английский язык (B2)") == ("Английский язык (B2)", None)  # уровень — не сокращение
    assert parse_subject_entry("Физика") == ("Физика", None)


@pytest.mark.parametrize(
    ("text", "query", "values", "okr"),
    [
        ("физ 8 9", "физ", [8, 9], False),
        ("Физика: 8,9", "Физика", [8, 9], False),
        ("8 9 физ", "физ", [8, 9], False),
        ("матан 9/8 10", "матан", [9, 8, 10], False),
        ("физра 10910", "физра", [10, 9, 10], False),
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
    quick = parse_quick("физ 8 100", 1, 10)
    assert quick.values == [8]
    assert quick.bad == ["100"]


def test_split_segments() -> None:
    assert split_segments("физра 10910\nохрана 99910\nфизика 101010") == [
        "физра 10910",
        "охрана 99910",
        "физика 101010",
    ]
    assert split_segments("физ 8 9, матан 7") == ["физ 8 9", "матан 7"]
    assert split_segments("Физика: 8, 9") == ["Физика: 8, 9"]


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
        ("тоэ", ["Теоретические основы электротехники"]),  # своё сокращение
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
        ("Охрана труда", "🦺"),
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


# ---------------------------------------------------------------- отметка за семестр, допуск, итог


def grade(value: int, okr: int | None = None, gid: int = 1, work: int | None = None) -> Grade:
    return Grade(id=gid, subject_id=1, value=value, okr_no=okr, work_id=work, created_at=0)


def work(num: int, passed: bool, kind: str = LAB, failed: bool = False) -> Work:
    return Work(id=num + (100 if kind == PRAC else 0), subject_id=1, kind=kind, num=num, title=None,
                defended_at=100 if passed else None, failed=failed, created_at=0)  # fmt: skip


def make_view(grades=(), works=(), pass_mark: int = 3, calc=None, final=None, **subject_kw) -> SubjectView:
    kw = {}
    if calc:
        kw["calc_mode"] = calc
    if final:
        kw["final_mode"] = final
    return SubjectView(subj(1, "Физика", **subject_kw), list(grades), list(works), pass_mark, 10, **kw)


def test_semester_grade_by_rule_22() -> None:
    """п. 22: среднее из ОКР, курсового и среднего остальных отметок."""
    view = make_view([grade(6), grade(8), grade(7), grade(9, okr=1, gid=2)], project=True, project_grade=8)
    assert view.current_avg == 7
    assert view.semester_exact == pytest.approx(8.0)  # (9 + 8 + 7) / 3
    assert view.semester_grade == 8
    assert view.formula == "(ОКР 9 + курсовой 8 + текущие 7) / 3 = 8,00"


def test_semester_grade_simple_mean() -> None:
    view = make_view([grade(6), grade(8), grade(7), grade(9, okr=1, gid=2)], calc=CALC_MEAN)
    assert view.semester_exact == 7.5
    assert view.formula is None
    general = make_view([grade(6), grade(8), grade(7), grade(9, okr=1, gid=2)], general=True)
    assert general.semester_exact == 7.5  # общеобразовательный — тоже простое среднее


def test_work_grades_count_in_current() -> None:
    view = make_view([grade(6), grade(10, work=1, gid=2)], works=[work(1, True)], okr=0)
    assert view.current == [6, 10]
    assert view.semester_exact == 8
    assert view.work_grades[1].value == 10


def test_not_certified_without_okr() -> None:
    view = make_view([grade(9)])
    assert not view.ok
    assert view.reasons() == ["нет оценки за ОКР"]


def test_okr_below_three_is_a_problem() -> None:
    assert make_view([grade(9), grade(2, okr=1, gid=2)]).reasons() == ["ОКР на 2 (нужно от 3)"]
    assert make_view([grade(9), grade(3, okr=1, gid=2)]).ok  # в ССО 3 — уже положительная (п. 4)


def test_not_certified_with_open_works() -> None:
    view = make_view([grade(7, okr=1)], works=[work(1, True), work(2, False), work(1, False, PRAC, failed=True)])
    assert not view.ok
    assert view.reasons() == ["не зачтены ЛР 2, ПР 1"]


def test_project_must_be_defended() -> None:
    assert make_view([grade(8, okr=1)], project=True).reasons() == ["курсовой проект не защищён"]
    assert make_view([grade(8, okr=1)], project=True, project_grade=2).reasons() == ["курсовой на 2 (нужно от 3)"]


def test_exam_admission_and_average_final() -> None:
    view = make_view([grade(7), grade(7, okr=1, gid=2)], control=EXAM)
    assert view.admitted and view.final is None
    # 3→5; 4–5→6 (5,5 и 6); 6–7→7; 8–9→8; 10→9 (8,5)
    assert view.exam_table() == [(3, 3, 5), (4, 5, 6), (6, 7, 7), (8, 9, 8), (10, 10, 9)]
    done = make_view([grade(7), grade(7, okr=1, gid=2)], control=EXAM, exam_grade=10)
    assert done.final.kind == "grade" and done.final.value == 9  # (7 + 10) / 2 = 8,5 → 9
    assert done.status_icon == "🏁"


def test_exam_only_final_mode() -> None:
    view = make_view([grade(7), grade(7, okr=1, gid=2)], control=EXAM, exam_grade=10, final=FINAL_EXAM)
    assert view.final.value == 10
    assert view.exam_table() == []


def test_no_admission_without_works_or_semester_grade() -> None:
    assert not make_view([grade(7, okr=1)], works=[work(1, False)], control=DIFF).admitted
    assert not make_view([], okr=0, control=EXAM).admitted  # нет отметок за семестр


def test_failed_exam_is_debt() -> None:
    view = make_view([grade(8), grade(8, okr=1, gid=2)], control=EXAM, exam_grade=2)
    assert view.final.kind == "debt"
    assert view.reasons() == ["экзамен на 2 — нужна пересдача"]
    assert view.status_icon == "❌"


def test_credit() -> None:
    assert make_view([grade(8, okr=1)], control=CREDIT, credit=True).final.kind == "pass"
    failed = make_view([grade(8, okr=1)], control=CREDIT, credit=False)
    assert failed.final.kind == "debt" and failed.reasons() == ["зачёт не сдан — нужна пересдача"]


def test_exemption_from_exam_for_nine_plus() -> None:
    assert make_view([grade(9), grade(9, okr=1, gid=2)], control=EXAM).can_exempt
    assert not make_view([grade(8), grade(8, okr=1, gid=2)], control=EXAM).can_exempt
    assert not make_view([grade(9), grade(9, okr=1, gid=2)], control=DIFF).can_exempt


def test_subject_without_okr_needs_only_works() -> None:
    assert make_view(okr=0).ok
    assert not make_view(okr=0, works=[work(1, False)]).ok


def test_no_exam_subject_is_never_finished_mid_semester() -> None:
    assert make_view([grade(8)], okr=0).status_icon == "✅"  # не 🏁: семестр ещё идёт


def test_multiple_okr_slots() -> None:
    view = make_view([grade(7, okr=1)], okr=2)
    assert view.reasons() == ["нет оценки за ОКР №2"]


def test_overview_average_and_session() -> None:
    a = SubjectView(subj(1, "A", okr=0, control=EXAM, exam_grade=9), [grade(10), grade(10)], [], 3, 10)
    b = SubjectView(subj(2, "B", okr=0, control=DIFF, exam_grade=7), [grade(6)], [], 3, 10)
    c = SubjectView(subj(3, "C", okr=0, control=CREDIT, credit=True), [], [], 3, 10)
    empty = SubjectView(subj(4, "D", okr=0), [], [], 3, 10)
    ov = Overview([a, b, c, empty])
    assert ov.marks == [10, 7]  # итоговые отметки: (10+9)/2=9,5→10; (6+7)/2=6,5→7
    assert ov.avg == 8.5  # (10 + 7) / 2 — у каждого предмета равный вес
    assert ov.avg_exact == 8  # без округления: (10 + 6) / 2
    assert ov.session_avg == 8  # (9 + 7) / 2
    assert ov.session_complete
    assert [v.final.value for v in (a, b)] == [10, 7]  # (10+9)/2=9,5→10; (6+7)/2=6,5→7


# ---------------------------------------------------------------- даты сессии


TODAY = date(2026, 11, 12)


def test_parse_range_numeric_and_words() -> None:
    assert parse_range("22.12–14.01", TODAY) == (date(2026, 12, 22), date(2027, 1, 14))
    assert parse_range("с 22 декабря по 14 января", TODAY) == (date(2026, 12, 22), date(2027, 1, 14))
    assert parse_range("15.01.2027 - 08.02.2027", TODAY) == (date(2027, 1, 15), date(2027, 2, 8))
    assert parse_range("22.12", TODAY) is None
    assert parse_range("14.01–22.12", TODAY) is None  # слишком длинно и наоборот


def test_phase() -> None:
    assert phase(TODAY, "2026-12-22", "2027-01-14").kind == "study"
    assert phase(TODAY, "2026-12-22", "2027-01-14").days == 40
    assert phase(date(2027, 1, 5), "2026-12-22", "2027-01-14").kind == "session"
    assert phase(date(2027, 1, 20), "2026-12-22", "2027-01-14", "2027-01-15", "2027-02-08").kind == "holidays"
    assert phase(date(2027, 2, 20), "2026-12-22", "2027-01-14").kind == "after"
    assert phase(TODAY, None, None).kind == "unknown"


def test_terms() -> None:
    assert next_term(1, 1) == (1, 2)
    assert next_term(1, 2) == (2, 1)
    assert term_for(date(2026, 11, 1)) == 1
    assert term_for(date(2027, 3, 1)) == 2


# ---------------------------------------------------------------- время и напоминания


def ts(y: int, m: int, d: int, hh: int, mm: int = 0, tz: str = "Europe/Minsk") -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(tz)).timestamp())


def make_user(**kw: object) -> User:
    base = dict(
        id=1, name="", username=None, created_at=ts(2026, 11, 1, 10), onboarded=True, scale=10, pass_mark=3,
        semester_id=1, remind_every=2, remind_time="20:00", skip_sunday=True, works_reminders=True,
        tz="Europe/Minsk", clean_chat=True, panel_msg_id=None, last_data_at=ts(2026, 11, 9, 18),
        last_reminded_at=None, snooze_until=None, blocked=False, final_mode="avg", calc_mode="sso",
    )  # fmt: skip
    base.update(kw)
    return User(**base)  # type: ignore[arg-type]


def test_reminder_waits_for_time_of_day() -> None:
    user = make_user()
    assert reminder_due(user, ts(2026, 11, 11, 19, 59)) is None
    assert reminder_due(user, ts(2026, 11, 11, 20, 0)) == "regular"
    assert reminder_due(user, ts(2026, 11, 12, 1, 0)) is None  # ночью не пишем


def test_reminder_skips_when_recently_active() -> None:
    user = make_user(last_data_at=ts(2026, 11, 10, 12))
    assert reminder_due(user, ts(2026, 11, 11, 20, 5)) is None
    assert (
        reminder_due(make_user(remind_every=1, last_data_at=ts(2026, 11, 10, 12)), ts(2026, 11, 11, 20, 5)) == "regular"
    )


def test_reminder_about_works_even_when_active() -> None:
    user = make_user(last_data_at=ts(2026, 11, 10, 12))
    assert reminder_due(user, ts(2026, 11, 11, 20, 5), works_due=True) == "works"
    off = make_user(last_data_at=ts(2026, 11, 10, 12), works_reminders=False)
    assert reminder_due(off, ts(2026, 11, 11, 20, 5), works_due=True) is None


def test_reminder_paused_on_holidays() -> None:
    assert reminder_due(make_user(), ts(2026, 11, 11, 20, 5), paused=True) is None


def test_reminder_not_twice_in_a_row() -> None:
    user = make_user(last_reminded_at=ts(2026, 11, 11, 20, 0))
    assert reminder_due(user, ts(2026, 11, 11, 21, 0)) is None
    assert reminder_due(user, ts(2026, 11, 12, 20, 0)) is None
    assert reminder_due(user, ts(2026, 11, 13, 20, 0)) == "regular"


def test_reminder_skips_sunday() -> None:
    sunday = ts(2026, 11, 15, 20, 0)
    assert reminder_due(make_user(), sunday) is None
    assert reminder_due(make_user(skip_sunday=False), sunday) == "regular"


def test_reminder_off_and_blocked() -> None:
    assert reminder_due(make_user(remind_every=0), ts(2026, 11, 11, 20, 0)) is None
    assert reminder_due(make_user(blocked=True), ts(2026, 11, 11, 20, 0)) is None


def test_reminder_respects_timezone() -> None:
    user = make_user(tz="Asia/Novosibirsk")
    assert reminder_due(user, ts(2026, 11, 11, 20, 0, tz="Asia/Novosibirsk")) == "regular"
    assert reminder_due(user, ts(2026, 11, 11, 20, 0, tz="Europe/Minsk")) is None


def test_snooze() -> None:
    until = ts(2026, 11, 11, 22, 0)
    assert reminder_due(make_user(snooze_until=until), until - 60) is None
    assert reminder_due(make_user(snooze_until=until), until) == "snooze"
    assert reminder_due(make_user(snooze_until=until, last_data_at=until - 600), until) == "clear_snooze"


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
    assert semester_title(date(2027, 2, 9)) == "Весна 2027"


def test_fit_keeps_messages_within_telegram_limit() -> None:
    from bot.screens import Screen, fit

    long = "\n".join(f"строка {i}" for i in range(2000))
    assert len(Screen(long).text) <= 4096
    with_pre = "<pre>" + "\n".join("x" * 50 for _ in range(200)) + "</pre>"
    cut = fit(with_pre)
    assert cut.count("<pre>") == cut.count("</pre>")
    assert fit("коротко") == "коротко"


def test_subject_without_anything_yet_is_neutral() -> None:
    plain = make_view(okr=0)  # без экзамена, без ОКР, без оценок
    assert plain.ok and plain.status_icon == "▫️" and plain.final is None
    assert make_view([grade(9)], okr=0).status_icon == "✅"


def test_average_like_in_college_records() -> None:
    """Средний балл — среднее отметок по предметам, как в ведомости: 58 / 11 = 5,27.

    Реальный случай: без округления (7,75; 6,75; 4,5…) выходило 5,11, и это сбивало с толку.
    """
    data = {
        "ТЭМР": [8], "Стандарты": [8, 9, 8, 8, 7, 9, 9, 4], "Охрана труда": [7, 7, 6, 7],
        "Основы ИГ": [6, 7, 5, 7, 9], "Основы ТМ": [7, 3, 8], "ОКЖД": [4, 5], "ФКИЗ": [4],
        "Материал и ТМ": [3, 4], "Эл.Приборы": [3, 3, 2, 3], "ТОЭ": [3], "Лин.сооружения": [3, 3, 3, 3, 3, 4],
    }  # fmt: skip
    exams = {"Охрана труда", "Эл.Приборы"}
    views = [
        SubjectView(subj(i, name, okr=0 if name == "ФКИЗ" else 1, control=EXAM if name in exams else NONE),
                    [grade(v, gid=j) for j, v in enumerate(values)], [], 3, 10)
        for i, (name, values) in enumerate(data.items(), 1)
    ]  # fmt: skip
    ov = Overview(views)
    assert ov.marks == [8, 8, 7, 7, 6, 5, 4, 4, 3, 3, 3]
    assert fmt_num(ov.avg) == "5,27"
    assert fmt_num(ov.avg_exact) == "5,11"


def test_average_uses_final_mark_after_exam() -> None:
    """После экзамена в средний идёт итог предмета, а не отметка за семестр."""
    exam = SubjectView(subj(1, "A", okr=0, control=EXAM, exam_grade=10), [grade(7)], [], 3, 10)
    plain = SubjectView(subj(2, "B", okr=0), [grade(6)], [], 3, 10)
    assert exam.mark == 9  # (7 + 10) / 2 = 8,5 → 9
    assert Overview([exam, plain]).avg == 7.5


def test_subject_over_several_semesters() -> None:
    """п. 24: предмет идёт два семестра — отметки за семестры складываются, экзамен — с общей отметкой."""
    view = SubjectView(subj(1, "ТОЭ", okr=0, control=EXAM, prev_marks="7"), [grade(9), grade(8, gid=2)], [], 3, 10)
    assert view.previous == [7] and view.semester_grade == 9  # 8,5 → 9 в этом семестре
    assert view.course_marks == [7, 9] and view.base_grade == 8  # (7 + 9) / 2 = 8
    assert view.exam_table()[0] == (3, 4, 6)  # экзамен 3–4 → (8 + 3..4) / 2 → 6: считается от общей отметки
    with_exam = SubjectView(
        subj(1, "ТОЭ", okr=0, control=EXAM, prev_marks="7", exam_grade=10), [grade(9), grade(8, gid=2)], [], 3, 10
    )
    assert with_exam.final.value == 9 and with_exam.final.note == "(8 + 10) / 2 = 9,0"  # итог с экзаменом
    only_exam = SubjectView(
        subj(1, "ТОЭ", okr=0, control=EXAM, prev_marks="7", exam_grade=10),
        [grade(9)], [], 3, 10, final_mode=FINAL_EXAM,
    )  # fmt: skip
    assert only_exam.final.value == 10  # по п. 24 отметка за экзамен окончательная
    assert with_exam.carry == "7 9"  # в следующий семестр уходят обе отметки (итог предмета)
    first = SubjectView(subj(2, "История", okr=0), [grade(6)], [], 3, 10)
    assert first.previous == [] and first.base_grade == 6 and first.carry == "6"
    assert parse_marks("7, 8 x") == [7, 8] and parse_marks(None) == []


def test_current_avg_rounded_before_formula() -> None:
    """Среднее текущих 6,50 округляется до 7, затем (ОКР 8 + 7) / 2 = 7,5 -> 8."""
    view = make_view([grade(7), grade(3), grade(8), grade(8), grade(8, okr=1, gid=5)])
    assert view.current_avg == 6.5
    assert view.semester_exact == 7.5
    assert view.semester_grade == 8
    assert view.formula == "(ОКР 8 + текущие 7 (6,50)) / 2 = 7,50"
