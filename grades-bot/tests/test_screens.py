"""Кнопки предметов в главном меню: целая отметка, без обрезки названий."""

from __future__ import annotations

from bot.callbacks import Subj
from bot.logic import SubjectView
from bot.models import EXAM, NONE
from bot.screens import HALF_ROW, mark_text, pack_rows, subject_button, text_width
from tests.test_logic import grade, subj

# Реальный список из семестра пользователя (как на его скриншоте).
REAL = {
    "ТЭМР": [8], "Стандарты": [8, 9, 8, 8, 7, 9, 9, 4], "Охрана труда": [7, 7, 6, 7],
    "Основы ИГ": [6, 7, 5, 7, 9], "Основы ТМ": [7, 3, 8], "ОКЖД": [4, 5], "ФКИЗ": [4],
    "Материал и ТМ": [3, 4], "Эл.Приборы": [3, 3, 2, 3], "ТОЭ": [3], "Лин.соружения": [3, 3, 3, 3, 3, 4],
}  # fmt: skip
EXAMS = {"Охрана труда", "Эл.Приборы"}


def real_views() -> list[SubjectView]:
    return [
        SubjectView(subj(i, name, okr=0 if name == "ФКИЗ" else 1, control=EXAM if name in EXAMS else NONE),
                    [grade(v, gid=j) for j, v in enumerate(values)], [], 3, 10)
        for i, (name, values) in enumerate(REAL.items(), 1)
    ]  # fmt: skip


def labels(rows) -> list[list[str]]:
    return [[b.text for b in row] for row in rows]


def test_buttons_show_mark_not_average() -> None:
    texts = [subject_button(v, Subj(a="v", id=v.id)).text for v in real_views()]
    assert texts[:3] == ["ТЭМР · 8 ❌", "Стандарты · 8 ❌", "🦺 Охрана труда · 7 ❌"]  # 7,75 → 8; 📘 не пишем
    assert "ФКИЗ · 4 ✅" in texts


def test_no_label_is_cut() -> None:
    """Две кнопки в ряд — только если обе подписи влезают целиком, иначе кнопка на весь ряд."""
    rows = pack_rows([subject_button(v, Subj(a="v", id=v.id)) for v in real_views()])
    assert labels(rows) == [
        ["ТЭМР · 8 ❌", "Стандарты · 8 ❌"],
        ["🦺 Охрана труда · 7 ❌"],
        ["Основы ИГ · 7 ❌", "Основы ТМ · 6 ❌"],
        ["ОКЖД · 5 ❌", "ФКИЗ · 4 ✅"],
        ["Материал и ТМ · 4 ❌"],
        ["Эл.Приборы · 3 ❌", "ТОЭ · 3 ❌"],
        ["Лин.соружения · 3 ❌"],
    ]
    for row in rows:
        if len(row) == 2:
            assert all(text_width(b.text) <= HALF_ROW for b in row)


def test_text_width() -> None:
    assert text_width("ТЭМР · 8 ❌") < text_width("Стандарты · 8 ❌") < text_width("Охрана труда · 7 ❌")
    assert text_width("⚛️") == text_width("❌") == 2  # эмодзи с вариантным селектором — как одно


def test_subject_without_grades_and_admin_line() -> None:
    empty = SubjectView(subj(1, "История", okr=0), [], [], 3, 10)
    assert subject_button(empty, Subj(a="v", id=1)).text.endswith("История ▫️")
    physics = SubjectView(subj(2, "Физика"), [grade(9), grade(8, gid=2)], [], 3, 10)
    assert mark_text(physics) == "9 (8,50)"  # для админа: отметка и точный средний
    done = SubjectView(subj(3, "Химия", okr=0, control=EXAM, exam_grade=10), [grade(7)], [], 3, 10)
    assert mark_text(done) == "9"  # после экзамена — итог (7 + 10) / 2 = 8,5 → 9, без семестрового среднего
