"""Экраны бота: текст + inline-клавиатура. Хендлеры только выбирают, что показать."""

from __future__ import annotations

import html
import math
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import clock
from .callbacks import (
    ACCESS_REQUEST,
    AddGrade,
    AddWorks,
    Adm,
    Arch,
    CalcSet,
    CreditSet,
    DelGrade,
    ExamSet,
    Nav,
    OkrSet,
    Onb,
    Pick,
    ProjSet,
    Rem,
    ScaleSet,
    SemSet,
    Subj,
    TzSet,
    WorkAct,
    WorkGrade,
)
from .dates import fmt_day, fmt_range, in_range, phase
from .logic import (
    ADMISSION_KINDS,
    DAY_FORMS,
    DEFAULT_ICON,
    GRADE_FORMS,
    SUBJECT_FORMS,
    WORK_FORMS,
    Overview,
    SubjectView,
    ago,
    display_name,
    fmt_date,
    fmt_num,
    fmt_time,
    local_dt,
    mean,
    plural,
    progress_bar,
    remind_every_label,
    short_list,
    subject_icon,
    truncate,
)
from .models import (
    CALC_MEAN,
    CALC_SSO,
    CONTROL_ICON,
    CONTROL_LABEL,
    CONTROLS,
    CREDIT,
    DIFF,
    EXAM,
    FINAL_AVG,
    FINAL_EXAM,
    LAB,
    NONE,
    WORK_ICON,
    Semester,
    Subject,
    User,
    Work,
)
from .repo import Repo

Row = list[InlineKeyboardButton]

CONTROL_DATIVE = {EXAM: "экзамену", DIFF: "диф. зачёту", CREDIT: "зачёту"}
CONTROL_FORMS = {
    EXAM: ("экзамен", "экзамена", "экзаменов"),
    DIFF: ("диф. зачёт", "диф. зачёта", "диф. зачётов"),
    CREDIT: ("зачёт", "зачёта", "зачётов"),
}

TZ_CHOICES: list[tuple[str, str]] = [
    ("Минск", "Europe/Minsk"),
    ("Москва", "Europe/Moscow"),
    ("Киев", "Europe/Kyiv"),
    ("Калининград", "Europe/Kaliningrad"),
    ("Самара", "Europe/Samara"),
    ("Екатеринбург", "Asia/Yekaterinburg"),
    ("Омск", "Asia/Omsk"),
    ("Новосибирск", "Asia/Novosibirsk"),
    ("Красноярск", "Asia/Krasnoyarsk"),
    ("Иркутск", "Asia/Irkutsk"),
    ("Владивосток", "Asia/Vladivostok"),
    ("Алматы", "Asia/Almaty"),
    ("Ташкент", "Asia/Tashkent"),
    ("Варшава", "Europe/Warsaw"),
    ("Тбилиси", "Asia/Tbilisi"),
]

REMIND_TIMES = ["17:00", "18:00", "19:00", "20:00", "21:00", "22:00"]
EDIT_PAGE = 24
TELEGRAM_LIMIT = 4096
SETUP_TABS = ("📌 ОКР", "🎓 Экзамены", "📐 Курсовые")


@dataclass(slots=True)
class Screen:
    text: str
    kb: InlineKeyboardMarkup | None = None

    def __post_init__(self) -> None:
        self.text = fit(self.text)


def fit(text: str, limit: int = TELEGRAM_LIMIT - 96) -> str:
    """Telegram не примет сообщение длиннее 4096 символов — аккуратно обрезаем по строкам."""
    if len(text) <= limit:
        return text
    lines = text.split("\n")
    while lines and len("\n".join(lines)) > limit:
        lines.pop()
    result = "\n".join(lines)
    if result.count("<pre>") > result.count("</pre>"):
        result += "</pre>"
    return result + "\n…"


# ---------------------------------------------------------------- примеры под шкалу


@dataclass(frozen=True, slots=True)
class Examples:
    """Примеры оценок для подсказок — под шкалу пользователя (на 5-балльной «8 9» не бывает)."""

    a: int
    b: int
    c: int
    okr: int
    glued: str


def ex(user: User) -> Examples:
    return Examples(4, 5, 3, 4, "4545") if user.scale <= 5 else Examples(8, 9, 10, 7, "10910")


def tips(user: User) -> list[str]:
    e = ex(user)
    return [
        f"Пиши прямо в чат: «физ {e.a} {e.b}» — оценки сразу попадут в физику.",
        f"Отправь просто «{e.a} {e.b}» — я спрошу, в какой предмет их записать.",
        f"Можно без пробелов: «физра {e.glued}» — я сам разделю оценки.",
        "Названия можно сокращать: «матан», «англ», «оаип», а своё сокращение задать в ⚙️ предмета.",
        f"«физ окр {e.okr}» — так быстро ставится оценка за ОКР.",
        f"Оценки за весь день — одним сообщением, каждый предмет с новой строки: «физ {e.a}» ⏎ «матан {e.b}».",
        "Ошибся? Сразу после добавления есть кнопка «↩️ Отменить».",
        "В карточке предмета видно, сколько оценок нужно до следующего балла.",
        "Перед экзаменом в карточке видно, какой выйдет итог при каждой отметке.",
        "Время и частоту напоминаний можно поменять в ⚙️ Настройках.",
        "После сессии я предложу отправить семестр в 🗂 Архив.",
        "Все оценки можно выгрузить в Excel: ⚙️ → 📤 Экспорт.",
    ]


# ---------------------------------------------------------------- helpers


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def btn(text: str, data: CallbackData | str) -> InlineKeyboardButton:
    payload = data.pack() if isinstance(data, CallbackData) else data
    return InlineKeyboardButton(text=text, callback_data=payload)


def markup(rows: Sequence[Row]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows if r])


def chunks(items: Sequence[InlineKeyboardButton], size: int) -> list[Row]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def adaptive_rows(buttons: Sequence[InlineKeyboardButton], wide: int = 20, narrow: int = 10) -> list[Row]:
    """Короткие подписи — по 2–3 в ряд, длинные — по одной."""
    longest = max((len(b.text) for b in buttons), default=0)
    if longest <= narrow:
        return chunks(buttons, 3)
    if longest <= wide:
        return chunks(buttons, 2)
    return chunks(buttons, 1)


def with_notice(notice: str | None, lines: list[str]) -> str:
    head = [notice, ""] if notice else []
    return "\n".join(head + lines)


def mark(selected: bool, text: str) -> str:
    return f"✅ {text}" if selected else text


def keypad(user: User, make: type[CallbackData] | None = None, **fields: int) -> list[Row]:
    """Клавиатура оценок: 1–10 в два ряда или 2–5 в один."""
    if user.scale <= 5:
        rows = [list(range(2, user.scale + 1))]
    else:
        values = list(range(1, user.scale + 1))
        half = math.ceil(len(values) / 2)
        rows = [values[:half], values[half:]]
    factory = make or AddGrade
    return [[btn(str(v), factory(**fields, v=v)) for v in row] for row in rows]


def tz_label(name: str) -> str:
    for label, tz in TZ_CHOICES:
        if tz == name:
            return label
    return name


def grades_line(values: Sequence[int], limit: int = 30) -> str:
    shown = list(values)[-limit:]
    text = ", ".join(str(v) for v in shown)
    hidden = len(values) - len(shown)
    return f"…(+{hidden}) {text}" if hidden else text


def works_range(works: Sequence[Work]) -> str:
    """«ЛР 1–5» для подряд идущих номеров без названий, иначе перечисление."""
    if works and all(not w.title for w in works):
        nums = [w.num for w in works]
        if nums == list(range(nums[0], nums[0] + len(nums))) and len(nums) > 2:
            return f"{works[0].short.split()[0]} {nums[0]}–{nums[-1]}"
    return short_list([esc(w.short) for w in works], 5)


def subject_title(v: SubjectView) -> str:
    return f"{v.icon} {esc(v.title)}"


def today_for(user: User) -> date:
    return local_dt(clock.now(), user.tz).date()


def value_label(v: SubjectView) -> str | None:
    """Что писать рядом с предметом: отметка (итоговая, а до неё — за семестр) или «зачтено»."""
    final = v.final
    if final is not None and final.kind == "pass":
        return "зачтено"
    if final is not None and final.kind == "debt" and final.value is None:
        return "не зачтено"
    return str(v.mark) if v.mark is not None else None


def subject_button(v: SubjectView, data: CallbackData) -> InlineKeyboardButton:
    """«Стандарты · 8 ❌» — название, отметка и статус; точный средний (7,75) — внутри предмета.

    Значок — только узнанный (🦺, ⚛️…): общий 📘 ничего не говорит и съедает место на кнопке.
    """
    name = truncate(v.label, 22)
    if v.icon != DEFAULT_ICON:
        name = f"{v.icon} {name}"
    value = value_label(v)
    return btn(f"{name} · {value} {v.status_icon}" if value else f"{name} {v.status_icon}", data)


def mark_text(v: SubjectView) -> str | None:
    """«9 (8,50)» — отметка и точный средний за семестр, пока итога ещё нет."""
    value = value_label(v)
    final = v.final
    exact = v.semester_exact
    no_final = final is None or final.value is None
    if value is not None and value.isdigit() and no_final and exact is not None and exact != int(value):
        return f"{value} ({fmt_num(exact)})"
    return value


def text_width(text: str) -> float:
    """Примерная ширина подписи в «буквах»: эмодзи шире, пробелы и точки уже, заглавные чуть шире."""
    width = 0.0
    for ch in text:
        code = ord(ch)
        if code in (0xFE0F, 0x200D):
            continue
        if code >= 0x1F000 or 0x2190 <= code <= 0x2BFF:
            width += 2
        elif ch in " .,·:;'’-–—()/":
            width += 0.5
        elif ch.isupper():
            width += 1.2
        else:
            width += 1
    return width


HALF_ROW = 15.0  # столько «букв» влезает в кнопку на пол-экрана телефона, дальше Telegram режет «…»


def pack_rows(buttons: Sequence[InlineKeyboardButton], half: float = HALF_ROW) -> list[Row]:
    """По две кнопки в ряд, если обе подписи влезают целиком, иначе кнопка на весь ряд. Порядок тот же."""
    rows: list[Row] = []
    pending: InlineKeyboardButton | None = None
    for button in buttons:
        if text_width(button.text) > half:
            if pending is not None:
                rows.append([pending])
                pending = None
            rows.append([button])
        elif pending is not None:
            rows.append([pending, button])
            pending = None
        else:
            pending = button
    if pending is not None:
        rows.append([pending])
    return rows


