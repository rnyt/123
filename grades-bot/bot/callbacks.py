"""Данные inline-кнопок. Префиксы короткие: в Telegram лимит 64 байта на кнопку."""

from aiogram.filters.callback_data import CallbackData


class Nav(CallbackData, prefix="n"):
    """Переходы между общими экранами: main, stats, help, settings, ..."""

    to: str


class Onb(CallbackData, prefix="ob"):
    """Шаги знакомства: scale, next, back, reset, okr, done."""

    a: str
    v: int = 0


class Subj(CallbackData, prefix="s"):
    """Действия с предметом. n — доп. параметр (страница, номер ОКР, откуда пришли)."""

    a: str
    id: int
    n: int = 0


class AddGrade(CallbackData, prefix="g"):
    s: int
    v: int


class OkrSet(CallbackData, prefix="o"):
    s: int
    n: int  # номер ОКР
    v: int  # оценка; 0 — убрать


class AddWorks(CallbackData, prefix="nw"):
    s: int
    k: str  # lab | prac
    c: int  # сколько
    b: int = 0  # 1 — вернуться к списку работ


class WorkAct(CallbackData, prefix="w"):
    a: str
    id: int


class DelGrade(CallbackData, prefix="dg"):
    id: int
    s: int
    p: int = 0


class UndoGrades(CallbackData, prefix="ug"):
    s: int
    a: int  # первый id
    b: int  # последний id


class UndoDelGrade(CallbackData, prefix="ud"):
    s: int
    v: int
    o: int  # номер ОКР или 0
    t: int  # когда была поставлена
    p: int = 0


class UndoOkr(CallbackData, prefix="uo"):
    s: int
    n: int
    v: int  # прежняя оценка; 0 — не было


class UndoWorks(CallbackData, prefix="uw"):
    s: int
    a: int
    b: int


class Pick(CallbackData, prefix="qp"):
    """Выбор предмета для оценок, отправленных текстом без названия."""

    s: int
    v: str  # «8-9-10»
    o: int = 0  # 1 — это ОКР


class Rem(CallbackData, prefix="r"):
    a: str  # every | time | sun | custom | test
    v: int = 0


class TzSet(CallbackData, prefix="tz"):
    i: int


class ScaleSet(CallbackData, prefix="sc"):
    a: str  # set | pass
    v: int = 0


class Sem(CallbackData, prefix="sm"):
    a: str  # list | new | new_yes | copy | fresh | view
    id: int = 0
