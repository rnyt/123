"""Чистая логика без Telegram: разбор ввода, средние, аттестация, цели, напоминания.

Всё здесь легко тестируется и не зависит от базы данных.
"""

from __future__ import annotations

import difflib
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from functools import cache, cached_property, lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (
    CALC_MEAN,
    CALC_SSO,
    CONTROL_LABEL,
    CONTROLS,
    CREDIT,
    DIFF,
    EXAM,
    FINAL_AVG,
    FINAL_EXAM,
    LAB,
    NONE,
    PRAC,
    Grade,
    Subject,
    User,
    Work,
)

# ---------------------------------------------------------------- ограничения

MAX_SUBJECTS = 40
MAX_NAME_LEN = 60
MAX_WORKS_PER_SUBJECT = 60
MAX_BATCH = 20  # оценок за один раз

# Шкала -> порог: в ССО положительные отметки — от 3 (п. 4 Правил № 282).
SCALES: dict[int, int] = {10: 3, 5: 3}

SNOOZE_SECONDS = 2 * 60 * 60
REMIND_WINDOW = timedelta(hours=4)  # если бот был выключен в момент напоминания

GRADE_FORMS = ("оценка", "оценки", "оценок")
SUBJECT_FORMS = ("предмет", "предмета", "предметов")
WORK_FORMS = ("работа", "работы", "работ")
DAY_FORMS = ("день", "дня", "дней")


# ---------------------------------------------------------------- форматирование


def plural(n: int, forms: tuple[str, str, str], with_number: bool = True) -> str:
    """Русское склонение: 1 оценка, 2 оценки, 5 оценок."""
    tail = abs(n) % 100
    if 11 <= tail <= 19:
        word = forms[2]
    elif tail % 10 == 1:
        word = forms[0]
    elif 2 <= tail % 10 <= 4:
        word = forms[1]
    else:
        word = forms[2]
    return f"{n} {word}" if with_number else word


def fmt_num(value: float | None, digits: int = 2) -> str:
    """7.4285 -> «7,43»; None -> «—»."""
    if value is None:
        return "—"
    return f"{value:.{digits}f}".replace(".", ",")


def round_half_up(value: float) -> int:
    """Школьное округление: 7,5 -> 8 (а не банковское, как у round())."""
    return int(math.floor(value + 0.5 + 1e-9))


def mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def progress_bar(done: int, total: int, width: int = 10) -> str:
    """▰▰▰▱▱ — наглядный прогресс."""
    if total <= 0:
        return ""
    cells = min(total, width)
    filled = round(done / total * cells)
    if done > 0 and filled == 0:
        filled = 1
    if done < total and filled == cells:
        filled = cells - 1
    return "▰" * filled + "▱" * (cells - filled)


def join_ru(items: Sequence[str]) -> str:
    """«a», «a и b», «a, b и c»."""
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " и " + items[-1]


def short_list(items: Sequence[str], limit: int = 4) -> str:
    """«ЛР 1, ЛР 2, ЛР 3 и ещё 2»."""
    items = list(items)
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f" и ещё {len(items) - limit}"


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------- нормализация и имена