# ---------------------------------------------------------------- главное меню


def phase_line(semester: Semester | None, today: date, previous: Semester | None = None) -> str | None:
    if semester is None:
        return None
    ph = phase(today, semester.session_start, semester.session_end, semester.holidays_start, semester.holidays_end)
    if ph.kind == "study":
        return f"🗓 До сессии {plural(ph.days, DAY_FORMS)} ({fmt_range(semester.session_start, semester.session_end)})"
    if ph.kind == "session":
        left = f"ещё {plural(ph.days, DAY_FORMS)}" if ph.days else "последний день"
        return f"📝 Идёт сессия — до {fmt_day(semester.session_end)} ({left})"
    if ph.kind == "holidays":
        return f"🏖 Каникулы до {fmt_day(ph.until)}"
    if ph.kind == "after":
        return f"✅ Сессия закончилась {fmt_day(semester.session_end)}"
    if previous is not None and in_range(today, previous.holidays_start, previous.holidays_end):
        return f"🏖 Каникулы до {fmt_day(previous.holidays_end)}"
    return None


def _session_block(ov: Overview) -> list[str]:
    session = ov.session_views
    if not session:
        return []
    counts = Counter(v.control for v in session)
    kinds = [plural(counts[c], CONTROL_FORMS[c]) for c in (EXAM, DIFF, CREDIT) if counts[c]]
    lines = ["🎓 Сессия: " + ", ".join(kinds)]
    done = ov.session_done
    if done or ov.debts:
        line = f"🏁 Сдано: {len(done)} из {len(session)}"
        if ov.session_avg is not None:
            line += f" · средний сессии {fmt_num(ov.session_avg)}"
        lines.append(line)
    else:
        admitted = sum(1 for v in session if v.admitted)
        lines.append(f"🎫 Допуск есть: {admitted} из {len(session)}")
    if ov.debts:
        lines.append(
            "🆘 Задолженности: " + ", ".join(f"{esc(v.label)} ({esc(v.final.note)})" for v in ov.debts if v.final)
        )
    if ov.session_complete and not ov.debts:
        lines.append("🎉 Все экзамены и зачёты сданы!")
    return lines


def _attestation_block(user: User, ov: Overview) -> list[str]:
    failing = [v for v in ov.views if any(p.kind in ADMISSION_KINDS for p in v.problems)]
    if not failing:
        if ov.debts:
            return []
        empty = [esc(v.label) for v in ov.views if v.semester_exact is None and v.final is None]
        if empty:
            return [f"✅ <b>Проблем нет</b> · пока без оценок: {short_list(empty, 5)}"]
        return ["✅ <b>Аттестован по всем предметам!</b>"]
    lines = [f"❌ <b>Не аттестован / нет допуска: {len(failing)} из {len(ov.views)}</b>"]
    no_okr: list[str] = []
    low_okr: list[str] = []
    works: list[str] = []
    project: list[str] = []
    semester_low: list[str] = []
    for v in failing:
        missing = [p.slot for p in v.problems if p.kind == "no_okr"]
        if missing:
            suffix = "" if v.subject.okr_count <= 1 else " (№" + ", №".join(map(str, missing)) + ")"
            no_okr.append(esc(v.label) + suffix)
        for p in v.problems:
            if p.kind == "okr_low":
                low_okr.append(f"{esc(v.label)} — {p.value}")
            elif p.kind == "works":
                works.append(f"{esc(v.label)} ({short_list([esc(w.short) for w in p.works], 3)})")
            elif p.kind == "project_missing":
                project.append(esc(v.label))
            elif p.kind == "project_low":
                project.append(f"{esc(v.label)} — {p.value}")
            elif p.kind == "semester_low":
                semester_low.append(f"{esc(v.label)} — {p.value}")
    if no_okr:
        lines.append("📌 Нет ОКР: " + ", ".join(no_okr))
    if low_okr:
        lines.append(f"📉 ОКР ниже {user.pass_mark}: " + ", ".join(low_okr))
    if works:
        lines.append("⏳ Не зачтены работы: " + "; ".join(works))
    if project:
        lines.append("📐 Курсовой не защищён: " + ", ".join(project))
    if semester_low:
        lines.append(f"📉 За семестр ниже {user.pass_mark}: " + ", ".join(semester_low))
    return lines


def _trend_line(ov: Overview) -> str | None:
    """«🗓 За неделю: +3 оценки · средний ▲0,15»."""
    week_ago = clock.now() - 7 * 86400
    fresh = ov.added_since(week_ago)
    if not fresh:
        return None
    line = f"📆 За неделю: +{plural(fresh, GRADE_FORMS)}"
    before = ov.avg_before(week_ago)
    if before is not None and ov.avg is not None and abs(ov.avg - before) >= 0.005:
        arrow = "▲" if ov.avg > before else "▼"
        line += f" · средний {arrow}{fmt_num(abs(ov.avg - before))}"
    return line


async def main_screen(repo: Repo, user: User, notice: str | None = None, top_rows: Sequence[Row] = ()) -> Screen:
    views = await repo.views(user)
    semester = await repo.get_semester(user.id, user.semester_id)
    previous = await repo.last_archived(user.id)
    ov = Overview(views)
    lines = [f"🎓 <b>{esc(semester.label) if semester else 'Мой семестр'}</b>"]
    when = phase_line(semester, today_for(user), previous)
    if when:
        lines.append(when)
    if not views:
        lines += ["", "Предметов пока нет. Нажми «➕ Предметы» и отправь их списком 👇"]
    else:
        if ov.avg is None:
            lines.append("📊 Средний балл: <b>—</b> · оценок пока нет")
        else:
            lines.append(f"📊 Средний балл: <b>{fmt_num(ov.avg)}</b>")
        counts = plural(len(views), SUBJECT_FORMS)
        if ov.grades_count:
            counts += f" · {plural(ov.grades_count, GRADE_FORMS)}"
        lines.append(f"📚 {counts}")
        trend = _trend_line(ov)
        if trend:
            lines.append(trend)
        if ov.works_total:
            lines.append(
                f"🔬 Зачтено работ: {ov.works_done} из {ov.works_total} {progress_bar(ov.works_done, ov.works_total)}"
            )
        session = _session_block(ov)
        if session:
            lines += ["", *session]
        attestation = _attestation_block(user, ov)
        if attestation:
            lines += ["", *attestation]
        lines += ["", f"<i>💡 {random.choice(tips(user))}</i>"]

    rows: list[Row] = list(top_rows)
    if ov.session_complete and not any(Arch(a="ask").pack() == b.callback_data for r in top_rows for b in r):
        rows.append([btn("🗂 Архивировать семестр", Arch(a="ask"))])
    rows += pack_rows([subject_button(v, Subj(a="v", id=v.id)) for v in views])
    rows.append([btn("➕ Предметы", Nav(to="add_subj")), btn("📊 Статистика", Nav(to="stats"))])
    rows.append([btn("🗂 Архив", Arch(a="list")), btn("⚙️ Настройки", Nav(to="settings"))])
    rows.append([btn("❓ Помощь", Nav(to="help"))])
    return Screen(with_notice(notice, lines), markup(rows))


# ---------------------------------------------------------------- знакомство


def onboarding_course(user: User, semester: Semester | None, notice: str | None = None) -> Screen:
    first = esc(user.name.split()[0]) if user.name.strip() else ""
    term = semester.term if semester and semester.term else 1
    lines = [
        f"👋 Привет{', ' + first if first else ''}! Я помогу следить за учёбой в колледже:",
        "• средний балл и отметка за семестр — по правилам ССО;",
        "• ОКР, лабораторные и практические, курсовые проекты;",
        "• допуск к экзаменам и итог по экзаменам и зачётам;",
        "• напоминания внести оценки и защитить работы.",
        "",
        "<b>Шаг 1 из 4.</b> На каком ты курсе? Семестр я угадал по дате — поправь, если нужно.",
    ]
    rows = [
        [btn(mark(term == 1, "1 семестр"), Onb(a="term", v=1)), btn(mark(term == 2, "2 семестр"), Onb(a="term", v=2))],
        [btn(f"{c} курс", Onb(a="course", v=c)) for c in (1, 2, 3, 4)],
    ]
    return Screen(with_notice(notice, lines), markup(rows))


async def onboarding_subjects(repo: Repo, user: User, notice: str | None = None) -> Screen:
    subjects = await repo.list_subjects(user.id, user.semester_id)
    step = "" if user.onboarded else "<b>Шаг 2 из 4.</b> "
    lines = [
        f"{step}Отправь список предметов этого семестра — через запятую или каждый с новой строки.",
        "",
        "Например:",
        "<code>Высшая математика, Физика, Основы алгоритмизации и программирования (ОАиП)</code>",
        "",
        "В скобках можно сразу указать сокращение — его удобно писать при быстром вводе.",
    ]
    if subjects:
        lines += ["", f"<b>Добавлено ({len(subjects)}):</b>"]
        lines += [
            f"{subject_icon(s.name)} {esc(display_name(s.name))}" + (f" ({esc(s.short)})" if s.short else "")
            for s in subjects
        ]
        lines += ["", "Можно отправить ещё или нажать «Дальше ➡️»."]
    rows: list[Row] = []
    if subjects:
        rows.append([btn("Дальше ➡️", Onb(a="setup"))])
        rows.append([btn("🗑 Очистить список", Onb(a="reset"))])
    rows.append([btn("⬅️ Назад", Onb(a="back") if not user.onboarded else Nav(to="main"))])
    return Screen(with_notice(notice, lines), markup(rows))


