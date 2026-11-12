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

from .models import LAB, PRAC, Grade, Subject, User, Work

# ---------------------------------------------------------------- ограничения

MAX_SUBJECTS = 40
MAX_NAME_LEN = 60
MAX_WORKS_PER_SUBJECT = 60
MAX_BATCH = 20  # оценок за один раз

SCALES: dict[int, int] = {10: 4, 5: 3}  # шкала -> порог сдачи ОКР по умолчанию

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
    return "📘"


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


def parse_grades(text: str, low: int, high: int) -> tuple[list[int], list[str]]:
    """«8, 9/10 7» -> ([8, 9, 10, 7], []). Неверные кусочки возвращаются отдельно."""
    values: list[int] = []
    bad: list[str] = []
    for token in _GRADE_SEPARATORS.split(text.strip()):
        token = token.strip(".")
        if not token:
            continue
        if _DIGITS.fullmatch(token) and low <= int(token) <= high:
            values.append(int(token))
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


def next_goal(values: Sequence[int], scale: int, limit: int = 25) -> Goal | None:
    if not values:
        return None
    rounded = round_half_up(sum(values) / len(values))
    if rounded >= scale:
        return None
    target = rounded + 1
    threshold = target - 0.5

    best = need_count(values, threshold, scale)
    if best is None or best > limit:
        return None
    options: list[tuple[int, int]] = []
    # Самая низкая оценка, которой хватит за минимальное число попыток.
    for grade in range(math.floor(threshold) + 1, scale + 1):
        if need_count(values, threshold, grade) == best:
            options.append((grade, best))
            break
    # Вариант «чуть больше оценок, но пониже».
    if options and options[0][0] > math.floor(threshold) + 1:
        for grade in range(math.floor(threshold) + 1, options[0][0]):
            k = need_count(values, threshold, grade)
            if k is not None and k <= min(best + 2, limit):
                options.append((grade, k))
                break
    return Goal(target=target, options=options)


@dataclass(slots=True)
class Problem:
    kind: str  # "no_okr" | "okr_low" | "pending"
    slot: int = 0
    value: int = 0
    works: list[Work] = field(default_factory=list)


@dataclass
class SubjectView:
    """Всё, что нужно знать о предмете для экрана: средний, ОКР, работы, аттестация."""

    subject: Subject
    grades: list[Grade]
    works: list[Work]
    pass_mark: int
    scale: int

    @property
    def id(self) -> int:
        return self.subject.id

    @cached_property
    def icon(self) -> str:
        return subject_icon(self.subject.name)

    @cached_property
    def name(self) -> str:
        return display_name(self.subject.name)

    @cached_property
    def values(self) -> list[int]:
        return [g.value for g in self.grades]

    @cached_property
    def avg(self) -> float | None:
        return mean(self.values)

    @cached_property
    def regular(self) -> list[Grade]:
        return [g for g in self.grades if not g.is_okr]

    @cached_property
    def okr_slots(self) -> list[tuple[int, Grade | None]]:
        by_slot = {g.okr_no: g for g in self.grades if g.is_okr}
        return [(slot, by_slot.get(slot)) for slot in range(1, self.subject.okr_count + 1)]

    @cached_property
    def labs(self) -> list[Work]:
        return sorted((w for w in self.works if w.kind == LAB), key=lambda w: (w.num, w.id))

    @cached_property
    def pracs(self) -> list[Work]:
        return sorted((w for w in self.works if w.kind == PRAC), key=lambda w: (w.num, w.id))

    @cached_property
    def pending(self) -> list[Work]:
        return [w for w in self.labs + self.pracs if not w.defended]

    @cached_property
    def problems(self) -> list[Problem]:
        result: list[Problem] = []
        for slot, grade in self.okr_slots:
            if grade is None:
                result.append(Problem("no_okr", slot=slot))
            elif grade.value < self.pass_mark:
                result.append(Problem("okr_low", slot=slot, value=grade.value))
        if self.pending:
            result.append(Problem("pending", works=self.pending))
        return result

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def avg_below_pass(self) -> bool:
        return self.avg is not None and self.avg < self.pass_mark

    @cached_property
    def goal(self) -> Goal | None:
        return next_goal(self.values, self.scale)

    def okr_label(self, slot: int) -> str:
        return "ОКР" if self.subject.okr_count <= 1 else f"ОКР №{slot}"

    def reasons(self) -> list[str]:
        """Человеческие причины неаттестации: «нет ОКР», «не защищены ЛР 3, ЛР 4»."""
        lines = []
        for p in self.problems:
            if p.kind == "no_okr":
                lines.append(f"нет оценки за {self.okr_label(p.slot)}")
            elif p.kind == "okr_low":
                lines.append(f"{self.okr_label(p.slot)} на {p.value} (нужно от {self.pass_mark})")
            elif p.kind == "pending":
                lines.append("не защищены " + short_list([w.short for w in p.works]))
        return lines


@dataclass
class Overview:
    views: list[SubjectView]

    @cached_property
    def avg(self) -> float | None:
        """Средний балл = среднее по средним предметов (у каждого предмета равный вес)."""
        return mean(v.avg for v in self.views if v.avg is not None)

    @cached_property
    def avg_all(self) -> float | None:
        """Среднее по всем оценкам подряд."""
        return mean(value for v in self.views for value in v.values)

    @property
    def grades_count(self) -> int:
        return sum(len(v.grades) for v in self.views)

    @property
    def works_total(self) -> int:
        return sum(len(v.works) for v in self.views)

    @property
    def works_done(self) -> int:
        return sum(1 for v in self.views for w in v.works if w.defended)

    @property
    def failing(self) -> list[SubjectView]:
        return [v for v in self.views if not v.ok]


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


def reminder_due(user: User, now_ts: int) -> str | None:
    """Пора ли напоминать: None | 'regular' | 'snooze' | 'clear_snooze'.

    Обычное напоминание приходит в выбранное время, если с последнего внесения
    данных и с последнего напоминания прошло не меньше `remind_every` дней.
    """
    if user.blocked or not user.onboarded:
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

    local = local_dt(now_ts, user.tz)
    at = parse_hhmm(user.remind_time) or time(20, 0)
    start = local.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if local < start or local >= start + REMIND_WINDOW:
        return None
    if user.skip_sunday and local.weekday() == 6:
        return None
    if user.last_reminded_at and days_between(user.last_reminded_at, now_ts, user.tz) < user.remind_every:
        return None
    base = user.last_data_at or user.created_at
    if days_between(base, now_ts, user.tz) < user.remind_every:
        return None
    return "regular"


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
