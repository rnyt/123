"""Данные inline-кнопок. Префиксы короткие: в Telegram лимит 64 байта на кнопку."""

from aiogram.filters.callback_data import CallbackData

ACCESS_REQUEST = "acc:req"  # кнопка «Попросить доступ» у тех, кому бот закрыт


class Nav(CallbackData, prefix="n"):
    """Переходы между общими экранами: main, stats, help, settings, ..."""

    to: str


class Onb(CallbackData, prefix="ob"):
    """Знакомство: course, term, next, back, reset, setup, tab, tokr, tctl, tprj, dates, skip, done."""

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


class ExamSet(CallbackData, prefix="ex"):
    s: int
    v: int  # отметка; 0 — убрать; 99 — «автомат» (= отметке за семестр)


class CreditSet(CallbackData, prefix="cr"):
    s: int
    v: int  # 1 — зачтено, 0 — не зачтено, 2 — убрать


class ProjSet(CallbackData, prefix="pj"):
    s: int
    v: int  # отметка за курсовой; 0 — убрать


class AddWorks(CallbackData, prefix="nw"):
    s: int
    k: str  # lab | prac
    c: int  # сколько
    b: int = 0  # 1 — вернуться к списку работ


class WorkAct(CallbackData, prefix="w"):
    """v — открыть, pass/fail/unpass — зачтено/не зачтено/сбросить, grade, ren, del, del_yes."""

    a: str
    id: int


class WorkGrade(CallbackData, prefix="wg"):
    w: int
    v: int  # 0 — убрать оценку


class DelGrade(CallbackData, prefix="dg"):
    id: int
    s: int
    p: int = 0


class UndoGrades(CallbackData, prefix="ug"):
    s: int  # 0 — оценки в нескольких предметах
    a: int  # первый id
    b: int  # последний id


class UndoDelGrade(CallbackData, prefix="ud"):
    s: int
    v: int
    o: int  # номер ОКР или 0
    t: int  # когда была поставлена
    p: int = 0
    w: int = 0  # работа, если это оценка за работу


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
    a: str  # every | time | sun | works | custom | test
    v: int = 0


class TzSet(CallbackData, prefix="tz"):
    i: int


class ScaleSet(CallbackData, prefix="sc"):
    a: str  # set | pass
    v: int = 0


class CalcSet(CallbackData, prefix="cm"):
    a: str  # sem | fin
    v: int


class SemSet(CallbackData, prefix="ss"):
    """Настройки текущего семестра: course, term, session, holidays, ask."""

    a: str
    v: int = 0


class Arch(CallbackData, prefix="ar"):
    """Архив: list, view, ask, yes, later, never, copy, fresh."""

    a: str
    id: int = 0


class Adm(CallbackData, prefix="ad"):
    """Админка: main, add, users, user, revoke, revoke_yes, unlist, requests, ok, no, open."""

    a: str
    id: int = 0
    p: int = 0
