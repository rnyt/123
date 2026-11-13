"""Мини-писатель .xlsx: файл должен открываться в любой программе (Excel, Google Таблицы, телефон)."""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

from bot.xlsx import Sheet, build, column
from tests.harness import read_xlsx


def sample() -> bytes:
    first = Sheet("Итоги", widths=[20, 10])
    first.add("Предмет", "Средний", bold=True)
    first.add("Физика & ТОЭ <ЛР>", 7.75)
    first.add()
    first.add("Средний балл", 58 / 11, None, "= 58 / 11", bold=True)
    second = Sheet("Все: оценки/работы?")
    second.add("Охрана труда", 8)
    second.add("тест\x01управляющих\x1fсимволов", "")
    return build(
        [first, second], title="Оценки — 2 курс · 1 семестр", created=datetime(2026, 11, 13, tzinfo=timezone.utc)
    )


def test_columns() -> None:
    assert [column(i) for i in (0, 1, 25, 26, 27, 51, 52, 701, 702)] == [
        "A", "B", "Z", "AA", "AB", "AZ", "BA", "ZZ", "AAA",
    ]  # fmt: skip


def test_roundtrip_values_and_escaping() -> None:
    book = read_xlsx(sample())
    assert list(book) == ["Итоги", "Все  оценки работы"]  # запрещённые в имени листа символы убраны
    first = book["Итоги"]
    assert first[0] == ["Предмет", "Средний"]
    assert first[1] == ["Физика & ТОЭ <ЛР>", 7.75]
    assert first[2] == []
    assert first[3][0] == "Средний балл" and first[3][1] == pytest.approx(5.2727, abs=1e-4)
    assert first[3][2] is None and first[3][3] == "= 58 / 11"
    assert book["Все  оценки работы"] == [["Охрана труда", 8], ["теступравляющихсимволов"]]


def test_opens_in_openpyxl() -> None:
    """Независимая проверка формата: openpyxl читает файл как настоящий Excel."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(io.BytesIO(sample()))
    assert wb.sheetnames == ["Итоги", "Все  оценки работы"]
    ws = wb["Итоги"]
    assert ws["A2"].value == "Физика & ТОЭ <ЛР>"
    assert ws["B2"].value == 7.75 and ws["B2"].number_format == "0.00"
    assert ws["A1"].font.b and not ws["A2"].font.b
    assert ws.column_dimensions["A"].width == 20
    assert wb.properties.title == "Оценки — 2 курс · 1 семестр"
