"""Режимы ввода (FSM): чего бот ждёт от следующего текстового сообщения."""

from aiogram.fsm.state import State, StatesGroup


class OnbSt(StatesGroup):
    course = State()  # шаг 1: курс и семестр
    subjects = State()  # шаг 2: список предметов
    setup = State()  # шаг 3: ОКР / экзамены / курсовые
    session = State()  # шаг 4: даты сессии
    holidays = State()  # шаг 4: даты каникул


class Inp(StatesGroup):
    grades = State()  # оценки для предмета (data: sid, added, before)
    okr = State()  # оценка за ОКР (data: sid, slot)
    exam = State()  # отметка за экзамен / диф. зачёт (data: sid)
    project = State()  # отметка за курсовой (data: sid)
    works = State()  # названия/количество работ (data: sid, kind, back)
    wgrade = State()  # оценка за работу (data: sid, wid)
    subjects = State()  # новые предметы
    rename = State()  # новое название предмета (data: sid)
    short = State()  # сокращение предмета (data: sid)
    prev = State()  # отметки за прошлые семестры (data: sid)
    wrename = State()  # новое название работы (data: sid, wid)
    time = State()  # время напоминаний
    tz = State()  # часовой пояс
    dates = State()  # даты сессии/каникул из настроек (data: which)


class AdmSt(StatesGroup):
    add = State()  # юзернеймы для доступа