async def onboarding_setup(
    repo: Repo, user: User, tab: int = 0, notice: str | None = None, finish: str = "dates"
) -> Screen:
    """Вкладки ОКР / экзамены / курсовые. finish: «dates» — дальше даты сессии, «main» — в меню."""
    subjects = await repo.list_subjects(user.id, user.semester_id)
    step = "" if user.onboarded else "<b>Шаг 3 из 4.</b> "
    hints = (
        "Где есть <b>обязательная контрольная работа (ОКР)</b>? Без оценки за ОКР — «не аттестован». "
        "Если в предмете ОКР нет (например, физкультура) — выключи. Несколько ОКР — в ⚙️ предмета.",
        "Чем заканчивается предмет? Нажимай по кругу: <b>экзамен → диф. зачёт → зачёт → без экзамена</b>. "
        "Без экзамена и зачёта итог — просто отметка за семестр.",
        "Где есть <b>курсовой проект (курсовая работа)</b>? Он оценивается отдельно и нужен для допуска (от 3).",
    )
    lines = [f"{step}Что есть в предметах? Переключай вкладки и нажимай на предметы.", "", hints[tab]]
    rows: list[Row] = [[btn(mark(i == tab, title), Onb(a="tab", v=i)) for i, title in enumerate(SETUP_TABS)]]
    for s in subjects:
        name = truncate(s.short or display_name(s.name), 24)
        if tab == 0:
            label = f"{'📌' if s.okr_count else '▫️'} {name} — {'есть ОКР' if s.okr_count else 'без ОКР'}"
            rows.append([btn(label, Onb(a="tokr", v=s.id))])
        elif tab == 1:
            control = s.control if s.control in CONTROLS else NONE
            rows.append([btn(f"{CONTROL_ICON[control]} {name} — {CONTROL_LABEL[control]}", Onb(a="tctl", v=s.id))])
        else:
            label = f"{'📐' if s.project else '▫️'} {name} — {'курсовой' if s.project else 'нет'}"
            rows.append([btn(label, Onb(a="tprj", v=s.id))])
    if len(subjects) > 1:
        if tab == 0:
            rows.append([btn("📌 Во всех есть", Onb(a="bulk", v=1)), btn("▫️ Ни в одном нет", Onb(a="bulk", v=2))])
        elif tab == 1:
            rows.append([btn("▫️ Сбросить: все без экзамена", Onb(a="bulk", v=3))])
        else:
            rows.append([btn("▫️ Сбросить: курсовых нет", Onb(a="bulk", v=4))])
    if finish == "main":
        rows.append([btn("✅ Готово", Onb(a="done"))])
    else:
        rows.append([btn("Дальше ➡️", Onb(a="dates"))])
    rows.append([btn("⬅️ Назад", Onb(a="subjects"))])
    return Screen(with_notice(notice, lines), markup(rows))


def onboarding_dates(which: str, notice: str | None = None, from_settings: bool = False, step: bool = True) -> Screen:
    step_label = "<b>Шаг 4 из 4.</b> " if step and not from_settings else ""
    if which == "session":
        lines = [
            f"{step_label}Когда сессия? Отправь даты, например:",
            "<code>22.12–14.01</code> или <code>22 декабря – 14 января</code>",
            "",
            "Я покажу, сколько осталось до сессии, а когда она закончится — предложу отправить семестр в архив.",
        ]
    else:
        lines = [
            f"{step_label}А каникулы когда? Например: <code>15.01–08.02</code>",
            "",
            "На каникулах я не беспокою напоминаниями.",
        ]
    if from_settings:
        rows = [[btn("🗑 Убрать даты", SemSet(a=f"clear_{which}"))], [btn("⬅️ Назад", Nav(to="sem"))]]
    else:
        rows = [[btn("Пропустить ➡️", Onb(a="skip"))]]
    return Screen(with_notice(notice, lines), markup(rows))


# ---------------------------------------------------------------- предмет


def _okr_lines(user: User, v: SubjectView) -> list[str]:
    if v.subject.okr_count == 0:
        return []
    parts = []
    for slot, grade in v.okr_slots:
        prefix = "" if v.subject.okr_count == 1 else f"№{slot} — "
        if grade is None:
            parts.append(f"{prefix}нет оценки ❌")
        elif grade.value >= user.pass_mark:
            parts.append(f"{prefix}<b>{grade.value}</b> ✅")
        else:
            parts.append(f"{prefix}<b>{grade.value}</b> ❌ (пересдача, нужно от {user.pass_mark})")
    return ["📌 ОКР: " + " · ".join(parts)]


def _works_lines(v: SubjectView) -> list[str]:
    lines = []
    for title, works in (("🔬 Лабораторные", v.labs), ("🛠 Практические", v.pracs)):
        if not works:
            continue
        done = sum(w.passed for w in works)
        grades = [v.work_grades[w.id].value for w in works if w.id in v.work_grades]
        extra = f" · оценки: {', '.join(map(str, grades))}" if grades else ""
        lines.append(f"{title}: зачтено {done} из {len(works)} {progress_bar(done, len(works))}{extra}")
    pending = [w for w in v.open_works if not w.failed]
    failed = [w for w in v.open_works if w.failed]
    parts = []
    if pending:
        parts.append("⏳ осталось: " + short_list([esc(w.short) for w in pending], 5))
    if failed:
        parts.append("❌ не зачтено: " + short_list([esc(w.short) for w in failed], 5))
    if parts:
        lines.append(" · ".join(parts))
    return lines


def _project_line(user: User, v: SubjectView) -> str | None:
    if not v.subject.project:
        return None
    grade = v.subject.project_grade
    if grade is None:
        return "📐 Курсовой проект: не защищён ❌"
    if grade < user.pass_mark:
        return f"📐 Курсовой проект: <b>{grade}</b> ❌ (новое задание, нужно от {user.pass_mark})"
    return f"📐 Курсовой проект: <b>{grade}</b> ✅"


def _exam_lines(user: User, v: SubjectView) -> list[str]:
    control = v.control
    icon, label = CONTROL_ICON[control], CONTROL_LABEL[control].capitalize()
    if control == CREDIT:
        if v.subject.credit is None:
            return [f"{icon} {label}: ещё не сдан"]
        return [f"{icon} {label}: " + ("<b>зачтено</b> ✅" if v.subject.credit else "<b>не зачтено</b> ❌")]
    exam = v.subject.exam_grade
    if exam is None:
        lines = [f"{icon} {label}: ещё не сдан"]
        table = v.exam_table()
        if table and v.admitted:
            lines.append("    Итог при отметке: " + " · ".join(_table_cell(lo, hi, res) for lo, hi, res in table))
        if v.can_exempt:
            lines.append("    🎉 За семестр 9+ — можно получить освобождение от экзамена (п. 20 Правил).")
        return lines
    if exam < user.pass_mark:
        return [f"{icon} {label}: <b>{exam}</b> ❌ — пересдача"]
    final = v.final
    if final is not None and final.kind == "grade":
        note = f" ({esc(final.note)})" if user.final_mode == FINAL_AVG and v.semester_grade is not None else ""
        return [f"{icon} {label}: <b>{exam}</b> → итог <b>{final.value}</b>{note}"]
    return [f"{icon} {label}: <b>{exam}</b>"]


def course_line(v: SubjectView) -> str:
    """«📚 Прошлые семестры: 7 · вместе: (7 + 8) / 2 = 7,50 → 8» — предмет идёт несколько семестров."""
    past = ", ".join(map(str, v.previous))
    marks = v.course_marks
    if not marks:
        return f"📚 Прошлые семестры: {past} — сложатся с отметкой за этот семестр"
    terms = " + ".join(map(str, marks))
    together = f"({terms}) / {len(marks)} = {fmt_num(v.course_exact)} → <b>{v.base_grade}</b>"
    return f"📚 Прошлые семестры: {past} · вместе: {together}"


def _table_cell(lo: int, hi: int, result: int) -> str:
    return f"{lo}→{result}" if lo == hi else f"{lo}–{hi}→{result}"


def _goal_line(v: SubjectView) -> str | None:
    goal = v.goal
    if goal is None or not goal.options:
        return None
    variants = " или ".join(f"{count} × {grade}" for grade, count in goal.options)
    return f"📈 До «{goal.target}» за семестр не хватает: {variants}"


def status_lines(user: User, v: SubjectView) -> list[str]:
    final = v.final
    if final is not None and final.kind == "debt":
        return [
            f"🆘 <b>Задолженность:</b> {esc(final.note)} — нужна пересдача.",
            "<i>Ликвидировать — не позднее месяца после начала следующего семестра (п. 26 Правил).</i>",
        ]
    if final is not None and final.kind == "pass":
        return ["🏁 <b>Зачтено</b> ✅"]
    if final is not None and final.kind == "grade":
        if v.has_session:
            return [f"🏁 <b>Итог за семестр: {final.value}</b>"]
        return [f"✅ <b>Аттестован</b> · за семестр выходит <b>{final.value}</b>"]
    if v.has_session:
        dative = CONTROL_DATIVE[v.control]
        if v.admitted:
            return [f"🎫 <b>Допуск к {dative} есть</b> ✅"]
        reasons = v.reasons(admission_only=True) or ["нет отметок за семестр"]
        return [f"❌ <b>Нет допуска к {dative}:</b> " + "; ".join(esc(r) for r in reasons)]
    if v.ok and v.semester_exact is None:
        return ["📭 <b>Оценок пока нет</b> — отметка за семестр появится с первыми оценками"]
    if v.ok:
        return ["✅ <b>Аттестован</b>"]
    return ["❌ <b>Не аттестован:</b> " + "; ".join(esc(r) for r in v.reasons())]