def norm(text: str) -> str:
    """Нижний регистр, ё->е, без пунктуации и лишних пробелов."""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[\W_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_BULLET = re.compile(r"^\s*(?:[-–—•*·▪►]+|\d{1,2}[.)])\s*")


def split_names(text: str) -> list[str]:
    """«Физика, Химия\\n- История» -> ['Физика', 'Химия', 'История']."""
    result: list[str] = []
    for part in re.split(r"[\n,;]+", text):
        part = _BULLET.sub("", part)
        part = re.sub(r"\s+", " ", part).strip().strip(".").strip()
        if part:
            result.append(part[:MAX_NAME_LEN].strip())
    return result


_ABBR = re.compile(r"^(?P<name>.+?)\s*\((?P<short>[^()\d\s]{2,10})\)\s*$")


def parse_subject_entry(text: str) -> tuple[str, str | None]:
    """«Теоретические основы электротехники (ТОЭ)» -> ('Теоретические основы электротехники', 'ТОЭ')."""
    text = text.strip()
    match = _ABBR.match(text)
    if match and len(match.group("name").strip()) > len(match.group("short")):
        return match.group("name").strip(), match.group("short").strip()
    return text, None


def clean_short(text: str) -> str | None:
    """Сокращение предмета: до 12 символов, без лишних пробелов; «-» — убрать."""
    value = " ".join(text.split()).strip(" ()")
    if value in {"", "-", "—", "–"}:
        return None
    return value[:12]


_LEADING_ICON = re.compile(r"^([^\w\s«\"'(\[{<]{1,8})\s+(\S.*)$")


def split_icon(name: str) -> tuple[str | None, str]:
    """«🧮 Матан» -> ('🧮', 'Матан'). Если эмодзи в начале нет — (None, name)."""
    match = _LEADING_ICON.match(name)
    if match and any(ord(ch) > 0x2000 for ch in match.group(1)):
        return match.group(1), match.group(2)
    return None, name


def display_name(name: str) -> str:
    return split_icon(name)[1]


# Порядок важен: более специфичные правила — выше.
# Ключ из ≤3 букв должен совпасть со словом целиком, длиннее — с началом слова,
# ключ с пробелом ищется как подстрока.
_ICON_RULES: list[tuple[tuple[str, ...], str]] = [
    (("физкульт", "физра", "физическая культура", "физическое воспитание", "спорт"), "🏃"),
    (("теория вероятностей", "тервер", "твимс", "статист", "эконометр"), "📊"),
    (
        (
            "матем",
            "матан",
            "алгебр",
            "геометр",
            "дискретн",
            "вычмат",
            "вышмат",
            "линал",
            "дифференц",
            "интеграл",
            "топологи",
        ),
        "📐",
    ),
    (("физик", "физ", "оптик", "термодинам"), "⚛️"),
    (("хими",), "⚗️"),
    (("биолог", "анатом", "генетик", "физиолог", "ботаник", "зоолог", "микробиол"), "🧬"),
    (
        (
            "программ",
            "информатик",
            "алгоритм",
            "компьютер",
            "ооп",
            "веб",
            "web",
            "python",
            "java",
            "sql",
            "базы данных",
            "бд",
            "сети",
            "сетев",
            "ит",
            "it",
            "кибер",
            "разработ",
            "операционн",
            "ос",
            "ии",
            "машинн",
            "нейрон",
            "данных",
            "оаип",
        ),
        "💻",
    ),
    (("английск", "english", "англ"), "🇬🇧"),
    (("немецк", "deutsch", "german"), "🇩🇪"),
    (("французск", "french"), "🇫🇷"),
    (("испанск", "spanish"), "🇪🇸"),
    (("китайск", "chinese"), "🇨🇳"),
    (("русск", "белорусск", "литератур", "словесн", "риторик", "культура речи"), "📖"),
    (("иностранн", "язык", "лингв", "перевод"), "🗣"),
    (("истори",), "📜"),
    (("философ", "логик", "этик"), "🤔"),
    (
        ("эконом", "финанс", "бухгалт", "менеджм", "маркет", "бизнес", "учет", "налог", "банк"),
        "💰",
    ),
    (("право", "права", "правов", "юрид", "юриспр", "закон", "конституц"), "⚖️"),
    (("психолог", "педагог"), "🧠"),
    (("социолог", "политолог", "обществ", "культуролог", "идеолог"), "🏛"),
    (("географ", "геолог"), "🌍"),
    (("черчен", "графи", "начертат", "дизайн", "cad"), "📏"),
    (("электр", "электрон", "схемотех", "радио", "цифров"), "🔌"),
    (("механик", "сопромат", "термех", "детали машин", "материаловед", "технолог"), "⚙️"),
    (("безопасност", "бжд", "охрана труда", "защита населения", "гражданская оборона"), "🦺"),
    (("музык",), "🎵"),
    (("искусств", "рисун", "живопис"), "🎨"),
    (("медицин", "фармак", "терапи", "хирург"), "🩺"),
    (("астроном",), "🔭"),
    (("эколог",), "🌱"),
]


def _key_matches(key: str, normalized: str, words: list[str]) -> bool:
    if " " in key:
        return key in normalized
    if len(key) <= 3:
        return key in words
    return any(word.startswith(key) for word in words)


DEFAULT_ICON = "📘"  # предмет не узнан — общий значок


def subject_icon(name: str) -> str:
    """Иконка предмета: своя (эмодзи в начале названия) или подобранная по смыслу."""
    icon, rest = split_icon(name)
    if icon:
        return icon
    normalized = norm(rest)
    words = normalized.split()
    for keys, emoji in _ICON_RULES:
        if any(_key_matches(key, normalized, words) for key in keys):
            return emoji
    return DEFAULT_ICON


# ---------------------------------------------------------------- поиск предмета


def _abbrev(query: str, words: list[str]) -> bool:
    """Можно ли собрать query из начал слов по порядку: «матан» ~ «МАТематический АНализ»."""
    if len(query) < 2 or not words:
        return False

    @cache
    def go(i: int, j: int) -> bool:
        if i == len(query):
            return True
        for k in range(j, len(words)):
            word = words[k]
            length = 0
            while length < len(word) and i + length < len(query) and word[length] == query[i + length]:
                length += 1
                if go(i + length, k + 1):
                    return True
        return False

    return go(0, 0)


# Студенческий сленг -> начала слов в официальных названиях.
SLANG: dict[str, tuple[str, ...]] = {
    "матан": ("математ", "анализ"),
    "вышмат": ("математ",),
    "вышка": ("математ",),
    "матеша": ("математ",),
    "линал": ("алгебр",),
    "линейка": ("алгебр",),
    "тервер": ("вероятн",),
    "твимс": ("вероятн",),
    "матстат": ("статист",),
    "дискра": ("дискретн",),
    "диффуры": ("дифференц",),
    "физра": ("физкульт", "физическ", "физвосп"),
    "физкультура": ("физкульт", "физическ"),
    "инфа": ("информат",),
    "информатика": ("информат",),
    "прога": ("программ",),
    "проги": ("программ",),
    "програмирование": ("программ",),
    "иняз": ("иностран", "английск", "немецк", "французск"),
    "инглиш": ("английск",),
    "англ": ("английск",),
    "бжд": ("безопасн",),
    "термех": ("механик",),
    "сопромат": ("сопротивлен",),
    "начерталка": ("начертат",),
    "история беларуси": ("истори",),
}


def match_subjects(query: str, subjects: Sequence[Subject]) -> list[Subject]:
    """Ищет предмет по началу, слову, аббревиатуре, сленгу, подстроке и с опечатками."""
    q = norm(query)
    if not q:
        return []
    by_short = [s for s in subjects if s.short and norm(s.short) == q]
    if by_short:
        return by_short
    compact = q.replace(" ", "")
    named = [(s, norm(display_name(s.name))) for s in subjects]
    slang = SLANG.get(q) or SLANG.get(compact) or ()

    def slang_match(n: str) -> bool:
        if not slang:
            return False
        words = n.split()
        if any(w.startswith(stem) for stem in slang for w in words):
            return True
        # Предмет сам назван сленгом («Матан»), а ищут синонимом («вышка»).
        own = {stem for w in words for stem in SLANG.get(w, ())}
        return bool(own & set(slang))

    tiers = (
        lambda n: n == q,
        lambda n: n.startswith(q),
        lambda n: all(any(w.startswith(qw) for w in n.split()) for qw in q.split()),
        lambda n: _abbrev(compact, n.split()),
        slang_match,
        lambda n: len(q) >= 3 and q in n,
    )
    for tier in tiers:
        found = [s for s, n in named if tier(n)]
        if found:
            return found

    # Опечатки: «физикка», «матиматика».
    scored: list[tuple[float, Subject]] = []
    for subject, n in named:
        ratio = difflib.SequenceMatcher(None, q, n).ratio()
        for word in n.split():
            ratio = max(ratio, difflib.SequenceMatcher(None, q, word).ratio())
        scored.append((ratio, subject))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] >= 0.75:
        if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05:
            return [scored[0][1]]
        return [s for r, s in scored if r >= 0.75]
    return []


