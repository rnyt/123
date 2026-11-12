"""Режимы ввода (FSM): чего бот ждёт от следующего текстового сообщения."""

from aiogram.fsm.state import State, StatesGroup


class OnbSt(StatesGroup):
    scale = State()
    subjects = State()
    okr = State()


class Inp(StatesGroup):
    grades = State()  # оценки для предмета (data: sid, added, before)
    okr = State()  # оценка за ОКР (data: sid, slot)
    works = State()  # названия/количество работ (data: sid, kind, back)
    subjects = State()  # новые предметы
    rename = State()  # новое название предмета (data: sid)
    wrename = State()  # новое название работы (data: sid, wid)
    time = State()  # время напоминаний
    tz = State()  # часовой пояс