def render_subject(user: User, v: SubjectView, notice: str | None, top_rows: Sequence[Row]) -> Screen:
    lines = [subject_title(v)]
    if v.has_session:
        lines.append(f"<i>{CONTROL_ICON[v.control]} В сессию: {CONTROL_LABEL[v.control]}</i>")
    elif v.previous:
        lines.append("<i>Идёт несколько семестров — итог: среднее отметок за все семестры (п. 24)</i>")
    elif v.subject.okr_count == 0:
        lines.append("<i>Без экзамена и ОКР — итог: отметка за семестр</i>")
    else:
        lines.append("<i>Без экзамена — итог: отметка за семестр</i>")
    lines.append("")
    if v.semester_exact is None:
        lines.append("📊 Оценок пока нет")
    else:
        lines.append(
            f"📊 За семестр: <b>{v.semester_grade}</b> ({fmt_num(v.semester_exact)}) · {plural(len(v.grades), GRADE_FORMS)}"
        )
        if v.formula:
            lines.append(f"🧮 {esc(v.formula)}")
    if v.previous:
        lines.append(course_line(v))
    if v.regular:
        lines.append("📝 " + grades_line([g.value for g in v.regular]))
    lines += _okr_lines(user, v)
    lines += _works_lines(v)
    project = _project_line(user, v)
    if project:
        lines.append(project)
    if v.has_session:
        lines += _exam_lines(user, v)
    goal = _goal_line(v)
    if goal and (v.final is None):
        lines.append(goal)
    lines.append("")
    lines += status_lines(user, v)
    e = ex(user)
    lines.append(f"<i>💡 Оценки можно просто отправить сообщением: {e.a} {e.b} или {e.glued}</i>")

    sid = v.id
    rows: list[Row] = list(top_rows)
    if v.subject.okr_count:
        rows.append([btn("➕ Оценки", Subj(a="add", id=sid)), btn("📌 ОКР", Subj(a="okr", id=sid))])
    else:
        rows.append([btn("➕ Оценки", Subj(a="add", id=sid))])
    rows.append([btn("🔬 + Лабораторная", Subj(a="nwl", id=sid)), btn("🛠 + Практическая", Subj(a="nwp", id=sid))])
    if v.works:
        done = sum(w.passed for w in v.works)
        rows.append([btn(f"📂 Работы · зачтено {done} из {len(v.works)}", Subj(a="works", id=sid))])
    session_row = []
    if v.has_session:
        session_row.append(
            btn(f"{CONTROL_ICON[v.control]} {CONTROL_LABEL[v.control].capitalize()}", Subj(a="exam", id=sid))
        )
    if v.subject.project:
        session_row.append(btn("📐 Курсовой", Subj(a="proj", id=sid)))
    if session_row:
        rows.append(session_row)
    tools = []
    if v.grades:
        tools.append(btn("✏️ Изменить оценки", Subj(a="edit", id=sid)))
    tools.append(btn("⚙️ Предмет", Subj(a="cfg", id=sid)))
    rows.append(tools)
    rows.append([btn("⬅️ Меню", Nav(to="main"))])
    return Screen(with_notice(notice, lines), markup(rows))


async def subject_screen(
    repo: Repo, user: User, sid: int, notice: str | None = None, top_rows: Sequence[Row] = ()
) -> Screen | None:
    v = await repo.view(user, sid)
    return render_subject(user, v, notice, top_rows) if v else None


async def add_grades_screen(
    repo: Repo,
    user: User,
    sid: int,
    added: Sequence[int] = (),
    before: float | None = None,
    notice: str | None = None,
) -> Screen | None:
    v = await repo.view(user, sid)
    if v is None:
        return None
    e = ex(user)
    lines = [f"➕ <b>Оценки: {subject_title(v)}</b>", ""]
    if added:
        lines.append("✅ Только что: " + ", ".join(map(str, added)))
        lines.append(f"📊 За семестр: {fmt_num(before)} → <b>{fmt_num(v.semester_exact)}</b>")
    else:
        lines.append(f"📊 За семестр: <b>{fmt_num(v.semester_exact)}</b> · {plural(len(v.grades), GRADE_FORMS)}")
        if v.regular:
            lines.append("📝 Последние: " + grades_line([g.value for g in v.regular], 12))
    lines += [
        "",
        "Нажимай на оценки — каждая сохраняется сразу.",
        f"Или отправь сообщением: <code>{e.a} {e.b} {e.c}</code>, <code>{e.a}, {e.b}</code> или слитно "
        f"<code>{e.glued}</code>",
    ]
    rows = keypad(user, AddGrade, s=sid)
    if added:
        rows.append([btn(f"↩️ Убрать последнюю ({added[-1]})", Subj(a="addundo", id=sid))])
    rows.append([btn("✅ Готово", Subj(a="done", id=sid))])
    return Screen(with_notice(notice, lines), markup(rows))


async def okr_screen(
    repo: Repo, user: User, sid: int, slot: int | None = None, notice: str | None = None
) -> Screen | None:
    v = await repo.view(user, sid)
    if v is None:
        return None
    count = v.subject.okr_count
    head = f"📌 <b>ОКР: {subject_title(v)}</b>"
    if count == 0:
        lines = [head, "", "В этом предмете ОКР не предусмотрена — аттестация зависит от работ и курсового."]
        rows = [
            [btn("📌 В предмете есть ОКР", Subj(a="okron", id=sid))],
            [btn("⬅️ Назад", Subj(a="v", id=sid))],
        ]
        return Screen(with_notice(notice, lines), markup(rows))

    if slot is None and count == 1:
        slot = 1
    if slot is None or not 1 <= slot <= count:
        lines = [head, ""]
        for n, grade in v.okr_slots:
            if grade is None:
                lines.append(f"№{n} — нет оценки ❌")
            else:
                lines.append(f"№{n} — <b>{grade.value}</b> {'✅' if grade.value >= user.pass_mark else '❌'}")
        lines += ["", "Выбери, какую ОКР выставить:"]
        buttons = [
            btn(f"ОКР №{n} · {grade.value if grade else '—'}", Subj(a="okr", id=sid, n=n)) for n, grade in v.okr_slots
        ]
        rows = chunks(buttons, 2) + [[btn("⬅️ Назад", Subj(a="v", id=sid))]]
        return Screen(with_notice(notice, lines), markup(rows))

    current = dict(v.okr_slots).get(slot)
    title = head if count == 1 else f"📌 <b>ОКР №{slot}: {subject_title(v)}</b>"
    lines = [
        title,
        "",
        f"Обязательная контрольная работа: без положительной отметки (от {user.pass_mark}) — «не аттестован».",
    ]
    if not v.simple:
        lines.append("В отметку за семестр ОКР входит отдельным слагаемым (п. 22 Правил).")
    else:
        lines.append("Оценка за ОКР входит в средний балл.")
    if v.open_works:
        lines.append("⚠️ К ОКР допускают, когда зачтены все лабораторные и практические.")
    lines.append("")
    if current is None:
        lines.append("Сейчас: оценки нет ❌")
    elif current.value >= user.pass_mark:
        lines.append(f"Сейчас: <b>{current.value}</b> ✅")
    else:
        lines.append(
            f"Сейчас: <b>{current.value}</b> ❌ — пересдача в течение двух недель, новая отметка окончательная."
        )
    lines.append("Выбери оценку или отправь её сообщением:")
    rows = keypad(user, OkrSet, s=sid, n=slot)
    if current is not None:
        rows.append([btn("🗑 Убрать оценку", OkrSet(s=sid, n=slot, v=0))])
    back = Subj(a="okr", id=sid) if count > 1 else Subj(a="v", id=sid)
    rows.append([btn("⬅️ Назад", back)])
    return Screen(with_notice(notice, lines), markup(rows))


async def exam_screen(repo: Repo, user: User, sid: int, notice: str | None = None) -> Screen | None:
    v = await repo.view(user, sid)
    if v is None or not v.has_session:
        return None
    control = v.control
    label = CONTROL_LABEL[control].capitalize()
    lines = [f"{CONTROL_ICON[control]} <b>{label}: {subject_title(v)}</b>", ""]
    if v.semester_exact is not None:
        lines.append(f"📊 За семестр: <b>{v.semester_grade}</b> ({fmt_num(v.semester_exact)})")
    if v.admitted:
        lines.append("🎫 Допуск: ✅ есть")
    else:
        reasons = v.reasons(admission_only=True) or ["нет отметок за семестр"]
        lines.append("🎫 Допуск: ❌ нет — " + "; ".join(esc(r) for r in reasons))

    if control == CREDIT:
        state = {None: "ещё не сдан", True: "<b>зачтено</b> ✅", False: "<b>не зачтено</b> ❌ — пересдача"}
        lines += ["", f"Сейчас: {state[v.subject.credit]}"]
        rows = [[btn("✅ Зачтено", CreditSet(s=sid, v=1)), btn("❌ Не зачтено", CreditSet(s=sid, v=0))]]
        if v.subject.credit is not None:
            rows.append([btn("🗑 Убрать отметку", CreditSet(s=sid, v=2))])
        rows.append([btn("⬅️ Назад", Subj(a="v", id=sid))])
        return Screen(with_notice(notice, lines), markup(rows))

    if user.final_mode == FINAL_AVG:
        lines.append(f"🧮 Итог = (отметка за семестр + {CONTROL_LABEL[control]}) / 2, округление по правилам: 8,5 → 9.")
        table = v.exam_table()
        if table:
            lines.append("Итог при отметке: " + " · ".join(_table_cell(lo, hi, res) for lo, hi, res in table))
    else:
        lines.append(f"🧮 Итог = отметка за {CONTROL_LABEL[control]} (п. 20 Правил).")
    if v.can_exempt:
        lines.append(
            "🎉 За семестр 9+: по п. 20 Правил преподаватель может освободить от экзамена — тогда жми «Автомат»."
        )
    exam = v.subject.exam_grade
    lines.append("")
    if exam is None:
        lines.append("Сейчас: ещё не сдан")
    elif exam < user.pass_mark:
        lines.append(f"Сейчас: <b>{exam}</b> ❌ — академическая задолженность, нужна пересдача")
    else:
        final = v.final
        lines.append(f"Сейчас: <b>{exam}</b> ✅" + (f" → итог <b>{final.value}</b>" if final and final.value else ""))
    lines.append("Выбери отметку или отправь её сообщением:")
    rows = keypad(user, ExamSet, s=sid)
    if v.can_exempt:
        rows.append([btn(f"🎉 Автомат (= {v.semester_grade})", ExamSet(s=sid, v=99))])
    if exam is not None:
        rows.append([btn("🗑 Убрать отметку", ExamSet(s=sid, v=0))])
    rows.append([btn("⬅️ Назад", Subj(a="v", id=sid))])
    return Screen(with_notice(notice, lines), markup(rows))


async def project_screen(repo: Repo, user: User, sid: int, notice: str | None = None) -> Screen | None:
    v = await repo.view(user, sid)
    if v is None or not v.subject.project:
        return None
    lines = [
        f"📐 <b>Курсовой проект: {subject_title(v)}</b>",
        "",
        f"Оценивается по 10-балльной шкале; для допуска к экзамену и диф. зачёту нужно от {user.pass_mark}.",
    ]
    if not v.simple:
        lines.append("В отметку за семестр входит отдельным слагаемым (п. 22 Правил).")
    lines.append(f"Ниже {user.pass_mark} — дают новое задание и срок; не сдал — «не аттестован».")
    grade = v.subject.project_grade
    lines.append("")
    if grade is None:
        lines.append("Сейчас: не защищён ❌")
    elif grade < user.pass_mark:
        lines.append(f"Сейчас: <b>{grade}</b> ❌ — нужно переделать")
    else:
        lines.append(f"Сейчас: защищён на <b>{grade}</b> ✅")
    lines.append("Выбери отметку или отправь её сообщением:")
    rows = keypad(user, ProjSet, s=sid)
    if grade is not None:
        rows.append([btn("🗑 Убрать отметку", ProjSet(s=sid, v=0))])
    rows.append([btn("⬅️ Назад", Subj(a="v", id=sid))])
    return Screen(with_notice(notice, lines), markup(rows))