# ---------------------------------------------------------------- разбор оценок

_GRADE_SEPARATORS = re.compile(r"[\s,;/\\|+]+")
_NUMERIC_WORD = re.compile(r"^[0-9][0-9,;/\\|+.]*$")
_DIGITS = re.compile(r"[0-9]+")
_OKR_WORDS = {"окр", "окр.", "окр:"}


def split_digits(token: str, high: int) -> list[int] | None:
    """Слитные цифры: «1078105» -> [10, 7, 8, 10, 5] (на 10-балльной «10» — одна оценка)."""
    values: list[int] = []
    i = 0
    while i < len(token):
        if high >= 10 and token.startswith("10", i):
            values.append(10)
            i += 2
            continue
        digit = int(token[i])
        if digit == 0:
            return None
        values.append(digit)
        i += 1
    return values


def parse_marks(text: str | None) -> list[int]:
    """«7 8» → [7, 8]: отметки за прошлые семестры, как они хранятся у предмета."""
    return [int(part) for part in (text or "").replace(",", " ").split() if part.isdigit()]


def parse_grades(text: str, low: int, high: int) -> tuple[list[int], list[str]]:
    """«8, 9/10 7» -> ([8, 9, 10, 7], []); «10910» -> [10, 9, 10]. Неверные кусочки — отдельно."""
    values: list[int] = []
    bad: list[str] = []
    for token in _GRADE_SEPARATORS.split(text.strip()):
        token = token.strip(".")
        if not token:
            continue
        if not _DIGITS.fullmatch(token):
            bad.append(token)
            continue
        if low <= int(token) <= high:
            values.append(int(token))
            continue
        pieces = split_digits(token, high) if len(token) > 1 else None
        if pieces and all(low <= v <= high for v in pieces):
            values.extend(pieces)
        else:
            bad.append(token)
    return values, bad


@dataclass(slots=True)
class Quick:
    """Результат разбора свободного текста вроде «физ 8 9» или «окр матан 7»."""

    query: str
    values: list[int] = field(default_factory=list)
    bad: list[str] = field(default_factory=list)
    okr: bool = False


def _strip_okr(words: list[str]) -> tuple[list[str], bool]:
    kept, found = [], False
    for word in words:
        if word.lower().strip(".:,;-—") == "окр":
            found = True
        else:
            kept.append(word)
    return kept, found


def parse_quick(text: str, low: int, high: int, subject_names: Sequence[str] = ()) -> Quick:
    raw = text.strip()
    lowered = raw.lower().replace("ё", "е")

    # 1) Текст начинается с точного названия предмета: «Физика 2 8 9» при предмете «Физика 2».
    for name in sorted((display_name(n) for n in subject_names), key=len, reverse=True):
        key = name.lower().replace("ё", "е")
        if key and lowered.startswith(key):
            rest = raw[len(key) :]
            if rest and (rest[0].isalnum() or rest[0] == "_"):
                continue
            words, okr = _strip_okr(rest.replace(":", " ").replace("=", " ").split())
            if all(_NUMERIC_WORD.match(w) for w in words):
                values, bad = parse_grades(" ".join(words), low, high)
                return Quick(query=name, values=values, bad=bad, okr=okr)

    # 2) Общий случай: «физ 8 9», «8 9 физ», «матан: 7,8», «физика8».
    prepared = re.sub(r"[:=]", " ", raw)
    prepared = re.sub(r"(?<=[^\W\d_])(?=\d)", " ", prepared)
    words, okr = _strip_okr(prepared.split())

    end = len(words)
    while end > 0 and _NUMERIC_WORD.match(words[end - 1]):
        end -= 1
    if end < len(words):
        name_words, number_words = words[:end], words[end:]
    else:
        start = 0
        while start < len(words) and _NUMERIC_WORD.match(words[start]):
            start += 1
        name_words, number_words = words[start:], words[:start]

    query = " ".join(name_words).strip(" -—–")
    values, bad = parse_grades(" ".join(number_words), low, high)
    return Quick(query=query, values=values, bad=bad, okr=okr)


_SEGMENTS = re.compile(r"\n+|[,;]\s*(?=[^\W\d_])")


def split_segments(text: str) -> list[str]:
    """«физ 8 9, матан 7» или по строкам -> ['физ 8 9', 'матан 7']. «8, 9» не делится."""
    return [part.strip() for part in _SEGMENTS.split(text) if part and part.strip()]


# ---------------------------------------------------------------- сводка по предмету


@dataclass(slots=True)
class Goal:
    """Сколько хороших оценок нужно до следующей округлённой оценки."""

    target: int
    options: list[tuple[int, int]]  # (оценка, сколько штук)


def need_count(values: Sequence[int], threshold: float, grade: int) -> int | None:
    """Минимальное число оценок `grade`, чтобы средний стал ≥ threshold."""
    n, total = len(values), sum(values)
    if n and total >= threshold * n - 1e-9:
        return 0
    if grade <= threshold:
        return None
    k = math.ceil((threshold * n - total) / (grade - threshold) - 1e-9)
    return max(k, 1)


def goal_for(values: Sequence[int], need_avg: float, scale: int, target: int, limit: int = 25) -> Goal | None:
    """Варианты «k × g», чтобы среднее `values` стало не ниже `need_avg`."""
    best = need_count(values, need_avg, scale)
    if not best or best > limit:
        return None
    low = max(math.floor(need_avg) + 1, 1)
    options: list[tuple[int, int]] = []
    # Самая низкая оценка, которой хватит за минимальное число попыток.
    for grade in range(low, scale + 1):
        if need_count(values, need_avg, grade) == best:
            options.append((grade, best))
            break
    # Вариант «чуть больше оценок, но пониже».
    if options and options[0][0] > low:
        for grade in range(low, options[0][0]):
            k = need_count(values, need_avg, grade)
            if k is not None and k <= min(best + 2, limit):
                options.append((grade, k))
                break
    return Goal(target=target, options=options)


def next_goal(values: Sequence[int], scale: int, limit: int = 25) -> Goal | None:
    """Цель для простого среднего: до следующей округлённой оценки."""
    if not values:
        return None
    rounded = round_half_up(sum(values) / len(values))
    if rounded >= scale:
        return None
    return goal_for(values, rounded + 0.5, scale, rounded + 1, limit)


@dataclass(slots=True)
class Problem:
    # no_okr | okr_low | works | project_missing | project_low | semester_low | exam_failed | credit_failed
    kind: str
    slot: int = 0
    value: int = 0
    works: list[Work] = field(default_factory=list)


ADMISSION_KINDS = frozenset({"no_okr", "okr_low", "works", "project_missing", "project_low", "semester_low"})


@dataclass(slots=True)
class Final:
    """Итог по предмету за семестр."""

    kind: str  # grade — отметка, pass — зачтено, debt — задолженность, na — не аттестован
    value: int | None = None
    note: str = ""

    @property
    def done(self) -> bool:
        return self.kind in ("grade", "pass")