async def edit_grades_screen(
    repo: Repo, user: User, sid: int, page: int = 0, notice: str | None = None, top_rows: Sequence[Row] = ()
) -> Screen | None:
    v = await repo.view(user, sid)
    if v is None:
        return None
    works = {w.id: w for w in v.works}
    grades = list(reversed(v.grades))
    pages = max(1, math.ceil(len(grades) / EDIT_PAGE))
    page = min(max(page, 0), pages - 1)
    chunk = grades[page * EDIT_PAGE : (page + 1) * EDIT_PAGE]
    lines = [f"✏️ <b>Изменить оценки: {subject_title(v)}</b>", ""]
    if grades:
        lines.append("Нажми на оценку, чтобы удалить её. Сначала — новые.")
        if any(g.is_okr or g.is_work for g in grades):
            lines.append("📌 — ОКР, 🔬/🛠 — оценка за работу.")
    else:
        lines.append("Оценок нет.")

    def marker(g) -> str:
        if g.is_okr:
            return "📌"
        if g.work_id in works:
            return WORK_ICON[works[g.work_id].kind]
        return ""

    buttons = [
        btn(f"{marker(g)}{g.value} · {fmt_date(g.created_at, user.tz)}", DelGrade(id=g.id, s=sid, p=page))
        for g in chunk
    ]
    rows: list[Row] = list(top_rows) + chunks(buttons, 3)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(btn("◀️", Subj(a="edit", id=sid, n=page - 1)))
        nav.append(btn(f"{page + 1} / {pages}", Nav(to="noop")))
        if page < pages - 1:
            nav.append(btn("▶️", Subj(a="edit", id=sid, n=page + 1)))
        rows.append(nav)
    rows.append([btn("⬅️ Назад", Subj(a="v", id=sid))])
    return Screen(with_notice(notice, lines), markup(rows))


async def subject_settings_screen(repo: Repo, user: User, sid: int, notice: str | None = None) -> Screen | None:
    v = await repo.view(user, sid)
    if v is None:
        return None
    s = v.subject
    subjects = await repo.list_subjects(user.id, user.semester_id)
    index = next((i for i, x in enumerate(subjects) if x.id == sid), 0)
    control = v.control
    kind = "общеобразовательный — простое среднее" if s.general else "профессиональный — по п. 22 Правил"
    lines = [
        f"⚙️ <b>{subject_title(v)}</b>",
        "",
        f"{CONTROL_ICON[control]} В сессию: <b>{CONTROL_LABEL[control]}</b>",
        f"📌 ОКР: <b>{s.okr_count if s.okr_count else 'нет'}</b>",
        f"📐 Курсовой проект: <b>{'есть' if s.project else 'нет'}</b>",
        f"🏷 Сокращение: <b>{esc(s.short) if s.short else 'нет'}</b>",
        f"📚 Прошлые семестры: <b>{', '.join(map(str, v.previous)) if v.previous else 'нет — первый семестр'}</b>",
    ]
    if user.calc_mode == CALC_SSO:
        lines.append(f"📚 Предмет: {kind}")
    lines += [
        f"📍 Место в списке: {index + 1} из {len(subjects)}",
        "",
        "<i>Своя иконка: поставь эмодзи в начало названия, например «🧮 Матан».</i>",
    ]
    control_buttons = [
        btn(mark(control == c, CONTROL_LABEL[c].capitalize()), Subj(a="ctl", id=sid, n=i))
        for i, c in enumerate(CONTROLS)
    ]
    rows = chunks(control_buttons, 2)
    rows.append(
        [
            btn(mark(s.okr_count == n, "нет ОКР" if n == 0 else f"ОКР: {n}"), Subj(a="okrn", id=sid, n=n))
            for n in range(0, 4)
        ]
    )
    rows.append([btn(f"📐 Курсовой: {'есть ✅' if s.project else 'нет'}", Subj(a="prj", id=sid))])
    rows.append([btn("🏷 Сокращение", Subj(a="short", id=sid)), btn("✏️ Переименовать", Subj(a="ren", id=sid))])
    rows.append([btn("📚 Отметки за прошлые семестры", Subj(a="prev", id=sid))])
    if user.calc_mode == CALC_SSO:
        rows.append([btn(f"📚 Общеобразовательный: {'да ✅' if s.general else 'нет'}", Subj(a="gen", id=sid))])
    rows.append([btn("⬆️ Выше", Subj(a="up", id=sid)), btn("⬇️ Ниже", Subj(a="down", id=sid))])
    rows.append([btn("🗑 Удалить предмет", Subj(a="del", id=sid))])
    rows.append([btn("⬅️ Назад", Subj(a="v", id=sid))])
    return Screen(with_notice(notice, lines), markup(rows))


async def delete_subject_screen(repo: Repo, user: User, sid: int) -> Screen | None:
    v = await repo.view(user, sid)
    if v is None:
        return None
    lines = [
        f"🗑 <b>Удалить «{esc(v.name)}»?</b>",
        "",
        f"Вместе с ним удалятся {plural(len(v.grades), GRADE_FORMS)} и {plural(len(v.works), WORK_FORMS)}. "
        "Это нельзя отменить.",
    ]
    rows = [[btn("🗑 Да, удалить", Subj(a="del_yes", id=sid)), btn("⬅️ Отмена", Subj(a="cfg", id=sid))]]
    return Screen("\n".join(lines), markup(rows))


# ---------------------------------------------------------------- работы


def _work_buttons(v: SubjectView, works: Sequence[Work]) -> list[Row]:
    buttons = []
    for w in works:
        grade = v.work_grades.get(w.id)
        suffix = f" · {grade.value}" if grade else ""
        buttons.append(btn(f"{w.status_icon} {w.short}{suffix}", WorkAct(a="v", id=w.id)))
    return adaptive_rows(buttons, wide=18, narrow=9)


async def works_screen(
    repo: Repo, user: User, sid: int, notice: str | None = None, top_rows: Sequence[Row] = ()
) -> Screen | None:
    v = await repo.view(user, sid)
    if v is None:
        return None
    lines = [f"📂 <b>Работы: {subject_title(v)}</b>", ""]
    for title, works in (("🔬 Лабораторные", v.labs), ("🛠 Практические", v.pracs)):
        if works:
            done = sum(w.passed for w in works)
            lines.append(f"{title}: зачтено {done} из {len(works)} {progress_bar(done, len(works))}")
    if v.works:
        lines += [
            "",
            "Нажми на работу, чтобы отметить «зачтено» и поставить оценку.",
            "✅ зачтено · ❌ не зачтено · ⏳ ещё нет",
        ]
    else:
        lines.append("Работ пока нет — добавь их кнопками ниже.")
    rows: list[Row] = list(top_rows)
    rows += _work_buttons(v, v.labs)
    rows += _work_buttons(v, v.pracs)
    rows.append([btn("🔬 + ЛР", Subj(a="nwl", id=sid, n=1)), btn("🛠 + ПР", Subj(a="nwp", id=sid, n=1))])
    rows.append([btn("⬅️ К предмету", Subj(a="v", id=sid))])
    return Screen(with_notice(notice, lines), markup(rows))


async def add_works_screen(
    repo: Repo, user: User, sid: int, kind: str, from_list: bool = False, notice: str | None = None
) -> Screen | None:
    v = await repo.view(user, sid)
    if v is None:
        return None
    existing = v.labs if kind == LAB else v.pracs
    word = "лабораторные" if kind == LAB else "практические"
    short = "ЛР" if kind == LAB else "ПР"
    next_num = max((w.num for w in existing), default=0) + 1
    lines = [f"{WORK_ICON[kind]} <b>Новые {word}: {subject_title(v)}</b>", ""]
    lines.append(f"Сейчас: {len(existing)} — {works_range(existing)}" if existing else "Пока нет ни одной.")
    lines += [
        "",
        "Сколько добавить? Номера проставлю сам.",
        "Или отправь названия через запятую:",
        "<code>Закон Ома, Мостовая схема</code>",
    ]
    back = 1 if from_list else 0
    rows = [[btn(f"➕ 1 · {short} №{next_num}", AddWorks(s=sid, k=kind, c=1, b=back))]]
    rows.append([btn(f"+{c}", AddWorks(s=sid, k=kind, c=c, b=back)) for c in (2, 3, 4, 5)])
    rows.append([btn(f"+{c}", AddWorks(s=sid, k=kind, c=c, b=back)) for c in (6, 8, 10, 12)])
    rows.append([btn("⬅️ Назад", Subj(a="works", id=sid) if from_list else Subj(a="v", id=sid))])
    return Screen(with_notice(notice, lines), markup(rows))


async def work_screen(repo: Repo, user: User, wid: int, notice: str | None = None) -> Screen | None:
    work = await repo.get_work(user, wid)
    if work is None:
        return None
    v = await repo.view(user, work.subject_id)
    if v is None:
        return None
    grade = v.work_grades.get(work.id)
    lines = [f"{work.icon} <b>{esc(work.full)}</b>", subject_title(v), ""]
    if work.passed and work.defended_at:
        lines.append(f"Статус: ✅ зачтено {fmt_date(work.defended_at, user.tz)}")
    elif work.failed:
        lines.append("Статус: ❌ не зачтено — пересдай и отметь «Зачтено»")
    else:
        lines.append("Статус: ⏳ ещё не сдавал")
    lines.append(f"Оценка: <b>{grade.value}</b> (идёт в средний)" if grade else "Оценка: нет")
    lines.append(f"Добавлена: {fmt_date(work.created_at, user.tz)}")
    back = btn("⬅️ Назад", Subj(a="works", id=v.id))
    if work.passed:
        rows = [
            [btn(f"🔢 Оценка: {grade.value}" if grade else "🔢 Поставить оценку", WorkAct(a="grade", id=wid)),
             btn("↩️ Снять зачёт", WorkAct(a="unpass", id=wid))],
            [back],
        ]  # fmt: skip
    else:
        second = (
            btn("↩️ Сбросить", WorkAct(a="unpass", id=wid))
            if work.failed
            else btn("❌ Не зачтено", WorkAct(a="fail", id=wid))
        )
        rows = [[btn("✅ Зачтено", WorkAct(a="pass", id=wid)), second], [back]]
    rows.append([btn("✏️ Название", WorkAct(a="ren", id=wid)), btn("🗑 Удалить", WorkAct(a="del", id=wid))])
    return Screen(with_notice(notice, lines), markup(rows))