@dataclass
class SubjectView:
    """Всё о предмете для экрана: средний, ОКР, работы, курсовой, допуск, экзамен, итог."""

    subject: Subject
    grades: list[Grade]
    works: list[Work]
    pass_mark: int
    scale: int
    calc_mode: str = CALC_SSO
    final_mode: str = FINAL_AVG

    @property
    def id(self) -> int:
        return self.subject.id

    @cached_property
    def icon(self) -> str:
        return subject_icon(self.subject.name)

    @cached_property
    def name(self) -> str:
        return display_name(self.subject.name)

    @property
    def short(self) -> str | None:
        return self.subject.short

    @property
    def label(self) -> str:
        """Как подписывать в кнопках: сокращение, если есть."""
        return self.subject.short or self.name

    @property
    def title(self) -> str:
        return f"{self.name} ({self.short})" if self.short else self.name

    @property
    def control(self) -> str:
        return self.subject.control if self.subject.control in CONTROLS else NONE

    # ------------------------------------------------------------ отметки

    @cached_property
    def values(self) -> list[int]:
        return [g.value for g in self.grades]

    @cached_property
    def regular(self) -> list[Grade]:
        """Обычные отметки текущей аттестации (без ОКР и без оценок за работы)."""
        return [g for g in self.grades if not g.is_okr and not g.is_work]

    @cached_property
    def work_grades(self) -> dict[int, Grade]:
        return {g.work_id: g for g in self.grades if g.work_id is not None}

    @cached_property
    def current(self) -> list[int]:
        """Все отметки текущей аттестации: обычные и за работы (без ОКР)."""
        return [g.value for g in self.grades if not g.is_okr]

    @cached_property
    def current_avg(self) -> float | None:
        return mean(self.current)

    @cached_property
    def okr_slots(self) -> list[tuple[int, Grade | None]]:
        by_slot = {g.okr_no: g for g in self.grades if g.is_okr}
        return [(slot, by_slot.get(slot)) for slot in range(1, self.subject.okr_count + 1)]

    @cached_property
    def okr_values(self) -> list[int]:
        return [g.value for _, g in self.okr_slots if g is not None]

    @property
    def simple(self) -> bool:
        """Простое среднее: так выбрано в настройках или предмет общеобразовательный."""
        return self.calc_mode == CALC_MEAN or self.subject.general

    @cached_property
    def parts(self) -> list[tuple[str, float]]:
        """Слагаемые отметки за семестр по п. 22 Правил: ОКР, курсовой, среднее текущих."""
        result: list[tuple[str, float]] = []
        for slot, grade in self.okr_slots:
            if grade is not None:
                result.append((self.okr_label(slot), grade.value))
        if self.subject.project and self.subject.project_grade is not None:
            result.append(("курсовой", self.subject.project_grade))
        if self.current_avg is not None:
            result.append(("текущие", self.current_avg))
        return result

    @cached_property
    def semester_exact(self) -> float | None:
        """Отметка за семестр до округления."""
        if self.simple:
            return mean(self.values)
        return mean(value for _, value in self.parts)

    @property
    def avg(self) -> float | None:
        return self.semester_exact

    @cached_property
    def semester_grade(self) -> int | None:
        return round_half_up(self.semester_exact) if self.semester_exact is not None else None

    @property
    def mark(self) -> int | None:
        """Отметка предмета для среднего балла: итоговая, если уже есть, иначе — за семестр."""
        final = self.final
        if final is not None and final.value is not None:
            return final.value
        return self.semester_grade

    # ------------------------------------------------------------ несколько семестров (п. 24)

    @cached_property
    def previous(self) -> list[int]:
        """Отметки за прошлые семестры, если предмет идёт не первый семестр."""
        return parse_marks(self.subject.prev_marks)

    @cached_property
    def course_marks(self) -> list[int]:
        """Отметки за все семестры по предмету: прошлые и этот. Пусто, если предмет односеместровый."""
        if not self.previous or self.semester_grade is None:
            return []
        return [*self.previous, self.semester_grade]

    @cached_property
    def course_exact(self) -> float | None:
        """Среднее отметок за все семестры (7 и 8 → 7,5)."""
        return mean(self.course_marks)

    @property
    def base_grade(self) -> int | None:
        """С чем складывается экзамен: отметка за семестр, а если предмет шёл несколько семестров — за все."""
        return round_half_up(self.course_exact) if self.course_exact is not None else self.semester_grade

    @property
    def carry(self) -> str | None:
        """Что перенести в следующий семестр, если предмет продолжается: прошлые отметки и отметку за этот."""
        marks = [*self.previous, *([self.mark] if self.mark is not None else [])]
        return " ".join(map(str, marks)) or None

    @property
    def formula(self) -> str | None:
        """«(ОКР 8 + курсовой 9 + текущие 7,20) / 3 = 8,07» — когда считается не простым средним."""
        if self.simple or len(self.parts) < 2:
            return None
        terms = " + ".join(f"{name} {fmt_num(value) if value % 1 else int(value)}" for name, value in self.parts)
        return f"({terms}) / {len(self.parts)} = {fmt_num(self.semester_exact)}"

    @cached_property
    def goal(self) -> Goal | None:
        if self.simple:
            return next_goal(self.values, self.scale)
        if not self.current or self.semester_grade is None or self.semester_grade >= self.scale:
            return None
        fixed = [value for name, value in self.parts if name != "текущие"]
        target = self.semester_grade + 1
        need_avg = (target - 0.5) * (len(fixed) + 1) - sum(fixed)
        return goal_for(self.current, need_avg, self.scale, target)

    # ------------------------------------------------------------ работы

    @cached_property
    def labs(self) -> list[Work]:
        return sorted((w for w in self.works if w.kind == LAB), key=lambda w: (w.num, w.id))

    @cached_property
    def pracs(self) -> list[Work]:
        return sorted((w for w in self.works if w.kind == PRAC), key=lambda w: (w.num, w.id))

    @cached_property
    def open_works(self) -> list[Work]:
        """Ещё не зачтённые работы (в том числе «не зачтено»)."""
        return [w for w in self.labs + self.pracs if not w.passed]

    @property
    def pending(self) -> list[Work]:
        return self.open_works

    # ------------------------------------------------------------ аттестация

    @property
    def has_session(self) -> bool:
        return self.control != NONE

    @cached_property
    def problems(self) -> list[Problem]:
        result: list[Problem] = []
        for slot, grade in self.okr_slots:
            if grade is None:
                result.append(Problem("no_okr", slot=slot))
            elif grade.value < self.pass_mark:
                result.append(Problem("okr_low", slot=slot, value=grade.value))
        if self.open_works:
            result.append(Problem("works", works=self.open_works))
        if self.subject.project:
            if self.subject.project_grade is None:
                result.append(Problem("project_missing"))
            elif self.subject.project_grade < self.pass_mark:
                result.append(Problem("project_low", value=self.subject.project_grade))
        if self.semester_grade is not None and self.semester_grade < self.pass_mark:
            result.append(Problem("semester_low", value=self.semester_grade))
        exam = self.subject.exam_grade
        if self.control in (EXAM, DIFF) and exam is not None and exam < self.pass_mark:
            result.append(Problem("exam_failed", value=exam))
        if self.control == CREDIT and self.subject.credit is False:
            result.append(Problem("credit_failed"))
        return result

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def admission_problems(self) -> list[Problem]:
        return [p for p in self.problems if p.kind in ADMISSION_KINDS]

    @property
    def admitted(self) -> bool:
        """Допуск к экзамену / диф. зачёту (п. 19–20): за семестр от 3, курсовой от 3, работы зачтены."""
        if not self.has_session or self.admission_problems:
            return False
        return self.control == CREDIT or self.semester_grade is not None

    @property
    def avg_below_pass(self) -> bool:
        return self.semester_exact is not None and self.semester_exact < self.pass_mark

    @property
    def control_label(self) -> str:
        return CONTROL_LABEL[self.control]

    @cached_property
    def final(self) -> Final | None:
        """Итог за семестр или None, если ещё рано."""
        control = self.control
        if control in (EXAM, DIFF):
            exam = self.subject.exam_grade
            if exam is None:
                return None
            if exam < self.pass_mark:
                return Final("debt", exam, f"{self.control_label} на {exam}")
            base = self.base_grade  # за семестр, а у предмета на несколько семестров — за все семестры
            if self.final_mode == FINAL_EXAM or base is None:
                return Final("grade", exam, f"{self.control_label} {exam}")
            exact = (base + exam) / 2
            return Final("grade", round_half_up(exact), f"({base} + {exam}) / 2 = {fmt_num(exact, 1)}")
        if control == CREDIT:
            if self.subject.credit is None:
                return None
            return Final("pass", None, "зачтено") if self.subject.credit else Final("debt", None, "не зачтено")
        if self.semester_grade is None:
            return None
        blocking = [p for p in self.problems if p.kind != "semester_low"]
        if blocking:
            return Final("na", None, "не аттестован")
        if self.semester_grade < self.pass_mark:
            return Final("debt", self.semester_grade, f"за семестр {self.semester_grade}")
        return Final("grade", self.semester_grade, "за семестр")

    @property
    def exam_pending(self) -> bool:
        return self.control in (EXAM, DIFF) and self.subject.exam_grade is None

    @property
    def can_exempt(self) -> bool:
        """Отметка за семестр 9+ — можно получить освобождение от экзамена (п. 20)."""
        return (
            self.control == EXAM
            and self.scale == 10
            and self.exam_pending
            and self.semester_grade is not None
            and self.semester_grade >= 9
        )

    def exam_table(self) -> list[tuple[int, int, int]]:
        """Какой выйдет итог при каждой отметке за экзамен: [(от, до, итог), ...]."""
        base = self.base_grade
        if self.final_mode != FINAL_AVG or base is None:
            return []
        rows: list[list[int]] = []
        for exam in range(self.pass_mark, self.scale + 1):
            result = round_half_up((base + exam) / 2)
            if rows and rows[-1][2] == result:
                rows[-1][1] = exam
            else:
                rows.append([exam, exam, result])
        return [(lo, hi, result) for lo, hi, result in rows]

    def okr_label(self, slot: int) -> str:
        return "ОКР" if self.subject.okr_count <= 1 else f"ОКР №{slot}"

    def reasons(self, admission_only: bool = False) -> list[str]:
        """Причины по-человечески: «нет ОКР», «не зачтены ЛР 3, ЛР 4», «курсовой не защищён»."""
        lines = []
        problems = self.admission_problems if admission_only else self.problems
        for p in problems:
            if p.kind == "no_okr":
                lines.append(f"нет оценки за {self.okr_label(p.slot)}")
            elif p.kind == "okr_low":
                lines.append(f"{self.okr_label(p.slot)} на {p.value} (нужно от {self.pass_mark})")
            elif p.kind == "works":
                lines.append("не зачтены " + short_list([w.short for w in p.works]))
            elif p.kind == "project_missing":
                lines.append("курсовой проект не защищён")
            elif p.kind == "project_low":
                lines.append(f"курсовой на {p.value} (нужно от {self.pass_mark})")
            elif p.kind == "semester_low":
                lines.append(f"за семестр выходит {p.value} (нужно от {self.pass_mark})")
            elif p.kind == "exam_failed":
                lines.append(f"{self.control_label} на {p.value} — нужна пересдача")
            elif p.kind == "credit_failed":
                lines.append("зачёт не сдан — нужна пересдача")
        return lines

    @property
    def status_icon(self) -> str:
        """🏁 — экзамен/зачёт сдан, ❌ — есть проблемы, ▫️ — оценок ещё нет, ✅ — всё в порядке."""
        if self.has_session and self.final is not None and self.final.done:
            return "🏁"
        if self.problems:
            return "❌"
        if self.semester_exact is None and self.final is None:
            return "▫️"
        return "✅"