async def work_grade_screen(repo: Repo, user: User, wid: int, notice: str | None = None) -> Screen | None:
    work = await repo.get_work(user, wid)
    if work is None:
        return None
    v = await repo.view(user, work.subject_id)
    if v is None:
        return None
    grade = v.work_grades.get(work.id)
    lines = [
        f"🔢 <b>Оценка за «{esc(work.full)}»</b>",
        subject_title(v),
        "",
        "Поставили оценку? Она пойдёт в средний балл предмета.",
        "Если только «зачтено» без оценки — жми «Без оценки».",
        "",
        f"Сейчас: <b>{grade.value}</b>" if grade else "Сейчас: без оценки",
    ]
    rows = keypad(user, WorkGrade, w=wid)
    if grade:
        rows.append([btn("🗑 Убрать оценку", WorkGrade(w=wid, v=0))])
    rows.append([btn("Без оценки ➡️", Subj(a="works", id=v.id))])
    return Screen(with_notice(notice, lines), markup(rows))


async def delete_work_screen(repo: Repo, user: User, wid: int) -> Screen | None:
    work = await repo.get_work(user, wid)
    if work is None:
        return None
    lines = [f"🗑 <b>Удалить «{esc(work.full)}»?</b>", "", "Отметка о зачёте и оценка за работу тоже пропадут."]
    rows = [[btn("🗑 Да, удалить", WorkAct(a="del_yes", id=wid)), btn("⬅️ Отмена", WorkAct(a="v", id=wid))]]
    return Screen("\n".join(lines), markup(rows))


# ---------------------------------------------------------------- быстрый ввод


def _subject_pick_label(s: Subject) -> str:
    return f"{subject_icon(s.name)} {truncate(s.short or display_name(s.name), 26)}"


def pick_screen(subjects: Sequence[Subject], values: Sequence[int], okr: bool, notice: str | None = None) -> Screen:
    if okr:
        what = f"оценку за ОКР {values[0]}"
    else:
        what = ("оценку " if len(values) == 1 else "оценки ") + ", ".join(map(str, values))
    lines = [f"🤔 <b>Куда записать {what}?</b>", "", "Выбери предмет:"]
    payload = "-".join(map(str, values))
    buttons = [btn(_subject_pick_label(s), Pick(s=s.id, v=payload, o=int(okr))) for s in subjects]
    rows = adaptive_rows(buttons, wide=18, narrow=0) + [[btn("✖️ Отмена", Nav(to="main"))]]
    return Screen(with_notice(notice, lines), markup(rows))


def open_pick_screen(subjects: Sequence[Subject], query: str) -> Screen:
    lines = [f"🔎 По запросу «{esc(query)}» нашлось несколько предметов:"]
    buttons = [btn(_subject_pick_label(s), Subj(a="v", id=s.id)) for s in subjects]
    rows = adaptive_rows(buttons, wide=18, narrow=0) + [[btn("✖️ Отмена", Nav(to="main"))]]
    return Screen("\n".join(lines), markup(rows))


def prompt_screen(title: str, body: str, back: CallbackData, notice: str | None = None) -> Screen:
    return Screen(with_notice(notice, [title, "", body]), markup([[btn("⬅️ Отмена", back)]]))


# ---------------------------------------------------------------- статистика и помощь


async def stats_screen(repo: Repo, user: User) -> Screen:
    views = await repo.views(user)
    ov = Overview(views)
    now = clock.now()
    week = ov.added_since(now - 7 * 86400)
    lines = ["📊 <b>Статистика</b>", ""]
    if not ov.grades_count:
        lines.append("Пока нет оценок — статистика появится, как только добавишь первые.")
    else:
        marks = ov.marks
        lines.append(f"Средний балл: <b>{fmt_num(ov.avg)}</b> = {sum(marks)} / {len(marks)}")
        lines.append("<i>Как в ведомости: среднее отметок по предметам — итоговых, а до сессии — за семестр.</i>")
        lines.append(f"Без округления: {fmt_num(ov.avg_exact)} · по всем оценкам подряд: {fmt_num(ov.avg_all)}")
        lines.append(f"Оценок: {ov.grades_count} · за неделю: +{week}")
    if ov.session_avg is not None:
        lines.append(f"Средний балл сессии (экзамены и диф. зачёты): <b>{fmt_num(ov.session_avg)}</b>")
    if ov.works_total:
        pct = round(ov.works_done / ov.works_total * 100)
        lines.append(
            f"Работы: зачтено {ov.works_done} из {ov.works_total} {progress_bar(ov.works_done, ov.works_total)} {pct}%"
        )
    if views:
        ok = len(views) - len(ov.failing)
        lines.append(f"Без проблем: {ok} из {len(views)}" + (" 🎉" if ok == len(views) else ""))

    counts = Counter(value for v in views for value in v.values)
    if counts:
        top = max(counts.values())
        bars = [
            f"{grade:>2} {'█' * max(1, round(n / top * 12)):<12} {n}"
            for grade, n in sorted(counts.items(), reverse=True)
        ]
        lines += ["", "<b>Распределение оценок</b>", "<pre>" + "\n".join(bars) + "</pre>"]

    ranked = sorted(
        (v for v in views if v.semester_exact is not None), key=lambda v: v.semester_exact or 0, reverse=True
    )
    if ranked:
        medals = ["🥇", "🥈", "🥉"]
        lines += ["", "<b>Рейтинг предметов</b> (за семестр)"]
        for i, v in enumerate(ranked):
            place = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{place} {v.icon} {esc(v.label)} — {fmt_num(v.semester_exact)}")
    empty = [esc(v.label) for v in views if v.semester_exact is None]
    if empty:
        lines.append("Без оценок: " + ", ".join(empty))
    return Screen("\n".join(lines), markup([[btn("⬅️ Меню", Nav(to="main"))]]))


def help_screen(user: User, is_owner: bool = False) -> Screen:
    e = ex(user)
    reminders = f"{remind_every_label(user.remind_every)} в {user.remind_time}" if user.remind_every else "выключены"
    lines = [
        "❓ <b>Как пользоваться</b>",
        "",
        "<b>Команды</b>",
        "/menu — главное меню",
        "/stats — статистика",
        "/archive — архив семестров",
        "/settings — настройки",
        "/help — эта справка",
    ]
    if is_owner:
        lines.append("/apanel — админ-панель (видишь только ты)")
    lines += [
        "",
        "<b>Быстрый ввод</b> — просто пиши в чат:",
        f"• <code>физ {e.a} {e.b}</code> — оценки по физике; можно слитно: <code>физ {e.glued}</code>",
        f"• <code>{e.a} {e.b}</code> — спрошу, в какой предмет",
        f"• <code>физ окр {e.okr}</code> — оценка за ОКР",
        "• <code>физ</code> — открыть предмет",
        f"• несколько предметов — каждый с новой строки: <code>физра {e.glued}</code> ⏎ <code>физ {e.a}</code>",
        "Названия можно сокращать: «матан», «англ», «оаип», или задать своё сокращение (ТОЭ).",
        "",
        "<b>Как считается</b> (Правила аттестации ССО, пост. Минобразования № 282)",
        f"• положительные отметки — от {user.pass_mark}; округление: 8,5 → 9, 8,4 → 8;",
        "• за семестр — среднее из ОКР, курсового и среднего текущих (п. 22)"
        if user.calc_mode == CALC_SSO
        else "• за семестр — среднее всех отметок;",
        "• итог с экзаменом — (семестровая + экзамен) / 2"
        if user.final_mode == FINAL_AVG
        else "• итог — отметка за экзамен (п. 20);",
        "• средний балл — среднее отметок по предметам (итоговых, до сессии — за семестр), как в ведомости;",
        f"• допуск к экзамену: за семестр от {user.pass_mark}, курсовой от {user.pass_mark}, все ЛР и ПР зачтены;",
        "• не аттестован — если не сданы ОКР, работы или курсовой.",
        "",
        f"<b>Напоминания</b>: {reminders}; на каникулах — тишина.",
        "<b>Отмена</b>: после добавления появляется кнопка «↩️ Отменить».",
        "",
        "<i>🔒 Данные хранятся в базе бота у его владельца.</i>",
    ]
    return Screen("\n".join(lines), markup([[btn("⬅️ Меню", Nav(to="main"))]]))


# ---------------------------------------------------------------- настройки


async def settings_screen(repo: Repo, user: User, notice: str | None = None, is_owner: bool = False) -> Screen:
    now = clock.now()
    semester = await repo.get_semester(user.id, user.semester_id)
    reminders = f"{remind_every_label(user.remind_every)} в {user.remind_time}" if user.remind_every else "выключены"
    calc = "по правилам ССО" if user.calc_mode == CALC_SSO else "простое среднее"
    final = "(семестр + экзамен) / 2" if user.final_mode == FINAL_AVG else "отметка за экзамен"
    lines = ["⚙️ <b>Настройки</b>", ""]
    if semester:
        lines.append(
            f"🎓 {esc(semester.label)} · сессия {fmt_range(semester.session_start, semester.session_end)}"
            f" · каникулы {fmt_range(semester.holidays_start, semester.holidays_end)}"
        )
    lines += [
        f"🔔 Напоминания: {reminders} · о работах: {'вкл' if user.works_reminders else 'выкл'}",
        f"🧮 За семестр: {calc} · итог: {final}",
        f"🌍 Часовой пояс: {esc(tz_label(user.tz))} · сейчас {fmt_time(now, user.tz)}",
        f"🔢 Шкала: {user.scale}-балльная · сдано от {user.pass_mark}",
        "🧹 Чистый чат: " + ("вкл — убираю твои сообщения, в чате одно меню" if user.clean_chat else "выкл"),
    ]
    rows = [
        [btn("🎓 Семестр и сессия", Nav(to="sem")), btn("🔔 Напоминания", Nav(to="rem"))],
        [btn("🧮 Как считать", Nav(to="calc")), btn("🌍 Часовой пояс", Nav(to="tz"))],
        [
            btn("🔢 Шкала и порог", Nav(to="scale")),
            btn(f"🧹 Чистый чат: {'вкл ✅' if user.clean_chat else 'выкл'}", Nav(to="clean")),
        ],
        [btn("📤 Экспорт в Excel", Nav(to="export")), btn("🗑 Удалить всё", Nav(to="wipe"))],
    ]
    if is_owner:
        rows.append([btn("🛡 Админ-панель", Adm(a="main"))])
    rows.append([btn("⬅️ Меню", Nav(to="main"))])
    return Screen(with_notice(notice, lines), markup(rows))