@dataclass
class Overview:
    views: list[SubjectView]

    @cached_property
    def marks(self) -> list[int]:
        """Отметки по предметам, из которых складывается средний балл."""
        return [mark for v in self.views if (mark := v.mark) is not None]

    @cached_property
    def avg(self) -> float | None:
        """Средний балл, как в ведомости: среднее отметок по предметам (у каждого предмета равный вес).

        Берётся итоговая отметка предмета, а пока её нет — отметка за семестр (целая, после округления).
        """
        return mean(self.marks)

    @cached_property
    def avg_exact(self) -> float | None:
        """То же, но без округления: среднее точных значений за семестр (7,75 вместо 8)."""
        return mean(v.semester_exact for v in self.views if v.semester_exact is not None)

    @cached_property
    def avg_all(self) -> float | None:
        """Среднее по всем отметкам подряд."""
        return mean(value for v in self.views for value in v.values)

    def avg_before(self, ts: int) -> float | None:
        """Каким был средний балл на момент ts — для динамики «за неделю»."""
        past = [
            SubjectView(v.subject, [g for g in v.grades if g.created_at < ts], v.works, v.pass_mark, v.scale,
                        v.calc_mode, v.final_mode)
            for v in self.views
        ]  # fmt: skip
        return Overview(past).avg

    def added_since(self, ts: int) -> int:
        return sum(1 for v in self.views for g in v.grades if g.created_at >= ts)

    @property
    def grades_count(self) -> int:
        return sum(len(v.grades) for v in self.views)

    @property
    def works_total(self) -> int:
        return sum(len(v.works) for v in self.views)

    @property
    def works_done(self) -> int:
        return sum(1 for v in self.views for w in v.works if w.passed)

    @property
    def open_works(self) -> list[tuple[SubjectView, list[Work]]]:
        return [(v, v.open_works) for v in self.views if v.open_works]

    @property
    def failing(self) -> list[SubjectView]:
        return [v for v in self.views if v.problems]

    @property
    def session_views(self) -> list[SubjectView]:
        return [v for v in self.views if v.has_session]

    @property
    def session_done(self) -> list[SubjectView]:
        return [v for v in self.session_views if v.final is not None and v.final.done]

    @property
    def debts(self) -> list[SubjectView]:
        return [v for v in self.views if v.final is not None and v.final.kind == "debt"]

    @property
    def session_complete(self) -> bool:
        return bool(self.session_views) and len(self.session_done) == len(self.session_views)

    @property
    def session_avg(self) -> float | None:
        """Средний балл сессии: по экзаменам и диф. зачётам."""
        return mean(
            v.subject.exam_grade
            for v in self.session_views
            if v.control in (EXAM, DIFF) and v.subject.exam_grade is not None
        )

    @property
    def finals_avg(self) -> float | None:
        """Средний итоговых отметок за семестр."""
        return mean(
            v.final.value for v in self.views if v.final is not None and v.final.kind == "grade" and v.final.value
        )


# ---------------------------------------------------------------- время и напоминания

_OFFSET = re.compile(r"^(?:utc|gmt)?\s*([+\-−])\s*(\d{1,2})(?:[:.]?(\d{2}))?$", re.IGNORECASE)


def parse_tz(name: str) -> tuple[str, tzinfo] | None:
    """Пояс по имени («Europe/Moscow») или смещению («+3», «UTC+05:30»). None, если не понял."""
    name = (name or "").strip()
    if not name:
        return None
    match = _OFFSET.match(name)
    if match:
        sign = -1 if match.group(1) in "-−" else 1
        hours, minutes = int(match.group(2)), int(match.group(3) or 0)
        if hours > 14 or minutes >= 60:
            return None
        delta = sign * timedelta(hours=hours, minutes=minutes)
        canonical = f"UTC{'+' if sign > 0 else '-'}{hours:02d}:{minutes:02d}"
        return canonical, timezone(delta, canonical)
    try:
        return name, ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        pass
    # Регистр букв: «europe/moscow» -> «Europe/Moscow».
    fixed = "/".join(part[:1].upper() + part[1:] for part in name.split("/"))
    if fixed != name:
        try:
            return fixed, ZoneInfo(fixed)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            return None
    return None


@lru_cache(maxsize=512)
def resolve_tz(name: str) -> tzinfo:
    parsed = parse_tz(name)
    return parsed[1] if parsed else timezone(timedelta(hours=3), "UTC+03:00")