def reminders_screen(user: User, notice: str | None = None) -> Screen:
    now = clock.now()
    lines = ["🔔 <b>Напоминания</b>", ""]
    if user.remind_every:
        lines.append(f"Сейчас: <b>{remind_every_label(user.remind_every)}</b> в <b>{user.remind_time}</b>")
    else:
        lines.append("Сейчас: <b>выключены</b>")
    lines += [
        f"Твоё время: {fmt_time(now, user.tz)} ({esc(tz_label(user.tz))})",
        "",
        "Напоминаю внести оценки, только если ты давно ничего не вносил — без спама.",
        "О незачтённых работах напомню, даже если оценки вносишь (можно выключить).",
        "На каникулах не беспокою.",
    ]
    if user.skip_sunday:
        lines.append("По воскресеньям не беспокою.")
    every = user.remind_every
    rows = [
        [btn(mark(every == 0, "🔕 Выкл"), Rem(a="every", v=0)), btn(mark(every == 1, "Каждый день"), Rem(a="every", v=1))],
        [btn(mark(every == 2, "Раз в 2 дня"), Rem(a="every", v=2)), btn(mark(every == 3, "Раз в 3 дня"), Rem(a="every", v=3))],
        *chunks([btn(mark(t == user.remind_time, t), Rem(a="time", v=int(t[:2]) * 60 + int(t[3:]))) for t in REMIND_TIMES], 3),
        [btn("⌨️ Другое время", Rem(a="custom"))],
        [btn(f"🔬 Про незачтённые работы: {'да ✅' if user.works_reminders else 'нет'}", Rem(a="works"))],
        [btn(f"🗓 Не беспокоить в воскресенье: {'да ✅' if user.skip_sunday else 'нет'}", Rem(a="sun"))],
        [btn("🔔 Прислать пример", Rem(a="test"))],
        [btn("⬅️ Назад", Nav(to="settings"))],
    ]  # fmt: skip
    return Screen(with_notice(notice, lines), markup(rows))


def calc_screen(user: User, notice: str | None = None) -> Screen:
    lines = [
        "🧮 <b>Как считать</b>",
        "",
        "<b>Отметка за семестр</b>",
        "• <b>По правилам ССО</b> (п. 22): среднее из отметок за ОКР, курсовой и среднего всех остальных "
        "отметок. Так у предметов профессионального компонента.",
        "• <b>Простое среднее</b> всех отметок вместе с ОКР.",
        "",
        "<b>Итог по экзамену и диф. зачёту</b>",
        "• <b>(семестровая + экзамен) / 2</b>: 7 и 10 → 8,5 → 9.",
        "• <b>Только экзамен</b>: по п. 20 Правил отметка за экзамен окончательная за семестр.",
        "",
        "<i>Если в колледже считают иначе — выбери, как у тебя.</i>",
    ]
    rows = [
        [btn(mark(user.calc_mode == CALC_SSO, "По правилам ССО"), CalcSet(a="sem", v=0)),
         btn(mark(user.calc_mode == CALC_MEAN, "Простое среднее"), CalcSet(a="sem", v=1))],
        [btn(mark(user.final_mode == FINAL_AVG, "(семестр + экзамен)/2"), CalcSet(a="fin", v=0)),
         btn(mark(user.final_mode == FINAL_EXAM, "Только экзамен"), CalcSet(a="fin", v=1))],
        [btn("⬅️ Назад", Nav(to="settings"))],
    ]  # fmt: skip
    return Screen(with_notice(notice, lines), markup(rows))


async def semester_settings_screen(repo: Repo, user: User, notice: str | None = None) -> Screen:
    semester = await repo.get_semester(user.id, user.semester_id)
    course = semester.course if semester else 1
    term = semester.term if semester else 1
    lines = [
        "🎓 <b>Семестр и сессия</b>",
        "",
        f"Сейчас: <b>{esc(semester.label) if semester else '—'}</b>",
        f"📝 Сессия: {fmt_range(semester.session_start, semester.session_end) if semester else 'не указаны'}",
        f"🏖 Каникулы: {fmt_range(semester.holidays_start, semester.holidays_end) if semester else 'не указаны'}",
        f"🗂 После сессии спросить про архив: {'да' if semester and semester.archive_ask else 'нет'}",
    ]
    rows = [
        [btn(mark(course == c, f"{c} курс"), SemSet(a="course", v=c)) for c in (1, 2, 3, 4)],
        [btn(mark(term == t, f"{t} семестр"), SemSet(a="term", v=t)) for t in (1, 2)],
        [btn("✏️ Даты сессии", SemSet(a="session")), btn("✏️ Даты каникул", SemSet(a="holidays"))],
        [btn(f"🗂 Спрашивать про архив: {'да ✅' if semester and semester.archive_ask else 'нет'}", SemSet(a="ask"))],
        [btn("🗂 Отправить семестр в архив", Arch(a="ask"))],
        [btn("⬅️ Назад", Nav(to="settings"))],
    ]
    return Screen(with_notice(notice, lines), markup(rows))


def tz_screen(user: User, notice: str | None = None) -> Screen:
    now = clock.now()
    lines = [
        "🌍 <b>Часовой пояс</b>",
        "",
        f"Сейчас: <b>{esc(tz_label(user.tz))}</b> — у тебя {fmt_time(now, user.tz)}",
        "От пояса зависит время напоминаний.",
        "",
        "Выбери город или отправь название пояса (<code>Europe/Moscow</code>) или смещение от UTC (<code>+3</code>).",
    ]
    buttons = [btn(mark(tz == user.tz, label), TzSet(i=i)) for i, (label, tz) in enumerate(TZ_CHOICES)]
    rows = chunks(buttons, 3) + [[btn("⬅️ Назад", Nav(to="settings"))]]
    return Screen(with_notice(notice, lines), markup(rows))


def scale_screen(user: User, notice: str | None = None) -> Screen:
    lines = [
        "🔢 <b>Шкала оценок</b>",
        "",
        f"Шкала: <b>{user.scale}-балльная</b>",
        f"Положительные отметки — от <b>{user.pass_mark}</b> (в ССО — от 3, п. 4 Правил).",
        "Ниже порога: ОКР и курсовой не сданы, нет допуска, экзамен — задолженность.",
    ]
    rows = [
        [btn(mark(user.scale == 10, "10-балльная"), ScaleSet(a="set", v=10)), btn(mark(user.scale == 5, "5-балльная"), ScaleSet(a="set", v=5))],
        [btn("➖", ScaleSet(a="pass", v=-1)), btn(f"Сдано от: {user.pass_mark}", Nav(to="noop")), btn("➕", ScaleSet(a="pass", v=1))],
        [btn("⬅️ Назад", Nav(to="settings"))],
    ]  # fmt: skip
    return Screen(with_notice(notice, lines), markup(rows))


def wipe_screen() -> Screen:
    lines = [
        "🗑 <b>Удалить все данные?</b>",
        "",
        "Пропадут все семестры, предметы, оценки и работы. Это нельзя отменить.",
        "",
        "<i>Совет: перед удалением можно сделать 📤 Экспорт.</i>",
    ]
    rows = [[btn("🗑 Да, удалить всё", Nav(to="wipe_yes"))], [btn("⬅️ Отмена", Nav(to="settings"))]]
    return Screen("\n".join(lines), markup(rows))


# ---------------------------------------------------------------- архив


def final_line(v: SubjectView) -> str:
    final = v.final
    if final is None:
        value = fmt_num(v.semester_exact) if v.semester_exact is not None else "без оценок"
        if v.has_session:
            value += f" · {CONTROL_LABEL[v.control]} не сдан"
    elif final.kind == "grade":
        value = f"<b>{final.value}</b>"
        if v.has_session and v.subject.exam_grade is not None:
            value += f" ({CONTROL_LABEL[v.control]} {v.subject.exam_grade})"
    elif final.kind == "pass":
        value = "зачтено ✅"
    elif final.kind == "debt":
        value = f"🆘 {esc(final.note)}"
    else:
        value = "не аттестован ❌"
    line = f"{v.icon} {esc(v.label)} — {value}"
    if v.subject.project and v.subject.project_grade is not None:
        line += f" · курсовой {v.subject.project_grade}"
    return line


def live_line(v: SubjectView) -> str:
    """Предмет текущего семестра одной строкой: «⚛️ Физика — 9 (8,50) ❌ (нет оценки за ОКР)»."""
    value = mark_text(v)
    line = f"{v.icon} {esc(v.label)}" + (f" — {value}" if value else "") + f" {v.status_icon}"
    if v.problems:
        line += f" ({esc('; '.join(v.reasons()))})"
    return line


def summary_lines(ov: Overview) -> list[str]:
    lines = []
    if ov.avg is not None:
        lines.append(f"📊 Средний балл: <b>{fmt_num(ov.avg)}</b> = {sum(ov.marks)} / {len(ov.marks)}")
    if ov.session_avg is not None:
        lines.append(f"🎓 Средний сессии: <b>{fmt_num(ov.session_avg)}</b>")
    if ov.debts:
        lines.append("🆘 Задолженности: " + ", ".join(esc(v.label) for v in ov.debts))
    return lines


async def diploma_avg(repo: Repo, user: User) -> tuple[float | None, int]:
    """Средний за всё обучение — по отметкам предметов всех семестров в архиве (как в приложении к диплому).

    Текущий семестр не считается: он ещё не закончен, итоговых отметок в нём почти нет.
    """
    marks: list[int] = []
    semesters = 0
    for semester in await repo.list_semesters(user.id):
        if not semester.archived_at:
            continue
        semester_marks = Overview(await repo.views(user, semester.id)).marks
        if semester_marks:
            semesters += 1
            marks.extend(semester_marks)
    return mean(marks), semesters