def local_dt(ts: int, tz_name: str) -> datetime:
    return datetime.fromtimestamp(ts, resolve_tz(tz_name))


def fmt_date(ts: int, tz_name: str, with_year: bool = False) -> str:
    return local_dt(ts, tz_name).strftime("%d.%m.%Y" if with_year else "%d.%m")


def fmt_time(ts: int, tz_name: str) -> str:
    return local_dt(ts, tz_name).strftime("%H:%M")


def days_between(ts_from: int, ts_to: int, tz_name: str) -> int:
    return (local_dt(ts_to, tz_name).date() - local_dt(ts_from, tz_name).date()).days


def ago(ts: int | None, now_ts: int, tz_name: str) -> str:
    if not ts:
        return "ещё ни разу"
    days = days_between(ts, now_ts, tz_name)
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return f"{plural(days, DAY_FORMS)} назад"


def parse_hhmm(text: str) -> time | None:
    """«20:30», «20.30», «2030», «20» -> time. None, если не похоже на время."""
    match = re.fullmatch(r"\s*(\d{1,2})(?:\s*[:.\-\s]\s*(\d{2})|(\d{2}))?\s*", text or "")
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2) or match.group(3) or 0)
    if hours > 23 or minutes > 59:
        return None
    return time(hours, minutes)


def reminder_due(user: User, now_ts: int, paused: bool = False, works_due: bool = False) -> str | None:
    """Пора ли напоминать: None | 'regular' | 'works' | 'snooze' | 'clear_snooze'.

    В выбранное время, не чаще раза в `remind_every` дней:
    * 'regular' — давно (не меньше `remind_every` дней) ничего не вносил;
    * 'works' — вносил, но остались незачтённые работы и включены напоминания о них.
    На каникулах (paused) не напоминаем вовсе.
    """
    if user.blocked or not user.onboarded or paused:
        return None
    if user.snooze_until:
        if now_ts < user.snooze_until:
            return None
        snoozed_at = user.snooze_until - SNOOZE_SECONDS
        if user.last_data_at and user.last_data_at >= snoozed_at:
            return "clear_snooze"  # пока ждали, оценки уже внесли
        return "snooze"
    if user.remind_every <= 0:
        return None
    if not in_window(user, now_ts):
        return None
    if user.last_reminded_at and days_between(user.last_reminded_at, now_ts, user.tz) < user.remind_every:
        return None
    base = user.last_data_at or user.created_at
    if days_between(base, now_ts, user.tz) >= user.remind_every:
        return "regular"
    if works_due and user.works_reminders:
        return "works"
    return None


def in_window(user: User, now_ts: int) -> bool:
    """Сейчас время напоминаний пользователя (и не воскресенье, если так выбрано)?"""
    local = local_dt(now_ts, user.tz)
    at = parse_hhmm(user.remind_time) or time(20, 0)
    start = local.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if local < start or local >= start + REMIND_WINDOW:
        return False
    return not (user.skip_sunday and local.weekday() == 6)


def semester_title(day: date) -> str:
    """Название семестра по дате: август–январь — осень, февраль–июль — весна."""
    if day.month >= 8:
        return f"Осень {day.year}"
    if day.month == 1:
        return f"Осень {day.year - 1}"
    return f"Весна {day.year}"


def remind_every_label(days: int) -> str:
    return {0: "выключены", 1: "каждый день", 2: "раз в 2 дня", 3: "раз в 3 дня"}.get(
        days, f"раз в {plural(days, DAY_FORMS)}"
    )