async def archive_screen(repo: Repo, user: User, notice: str | None = None) -> Screen:
    semesters = await repo.list_semesters(user.id)
    current = next((s for s in semesters if s.id == user.semester_id), None)
    archived = [s for s in semesters if s.archived_at]
    ov = Overview(await repo.views(user))
    lines = ["🗂 <b>Архив</b>", ""]
    if current:
        lines.append(
            f"Сейчас: <b>{esc(current.label)}</b> — {plural(len(ov.views), SUBJECT_FORMS)}, средний {fmt_num(ov.avg)}"
        )
    total, count = await diploma_avg(repo, user)
    if total is not None:
        lines.append(f"🎓 Средний за всё обучение: <b>{fmt_num(total)}</b> (семестров в архиве: {count})")
    lines.append("")
    if archived:
        lines.append("Нажми на семестр, чтобы посмотреть итоги:")
    else:
        lines.append("Архив пуст. После сессии я предложу отправить семестр сюда — со всеми оценками и итогами.")
    rows: list[Row] = []
    for semester in archived:
        avg = Overview(await repo.views(user, semester.id)).avg
        rows.append([btn(f"📁 {semester.label} · {fmt_num(avg)}", Arch(a="view", id=semester.id))])
    if ov.views:
        rows.append([btn("🗂 Отправить текущий семестр в архив", Arch(a="ask"))])
    rows.append([btn("⬅️ Меню", Nav(to="main"))])
    return Screen(with_notice(notice, lines), markup(rows))


async def archive_view_screen(repo: Repo, user: User, sem_id: int) -> Screen | None:
    semester = await repo.get_semester(user.id, sem_id)
    if semester is None:
        return None
    ov = Overview(await repo.views(user, sem_id))
    period = fmt_date(semester.created_at, user.tz, with_year=True)
    if semester.archived_at:
        period += " — " + fmt_date(semester.archived_at, user.tz, with_year=True)
    lines = [f"📁 <b>{esc(semester.label)}</b> · архив", period, "", *summary_lines(ov)]
    if ov.views:
        lines += ["", "<b>Итоги</b>"]
        lines += [final_line(v) for v in ov.views]
    else:
        lines.append("Предметов не было.")
    return Screen("\n".join(lines), markup([[btn("⬅️ Архив", Arch(a="list"))]]))


async def archive_confirm_screen(repo: Repo, user: User) -> Screen:
    semester = await repo.get_semester(user.id, user.semester_id)
    ov = Overview(await repo.views(user))
    title = esc(semester.label) if semester else "семестр"
    lines = [f"🗂 <b>Отправить «{title}» в архив?</b>", "", *summary_lines(ov)]
    if ov.views:
        lines += ["", *[final_line(v) for v in ov.views]]
    if ov.debts:
        lines += ["", "⚠️ Есть задолженности — если пересдашь, лучше сначала внести отметку."]
    lines += ["", "Потом начнётся следующий семестр: введёшь новые предметы или перенесёшь список."]
    rows = [[btn("✅ Да, в архив", Arch(a="yes")), btn("⬅️ Отмена", Nav(to="main"))]]
    return Screen("\n".join(lines), markup(rows))


def after_archive_screen(old_label: str, new: Semester, previous_id: int, previous_count: int) -> Screen:
    lines = [
        f"🎉 «{esc(old_label)}» теперь в архиве — его можно посмотреть в 🗂 Архив.",
        "",
        f"Начат <b>{esc(new.label)}</b>. Предметы обычно другие — введи их, когда будет расписание.",
    ]
    rows: list[Row] = [[btn("✏️ Ввести предметы", Arch(a="fresh"))]]
    if previous_count:
        rows.append([btn(f"📋 Перенести список ({previous_count})", Arch(a="copy", id=previous_id))])
    rows.append([btn("⏰ Позже — в меню", Nav(to="main"))])
    return Screen("\n".join(lines), markup(rows))


def archive_prompt(semester: Semester, ov: Overview) -> Screen:
    lines = [f"🎓 <b>Сессия закончилась!</b> «{esc(semester.label)}»"]
    session = ov.session_views
    if session:
        lines.append(f"🏁 Сдано: {len(ov.session_done)} из {len(session)}")
    lines += summary_lines(ov)
    lines += ["", "Отправить семестр в архив? Его можно будет посмотреть в 🗂 Архив."]
    rows = [
        [btn("✅ Да, в архив", Arch(a="yes")), btn("⏳ Спросить позже", Arch(a="later"))],
        [btn("🙅 Не спрашивать", Arch(a="never"))],
    ]
    return Screen("\n".join(lines), markup(rows))


# ---------------------------------------------------------------- доступ и админка


def deny_screen() -> Screen:
    lines = [
        "🔒 <b>Это личный бот</b>",
        "",
        "Пользоваться можно по приглашению владельца. Нажми кнопку — он получит заявку.",
    ]
    return Screen("\n".join(lines), markup([[btn("📨 Попросить доступ", ACCESS_REQUEST)]]))


def request_card(user_id: int, name: str, username: str | None) -> Screen:
    who = esc(name or "Без имени") + (f" (@{esc(username)})" if username else "")
    lines = ["📨 <b>Заявка на доступ</b>", "", f"{who}", f"id: <code>{user_id}</code>"]
    rows = [[btn("✅ Пустить", Adm(a="ok", id=user_id)), btn("❌ Отказать", Adm(a="no", id=user_id))]]
    return Screen("\n".join(lines), markup(rows))


def who(user: User) -> str:
    name = esc(user.name or "Без имени")
    return f"{name} (@{esc(user.username)})" if user.username else name


async def admin_screen(repo: Repo, is_open: bool, notice: str | None = None) -> Screen:
    users = await repo.all_users()
    week_ago = clock.now() - 7 * 86400
    active = sum(1 for u in users if (u.last_data_at or 0) >= week_ago)
    requests = await repo.requests()
    lines = [
        "🛡 <b>Админ-панель</b>",
        "",
        f"👥 Пользователей: {len(users)} · вносили оценки за неделю: {active}",
        f"📨 Заявок: {len(requests)}",
        "🔐 Доступ: " + ("открыт для всех" if is_open else "только приглашённые"),
        "",
        "<i>Просмотр результатов — тихий: пользователь ничего не узнает.</i>",
    ]
    rows = [
        [btn("➕ Добавить по юзернейму", Adm(a="add"))],
        [btn("👥 Пользователи", Adm(a="users")), btn(f"📨 Заявки ({len(requests)})", Adm(a="requests"))],
        [btn("🔒 Закрыть для всех, кроме приглашённых" if is_open else "🔓 Открыть для всех", Adm(a="open"))],
        [btn("⬅️ Меню", Nav(to="main"))],
    ]
    return Screen(with_notice(notice, lines), markup(rows))


ADMIN_PAGE = 12


async def admin_users_screen(repo: Repo, owner_id: int | None, page: int = 0, notice: str | None = None) -> Screen:
    users = [u for u in await repo.all_users() if u.id != owner_id]
    access = await repo.access_list()
    known = {u.id for u in users} | {u.username for u in users if u.username}
    waiting = [r for r in access if r["user_id"] not in known and r["username"] not in known]
    pages = max(1, math.ceil(len(users) / ADMIN_PAGE))
    page = min(max(page, 0), pages - 1)
    lines = ["👥 <b>Пользователи</b>", ""]
    if not users and not waiting:
        lines.append("Пока никого. Добавь кого-нибудь по юзернейму.")
    if waiting:
        lines.append(
            "⏳ Приглашены, но ещё не заходили: "
            + ", ".join(f"@{esc(r['username'])}" if r["username"] else str(r["user_id"]) for r in waiting)
        )
    rows: list[Row] = []
    for u in users[page * ADMIN_PAGE : (page + 1) * ADMIN_PAGE]:
        rows.append(
            [
                btn(
                    f"👤 {truncate(u.name or 'Без имени', 18)}" + (f" @{u.username}" if u.username else ""),
                    Adm(a="user", id=u.id),
                )
            ]
        )
    for r in waiting:
        label = f"🗑 @{r['username']}" if r["username"] else f"🗑 id {r['user_id']}"
        rows.append([btn(f"{label} — убрать приглашение", Adm(a="unlist", id=r["id"]))])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(btn("◀️", Adm(a="users", p=page - 1)))
        nav.append(btn(f"{page + 1} / {pages}", Nav(to="noop")))
        if page < pages - 1:
            nav.append(btn("▶️", Adm(a="users", p=page + 1)))
        rows.append(nav)
    rows.append([btn("⬅️ Админ-панель", Adm(a="main"))])
    return Screen(with_notice(notice, lines), markup(rows))


async def admin_user_screen(repo: Repo, target: User, has_access: bool, notice: str | None = None) -> Screen:
    now = clock.now()
    semester = await repo.get_semester(target.id, target.semester_id)
    ov = Overview(await repo.views(target))
    lines = [
        f"👤 <b>{who(target)}</b>",
        f"id: <code>{target.id}</code> · вносил данные: {ago(target.last_data_at, now, target.tz)}",
        "",
    ]
    if not target.onboarded:
        lines.append("Ещё не закончил знакомство с ботом.")
    if semester:
        lines.append(f"🎓 {esc(semester.label)}")
    if ov.views:
        lines.append(f"📊 Средний балл: <b>{fmt_num(ov.avg)}</b> · оценок: {ov.grades_count}")
        if ov.works_total:
            lines.append(f"🔬 Зачтено работ: {ov.works_done} из {ov.works_total}")
        lines += ["", *[live_line(v) for v in ov.views]]
    total, count = await diploma_avg(repo, target)
    if total is not None:
        lines += ["", f"🎓 Средний за всё обучение: {fmt_num(total)} (семестров в архиве: {count})"]
    rows: list[Row] = []
    if has_access:
        rows.append([btn("🔒 Закрыть доступ", Adm(a="revoke", id=target.id))])
    rows.append([btn("⬅️ Пользователи", Adm(a="users"))])
    return Screen(with_notice(notice, lines), markup(rows))


async def admin_requests_screen(repo: Repo, notice: str | None = None) -> Screen:
    requests = await repo.requests()
    lines = ["📨 <b>Заявки на доступ</b>", ""]
    if not requests:
        lines.append("Заявок нет.")
    rows: list[Row] = []
    for r in requests:
        name = truncate(r["name"] or "Без имени", 18) + (f" @{r['username']}" if r["username"] else "")
        rows.append([btn(f"✅ {name}", Adm(a="ok", id=r["user_id"])), btn("❌", Adm(a="no", id=r["user_id"]))])
    rows.append([btn("⬅️ Админ-панель", Adm(a="main"))])
    return Screen(with_notice(notice, lines), markup(rows))
