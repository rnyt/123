"""Экраны бота: текст + inline-клавиатура. Хендлеры только выбирают, что показать."""

from __future__ import annotations

import html
import math
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import clock
from .callbacks import (
    AddGrade,
    AddWorks,
    DelGrade,
    Nav,
    OkrSet,
    Onb,
    Pick,
    Rem,
    ScaleSet,
    Sem,
    Subj,
    TzSet,
    WorkAct,
)
from .logic import (
    GRADE_FORMS,
    SUBJECT_FORMS,
    WORK_FORMS,
    Overview,
    SubjectView,
    display_name,
    fmt_date,
    fmt_num,
    fmt_time,
    plural,
    progress_bar,
    remind_every_label,
    round_half_up,
    short_list,
    subject_icon,
    truncate,
)
from .models import LAB, WORK_ICON, Subject, User, Work
from .repo import Repo

Row = list[InlineKeyboardButton]


@dataclass(frozen=True, slots=True)
class Examples:
    """Примеры оценок для подсказок — под шкалу пользователя (на 5-балльной «8 9» не бывает)."""

    a: int
    b: int
    c: int
    okr: int


def ex(user: User) -> Examples:
    return Examples(4, 5, 3, 4) if user.scale <= 5 else Examples(8, 9, 10, 7)


def tips(user: User) -> list[str]:
    e = ex(user)
    return [
        f"Пиши прямо в чат: «физ {e.a} {e.b}» — оценки сразу попадут в физику.",
        f"Отправь просто «{e.a} {e.b}» — я спрошу, в какой предмет их записать.",
        "Названия можно сокращать: «матан», «англ», «оаип» — я пойму.",
        f"«физ окр {e.okr}» — так быстро ставится оценка за ОКР.",
        f"Оценки за весь день — одним сообщением: «физ {e.a}, матан {e.b}».",
        "Ошибся? Сразу после добавления есть кнопка «↩️ Отменить».",
        "В карточке предмета видно, сколько оценок нужно до следующего балла.",
        "Время и частоту напоминаний можно поменять в ⚙️ Настройках.",
        "Закончился семестр? ⚙️ → 🗂 Семестры: старый уйдёт в архив.",
        "Своя иконка предмета: поставь эмодзи в начало названия — «🧮 Матан».",
        "Все оценки можно выгрузить в Excel: ⚙️ → 📤 Экспорт.",
    ]


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


@dataclass(slots=True)
class Screen:
    text: str
    kb: InlineKeyboardMarkup | None = None


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
        values = list(range(2, user.scale + 1))
        rows = [values]
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
    return f"{v.icon} {esc(v.name)}"


# ---------------------------------------------------------------- главное меню


def _attestation_block(user: User, ov: Overview) -> list[str]:
    failing = ov.failing
    if not failing:
        return ["✅ <b>Аттестован по всем предметам!</b>"]
    lines = [f"❌ <b>Не аттестован: {len(failing)} из {len(ov.views)}</b>"]
    no_okr: list[str] = []
    low: list[str] = []
    pending: list[str] = []
    for v in failing:
        missing = [p.slot for p in v.problems if p.kind == "no_okr"]
        if missing:
            suffix = "" if v.subject.okr_count <= 1 else " (№" + ", №".join(map(str, missing)) + ")"
            no_okr.append(esc(v.name) + suffix)
        for p in v.problems:
            if p.kind == "okr_low":
                low.append(f"{esc(v.name)} — {p.value}")
            elif p.kind == "pending":
                pending.append(f"{esc(v.name)} ({short_list([esc(w.short) for w in p.works], 3)})")
    if no_okr:
        lines.append("📌 Нет ОКР: " + ", ".join(no_okr))
    if low:
        lines.append(f"📉 ОКР ниже {user.pass_mark}: " + ", ".join(low))
    if pending:
        lines.append("⏳ Не защищены: " + "; ".join(pending))
    return lines


def _trend_line(ov: Overview) -> str | None:
    """«📈 За неделю: +3 оценки · средний ▲0,15»."""
    week_ago = clock.now() - 7 * 86400
    fresh = ov.added_since(week_ago)
    if not fresh:
        return None
    line = f"🗓 За неделю: +{plural(fresh, GRADE_FORMS)}"
    before = ov.avg_before(week_ago)
    if before is not None and ov.avg is not None and abs(ov.avg - before) >= 0.005:
        arrow = "▲" if ov.avg > before else "▼"
        line += f" · средний {arrow}{fmt_num(abs(ov.avg - before))}"
    return line


def subject_button(v: SubjectView, data: CallbackData) -> InlineKeyboardButton:
    status = "✅" if v.ok else "❌"
    return btn(f"{v.icon} {truncate(v.name, 22)} · {fmt_num(v.avg)} {status}", data)


async def main_screen(repo: Repo, user: User, notice: str | None = None, top_rows: Sequence[Row] = ()) -> Screen:
    views = await repo.views(user)
    semester = await repo.get_semester(user.id, user.semester_id)
    ov = Overview(views)
    lines = [f"🎓 <b>{esc(semester.title) if semester else 'Мой семестр'}</b>"]
    if not views:
        lines += ["", "Предметов пока нет. Нажми «➕ Предметы» и отправь их списком 👇"]
    else:
        if ov.avg is None:
            lines.append("📊 Средний балл: <b>—</b> · оценок пока нет")
        else:
            lines.append(f"📊 Средний балл: <b>{fmt_num(ov.avg)}</b> ≈ {round_half_up(ov.avg)}")
        lines.append(f"📚 {plural(len(views), SUBJECT_FORMS)} · {plural(ov.grades_count, GRADE_FORMS)}")
        trend = _trend_line(ov)
        if trend:
            lines.append(trend)
        if ov.works_total:
            lines.append(
                f"🔬 Защищено работ: {ov.works_done} из {ov.works_total} {progress_bar(ov.works_done, ov.works_total)}"
            )
        lines.append("")
        lines += _attestation_block(user, ov)
        lines += ["", f"<i>💡 {random.choice(tips(user))}</i>"]

    rows: list[Row] = list(top_rows)
    rows += adaptive_rows([subject_button(v, Subj(a="v", id=v.id)) for v in views], wide=24, narrow=0)
    rows.append([btn("➕ Предметы", Nav(to="add_subj")), btn("📊 Статистика", Nav(to="stats"))])
    rows.append([btn("⚙️ Настройки", Nav(to="settings")), btn("❓ Помощь", Nav(to="help"))])
    return Screen(with_notice(notice, lines), markup(rows))


# ---------------------------------------------------------------- знакомство


def onboarding_scale(user: User, notice: str | None = None) -> Screen:
    first = esc(user.name.split()[0]) if user.name.strip() else ""
    lines = [
        f"👋 Привет{', ' + first if first else ''}! Я помогу следить за учёбой:",
        "• средний балл по каждому предмету и общий;",
        "• обязательные контрольные (ОКР) и защита лабораторных и практических;",
        "• аттестация — сразу видно, где проблемы;",
        "• напоминания внести новые оценки.",
        "",
        "<b>Шаг 1 из 3.</b> Какая у тебя шкала оценок?",
    ]
    rows = [
        [btn("🔟 10-балльная (1–10)", Onb(a="scale", v=10))],
        [btn("5️⃣ 5-балльная (2–5)", Onb(a="scale", v=5))],
    ]
    return Screen(with_notice(notice, lines), markup(rows))


async def onboarding_subjects(repo: Repo, user: User, notice: str | None = None) -> Screen:
    subjects = await repo.list_subjects(user.id, user.semester_id)
    step = "" if user.onboarded else "<b>Шаг 2 из 3.</b> "
    lines = [
        f"{step}Отправь список своих предметов — через запятую или каждый с новой строки.",
        "",
        "Например:",
        "<code>Высшая математика, Физика, Программирование, Английский язык, История</code>",
    ]
    if subjects:
        lines += ["", f"<b>Добавлено ({len(subjects)}):</b>"]
        lines += [f"{subject_icon(s.name)} {esc(display_name(s.name))}" for s in subjects]
        lines += ["", "Можно отправить ещё или нажать «Дальше ➡️»."]
    rows: list[Row] = []
    if subjects:
        rows.append([btn("Дальше ➡️", Onb(a="next"))])
        rows.append([btn("🗑 Очистить список", Onb(a="reset"))])
    if not user.onboarded:
        rows.append([btn("⬅️ Назад", Onb(a="back"))])
    else:
        rows.append([btn("⬅️ Меню", Nav(to="main"))])
    return Screen(with_notice(notice, lines), markup(rows))


async def onboarding_okr(repo: Repo, user: User, notice: str | None = None) -> Screen:
    subjects = await repo.list_subjects(user.id, user.semester_id)
    step = "" if user.onboarded else "<b>Шаг 3 из 3.</b> "
    lines = [
        f"{step}В каких предметах есть <b>обязательная контрольная работа (ОКР)</b>?",
        "",
        "Без оценки за ОКР — не аттестован, поэтому я буду за этим следить.",
        "Нажимай на предметы, чтобы переключить. Если ОКР несколько — это настраивается в ⚙️ предмета.",
    ]
    rows = [
        [
            btn(
                f"{'📌' if s.okr_count else '▫️'} {truncate(display_name(s.name), 24)} — "
                f"{'есть ОКР' if s.okr_count else 'без ОКР'}",
                Onb(a="okr", v=s.id),
            )
        ]
        for s in subjects
    ]
    rows.append([btn("✅ Готово", Onb(a="done"))])
    rows.append([btn("⬅️ Назад", Onb(a="subjects"))])
    return Screen(with_notice(notice, lines), markup(rows))


# ---------------------------------------------------------------- предмет


def _okr_lines(user: User, v: SubjectView) -> list[str]:
    if v.subject.okr_count == 0:
        return ["📌 ОКР: не предусмотрена"]
    parts = []
    for slot, grade in v.okr_slots:
        prefix = "" if v.subject.okr_count == 1 else f"№{slot} — "
        if grade is None:
            parts.append(f"{prefix}нет оценки ❌")
        elif grade.value >= user.pass_mark:
            parts.append(f"{prefix}<b>{grade.value}</b> ✅")
        else:
            parts.append(f"{prefix}<b>{grade.value}</b> ❌ (нужно от {user.pass_mark})")
    return ["📌 ОКР: " + " · ".join(parts)]


def _goal_line(v: SubjectView) -> str | None:
    goal = v.goal
    if goal is None or not goal.options:
        return None
    variants = " или ".join(f"{count} × {grade}" for grade, count in goal.options)
    return f"📈 До «{goal.target}» не хватает: {variants}"


def render_subject(user: User, v: SubjectView, notice: str | None, top_rows: Sequence[Row]) -> Screen:
    lines = [f"{subject_title(v)}", ""]
    if v.avg is None:
        lines.append("📊 Оценок пока нет")
    else:
        lines.append(
            f"📊 Средний: <b>{fmt_num(v.avg)}</b> ≈ {round_half_up(v.avg)} · {plural(len(v.grades), GRADE_FORMS)}"
        )
    if v.regular:
        lines.append("📝 " + grades_line([g.value for g in v.regular]))
    lines += _okr_lines(user, v)
    if v.labs:
        done = sum(w.defended for w in v.labs)
        lines.append(f"🔬 Лабораторные: {done} из {len(v.labs)} {progress_bar(done, len(v.labs))}")
    if v.pracs:
        done = sum(w.defended for w in v.pracs)
        lines.append(f"🛠 Практические: {done} из {len(v.pracs)} {progress_bar(done, len(v.pracs))}")
    if not v.works:
        lines.append("🔬 Лабораторных и практических пока нет")
    if v.pending:
        lines.append("⏳ Не защищены: " + short_list([esc(w.short) for w in v.pending], 5))
    goal = _goal_line(v)
    if goal:
        lines.append(goal)
    lines.append("")
    if v.ok:
        lines.append("✅ <b>Аттестован</b>")
    else:
        lines.append("❌ <b>Не аттестован:</b> " + "; ".join(esc(r) for r in v.reasons()))
    if v.avg_below_pass:
        lines.append(f"⚠️ Средний ниже {user.pass_mark} — стоит подтянуть")
    e = ex(user)
    lines.append(f"<i>💡 Оценки можно просто отправить сообщением: {e.a} {e.b}</i>")

    sid = v.id
    rows: list[Row] = list(top_rows)
    rows.append([btn("➕ Оценки", Subj(a="add", id=sid)), btn("📌 ОКР", Subj(a="okr", id=sid))])
    rows.append([btn("🔬 + Лабораторная", Subj(a="nwl", id=sid)), btn("🛠 + Практическая", Subj(a="nwp", id=sid))])
    if v.works:
        done = sum(w.defended for w in v.works)
        rows.append([btn(f"📂 Работы · защищено {done} из {len(v.works)}", Subj(a="works", id=sid))])
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
        lines.append(f"📊 Средний: {fmt_num(before)} → <b>{fmt_num(v.avg)}</b>")
    else:
        lines.append(f"📊 Средний: <b>{fmt_num(v.avg)}</b> · {plural(len(v.grades), GRADE_FORMS)}")
        if v.regular:
            lines.append("📝 Последние: " + grades_line([g.value for g in v.regular], 12))
    lines += [
        "",
        "Нажимай на оценки — каждая сохраняется сразу.",
        f"Или отправь сообщением: <code>{e.a} {e.b} {e.c}</code>, <code>{e.a}, {e.b}</code> или <code>{e.a}/{e.b}</code>",
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
        lines = [
            head,
            "",
            "В этом предмете ОКР не предусмотрена — аттестация зависит только от защиты работ.",
        ]
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
                ok = "✅" if grade.value >= user.pass_mark else "❌"
                lines.append(f"№{n} — <b>{grade.value}</b> {ok}")
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
        f"Обязательная контрольная работа: без оценки от {user.pass_mark} — не аттестован.",
        "Оценка за ОКР входит в средний балл.",
        "",
    ]
    if current is None:
        lines.append("Сейчас: оценки нет ❌")
    else:
        ok = "✅" if current.value >= user.pass_mark else f"❌ (нужно от {user.pass_mark})"
        lines.append(f"Сейчас: <b>{current.value}</b> {ok}")
    lines.append("Выбери оценку или отправь её сообщением:")
    rows = keypad(user, OkrSet, s=sid, n=slot)
    if current is not None:
        rows.append([btn("🗑 Убрать оценку", OkrSet(s=sid, n=slot, v=0))])
    back = Subj(a="okr", id=sid) if count > 1 else Subj(a="v", id=sid)
    rows.append([btn("⬅️ Назад", back)])
    return Screen(with_notice(notice, lines), markup(rows))


async def edit_grades_screen(
    repo: Repo,
    user: User,
    sid: int,
    page: int = 0,
    notice: str | None = None,
    top_rows: Sequence[Row] = (),
) -> Screen | None:
    v = await repo.view(user, sid)
    if v is None:
        return None
    grades = list(reversed(v.grades))
    pages = max(1, math.ceil(len(grades) / EDIT_PAGE))
    page = min(max(page, 0), pages - 1)
    chunk = grades[page * EDIT_PAGE : (page + 1) * EDIT_PAGE]
    lines = [f"✏️ <b>Изменить оценки: {subject_title(v)}</b>", ""]
    if grades:
        lines.append("Нажми на оценку, чтобы удалить её. Сначала — новые.")
        if any(g.is_okr for g in grades):
            lines.append("📌 — оценка за ОКР.")
    else:
        lines.append("Оценок нет.")
    buttons = [
        btn(
            f"{'📌' if g.is_okr else ''}{g.value} · {fmt_date(g.created_at, user.tz)}",
            DelGrade(id=g.id, s=sid, p=page),
        )
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
    subjects = await repo.list_subjects(user.id, user.semester_id)
    index = next((i for i, s in enumerate(subjects) if s.id == sid), 0)
    count = v.subject.okr_count
    lines = [
        f"⚙️ <b>{subject_title(v)}</b>",
        "",
        f"📌 ОКР в предмете: <b>{count if count else 'нет'}</b>",
        f"📍 Место в списке: {index + 1} из {len(subjects)}",
        "",
        "<i>Своя иконка: поставь эмодзи в начало названия, например «🧮 Матан».</i>",
    ]
    rows = [
        [btn("✏️ Переименовать", Subj(a="ren", id=sid))],
        [btn(mark(count == n, "нет ОКР" if n == 0 else f"ОКР: {n}"), Subj(a="okrn", id=sid, n=n)) for n in range(0, 4)],
        [btn("⬆️ Выше", Subj(a="up", id=sid)), btn("⬇️ Ниже", Subj(a="down", id=sid))],
        [btn("🗑 Удалить предмет", Subj(a="del", id=sid))],
        [btn("⬅️ Назад", Subj(a="v", id=sid))],
    ]
    return Screen(with_notice(notice, lines), markup(rows))


async def delete_subject_screen(repo: Repo, user: User, sid: int) -> Screen | None:
    v = await repo.view(user, sid)
    if v is None:
        return None
    lines = [
        f"🗑 <b>Удалить «{esc(v.name)}»?</b>",
        "",
        f"Вместе с ним удалятся {plural(len(v.grades), GRADE_FORMS)} и "
        f"{plural(len(v.works), WORK_FORMS)}. Это нельзя отменить.",
    ]
    rows = [[btn("🗑 Да, удалить", Subj(a="del_yes", id=sid)), btn("⬅️ Отмена", Subj(a="cfg", id=sid))]]
    return Screen("\n".join(lines), markup(rows))


# ---------------------------------------------------------------- работы


def _work_buttons(works: Sequence[Work]) -> list[Row]:
    buttons = [btn(f"{'✅' if w.defended else '⏳'} {w.short}", WorkAct(a="v", id=w.id)) for w in works]
    return adaptive_rows(buttons, wide=18, narrow=9)


async def works_screen(
    repo: Repo, user: User, sid: int, notice: str | None = None, top_rows: Sequence[Row] = ()
) -> Screen | None:
    v = await repo.view(user, sid)
    if v is None:
        return None
    lines = [f"📂 <b>Работы: {subject_title(v)}</b>", ""]
    if v.labs:
        done = sum(w.defended for w in v.labs)
        lines.append(f"🔬 Лабораторные: {done} из {len(v.labs)} {progress_bar(done, len(v.labs))}")
    if v.pracs:
        done = sum(w.defended for w in v.pracs)
        lines.append(f"🛠 Практические: {done} из {len(v.pracs)} {progress_bar(done, len(v.pracs))}")
    if v.works:
        lines += ["", "Нажми на работу, чтобы отметить защиту.", "✅ — защищена, ⏳ — ещё нет."]
    else:
        lines.append("Работ пока нет — добавь их кнопками ниже.")
    rows: list[Row] = list(top_rows)
    rows += _work_buttons(v.labs)
    rows += _work_buttons(v.pracs)
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
    if existing:
        lines.append(f"Сейчас: {len(existing)} — {works_range(existing)}")
    else:
        lines.append("Пока нет ни одной.")
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
    lines = [f"{work.icon} <b>{esc(work.full)}</b>", subject_title(v), ""]
    if work.defended and work.defended_at:
        lines.append(f"Статус: ✅ защищена {fmt_date(work.defended_at, user.tz)}")
    else:
        lines.append("Статус: ⏳ не защищена")
    lines.append(f"Добавлена: {fmt_date(work.created_at, user.tz)}")
    back = btn("⬅️ Назад", Subj(a="works", id=v.id))
    if work.defended:
        first = [btn("↩️ Не защищена", WorkAct(a="undef", id=wid)), back]
    else:
        first = [btn("✅ Защитил", WorkAct(a="def", id=wid)), back]
    rows = [
        first,
        [btn("✏️ Название", WorkAct(a="ren", id=wid)), btn("🗑 Удалить", WorkAct(a="del", id=wid))],
    ]
    return Screen(with_notice(notice, lines), markup(rows))


async def delete_work_screen(repo: Repo, user: User, wid: int) -> Screen | None:
    work = await repo.get_work(user, wid)
    if work is None:
        return None
    lines = [f"🗑 <b>Удалить «{esc(work.full)}»?</b>", "", "Отметка о защите тоже пропадёт."]
    rows = [[btn("🗑 Да, удалить", WorkAct(a="del_yes", id=wid)), btn("⬅️ Отмена", WorkAct(a="v", id=wid))]]
    return Screen("\n".join(lines), markup(rows))


# ---------------------------------------------------------------- быстрый ввод


def pick_screen(subjects: Sequence[Subject], values: Sequence[int], okr: bool, notice: str | None = None) -> Screen:
    if okr:
        what = f"оценку за ОКР {values[0]}"
    else:
        what = ("оценку " if len(values) == 1 else "оценки ") + ", ".join(map(str, values))
    lines = [f"🤔 <b>Куда записать {what}?</b>", "", "Выбери предмет:"]
    payload = "-".join(map(str, values))
    buttons = [
        btn(f"{subject_icon(s.name)} {truncate(display_name(s.name), 26)}", Pick(s=s.id, v=payload, o=int(okr)))
        for s in subjects
    ]
    rows = adaptive_rows(buttons, wide=18, narrow=0) + [[btn("✖️ Отмена", Nav(to="main"))]]
    return Screen(with_notice(notice, lines), markup(rows))


def open_pick_screen(subjects: Sequence[Subject], query: str) -> Screen:
    lines = [f"🔎 По запросу «{esc(query)}» нашлось несколько предметов:"]
    buttons = [
        btn(f"{subject_icon(s.name)} {truncate(display_name(s.name), 26)}", Subj(a="v", id=s.id)) for s in subjects
    ]
    rows = adaptive_rows(buttons, wide=18, narrow=0) + [[btn("✖️ Отмена", Nav(to="main"))]]
    return Screen("\n".join(lines), markup(rows))


def prompt_screen(title: str, body: str, back: CallbackData, notice: str | None = None) -> Screen:
    return Screen(with_notice(notice, [title, "", body]), markup([[btn("⬅️ Отмена", back)]]))


# ---------------------------------------------------------------- статистика и помощь


async def stats_screen(repo: Repo, user: User) -> Screen:
    views = await repo.views(user)
    ov = Overview(views)
    now = clock.now()
    week = sum(1 for v in views for g in v.grades if g.created_at >= now - 7 * 86400)
    lines = ["📊 <b>Статистика</b>", ""]
    if not ov.grades_count:
        lines.append("Пока нет оценок — статистика появится, как только добавишь первые.")
    else:
        lines.append(f"Средний балл: <b>{fmt_num(ov.avg)}</b> ≈ {round_half_up(ov.avg or 0)}")
        lines.append(f"По всем оценкам подряд: {fmt_num(ov.avg_all)}")
        lines.append(f"Оценок: {ov.grades_count} · за неделю: +{week}")
    if ov.works_total:
        pct = round(ov.works_done / ov.works_total * 100)
        lines.append(
            f"Работы: {ov.works_done} из {ov.works_total} {progress_bar(ov.works_done, ov.works_total)} {pct}%"
        )
    if views:
        ok = len(views) - len(ov.failing)
        lines.append(f"Аттестация: {ok} из {len(views)}" + (" 🎉" if ok == len(views) else ""))

    counts = Counter(value for v in views for value in v.values)
    if counts:
        top = max(counts.values())
        bars = [
            f"{grade:>2} {'█' * max(1, round(n / top * 12)):<12} {n}"
            for grade, n in sorted(counts.items(), reverse=True)
        ]
        lines += ["", "<b>Распределение оценок</b>", "<pre>" + "\n".join(bars) + "</pre>"]

    ranked = sorted((v for v in views if v.avg is not None), key=lambda v: v.avg or 0, reverse=True)
    if ranked:
        medals = ["🥇", "🥈", "🥉"]
        lines += ["", "<b>Рейтинг предметов</b>"]
        for i, v in enumerate(ranked):
            place = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{place} {subject_title(v)} — {fmt_num(v.avg)}")
    empty = [esc(v.name) for v in views if v.avg is None]
    if empty:
        lines.append("Без оценок: " + ", ".join(empty))
    return Screen("\n".join(lines), markup([[btn("⬅️ Меню", Nav(to="main"))]]))


def help_screen(user: User) -> Screen:
    e = ex(user)
    reminders = f"{remind_every_label(user.remind_every)} в {user.remind_time}" if user.remind_every else "выключены"
    lines = [
        "❓ <b>Как пользоваться</b>",
        "",
        "<b>Быстрый ввод</b> — просто пиши в чат:",
        f"• <code>физ {e.a} {e.b}</code> — оценки {e.a} и {e.b} по физике",
        f"• <code>{e.a} {e.b}</code> — спрошу, в какой предмет",
        f"• <code>физ окр {e.okr}</code> — оценка за ОКР",
        "• <code>физ</code> — открыть предмет",
        f"• <code>физ {e.a}, матан {e.b}</code> — сразу в несколько предметов (можно и по строкам)",
        f"Разделители любые: пробел, запятая, «/» — <code>{e.a}, {e.b}/{e.c}</code>.",
        "Названия можно сокращать: «матан», «англ», «оаип».",
        "",
        "<b>Аттестация</b> ❌ — если в предмете:",
        f"• нет оценки за ОКР или она ниже {user.pass_mark};",
        "• не защищена хоть одна лабораторная или практическая.",
        "",
        "<b>Средний балл</b> предмета — по всем его оценкам (вместе с ОКР). Общий — среднее по предметам.",
        "",
        f"<b>Напоминания</b>: {reminders}, и только если ты давно ничего не вносил.",
        "",
        "<b>Отмена</b>: после добавления появляется кнопка «↩️ Отменить».",
        "",
        "Команды: /menu — меню, /stats — статистика, /settings — настройки.",
    ]
    return Screen("\n".join(lines), markup([[btn("⬅️ Меню", Nav(to="main"))]]))


# ---------------------------------------------------------------- настройки


async def settings_screen(repo: Repo, user: User, notice: str | None = None) -> Screen:
    now = clock.now()
    reminders = f"{remind_every_label(user.remind_every)} в {user.remind_time}" if user.remind_every else "выключены"
    lines = [
        "⚙️ <b>Настройки</b>",
        "",
        f"🔔 Напоминания: {reminders}",
        f"🌍 Часовой пояс: {esc(tz_label(user.tz))} · сейчас {fmt_time(now, user.tz)}",
        f"🔢 Шкала: {user.scale}-балльная · ОКР сдана от {user.pass_mark}",
        "🧹 Чистый чат: " + ("вкл — убираю твои сообщения, в чате остаётся одно меню" if user.clean_chat else "выкл"),
    ]
    rows = [
        [btn("🔔 Напоминания", Nav(to="rem")), btn("🌍 Часовой пояс", Nav(to="tz"))],
        [btn("🔢 Шкала и порог ОКР", Nav(to="scale"))],
        [btn(f"🧹 Чистый чат: {'вкл ✅' if user.clean_chat else 'выкл'}", Nav(to="clean"))],
        [btn("🗂 Семестры", Sem(a="list")), btn("📤 Экспорт", Nav(to="export"))],
        [btn("🗑 Удалить все данные", Nav(to="wipe"))],
        [btn("⬅️ Меню", Nav(to="main"))],
    ]
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
        "Напоминаю, только если ты давно ничего не вносил — без спама.",
    ]
    if user.skip_sunday:
        lines.append("По воскресеньям не беспокою.")
    every = user.remind_every
    rows = [
        [btn(mark(every == 0, "🔕 Выкл"), Rem(a="every", v=0)), btn(mark(every == 1, "Каждый день"), Rem(a="every", v=1))],
        [btn(mark(every == 2, "Раз в 2 дня"), Rem(a="every", v=2)), btn(mark(every == 3, "Раз в 3 дня"), Rem(a="every", v=3))],
        *chunks(
            [btn(mark(t == user.remind_time, t), Rem(a="time", v=int(t[:2]) * 60 + int(t[3:]))) for t in REMIND_TIMES],
            3,
        ),
        [btn("⌨️ Другое время", Rem(a="custom"))],
        [btn(f"🗓 Не беспокоить в воскресенье: {'да ✅' if user.skip_sunday else 'нет'}", Rem(a="sun"))],
        [btn("🔔 Прислать пример", Rem(a="test"))],
        [btn("⬅️ Назад", Nav(to="settings"))],
    ]  # fmt: skip
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
        f"ОКР считается сданной от <b>{user.pass_mark}</b> — ниже порога ты не аттестован.",
    ]
    rows = [
        [btn(mark(user.scale == 10, "10-балльная"), ScaleSet(a="set", v=10)), btn(mark(user.scale == 5, "5-балльная"), ScaleSet(a="set", v=5))],
        [btn("➖", ScaleSet(a="pass", v=-1)), btn(f"Порог ОКР: {user.pass_mark}", Nav(to="noop")), btn("➕", ScaleSet(a="pass", v=1))],
        [btn("⬅️ Назад", Nav(to="settings"))],
    ]  # fmt: skip
    return Screen(with_notice(notice, lines), markup(rows))


# ---------------------------------------------------------------- семестры


async def semesters_screen(repo: Repo, user: User, notice: str | None = None) -> Screen:
    semesters = await repo.list_semesters(user.id)
    current = next((s for s in semesters if s.id == user.semester_id), None)
    archived = [s for s in semesters if s.id != user.semester_id]
    ov = Overview(await repo.views(user))
    lines = ["🗂 <b>Семестры</b>", ""]
    if current:
        lines.append(
            f"Сейчас: <b>{esc(current.title)}</b> — {plural(len(ov.views), SUBJECT_FORMS)}, средний {fmt_num(ov.avg)}"
        )
        lines.append(f"Начат {fmt_date(current.created_at, user.tz, with_year=True)}")
    lines.append("")
    if archived:
        lines.append("<b>Архив</b> — нажми, чтобы посмотреть итоги:")
    else:
        lines.append("Архив пуст. Когда семестр закончится — начни новый, а этот сохранится здесь со всеми оценками.")
    rows = [[btn("🆕 Начать новый семестр", Sem(a="new"))]]
    for sem in archived:
        avg = Overview(await repo.views(user, sem.id)).avg
        rows.append([btn(f"📁 {sem.title} · {fmt_num(avg)}", Sem(a="view", id=sem.id))])
    rows.append([btn("⬅️ Назад", Nav(to="settings"))])
    return Screen(with_notice(notice, lines), markup(rows))


async def semester_view_screen(repo: Repo, user: User, sem_id: int) -> Screen | None:
    semester = await repo.get_semester(user.id, sem_id)
    if semester is None:
        return None
    ov = Overview(await repo.views(user, sem_id))
    period = fmt_date(semester.created_at, user.tz, with_year=True)
    if semester.archived_at:
        period += " — " + fmt_date(semester.archived_at, user.tz, with_year=True)
    lines = [f"📁 <b>{esc(semester.title)}</b> · архив", period, ""]
    lines.append(f"📊 Средний балл: <b>{fmt_num(ov.avg)}</b>")
    if ov.views:
        lines.append(f"Аттестован: {len(ov.views) - len(ov.failing)} из {len(ov.views)}")
        lines.append("")
        for v in ov.views:
            avg = fmt_num(v.avg) if v.avg is not None else "без оценок"
            lines.append(f"{subject_title(v)} — {avg} {'✅' if v.ok else '❌'}")
    else:
        lines.append("Предметов не было.")
    return Screen("\n".join(lines), markup([[btn("⬅️ Назад", Sem(a="list"))]]))


async def new_semester_confirm(repo: Repo, user: User) -> Screen:
    semester = await repo.get_semester(user.id, user.semester_id)
    title = esc(semester.title) if semester else "Текущий семестр"
    lines = [
        "🆕 <b>Начать новый семестр?</b>",
        "",
        f"«{title}» уйдёт в архив: все оценки и итоги сохранятся, их можно будет посмотреть "
        "в 🗂 Семестрах. Новый семестр начнётся с чистого листа.",
    ]
    rows = [[btn("✅ Да, начать", Sem(a="new_yes")), btn("⬅️ Отмена", Sem(a="list"))]]
    return Screen("\n".join(lines), markup(rows))


def new_semester_subjects(title: str, previous_id: int, previous_count: int) -> Screen:
    lines = [f"🎉 Начат семестр «{esc(title)}»."]
    rows: list[Row] = []
    if previous_count:
        lines += [
            "",
            f"Перенести список предметов из прошлого семестра ({previous_count})? "
            "Переносятся только названия и настройки ОКР — оценки и работы останутся в архиве.",
        ]
        rows.append([btn("📋 Да, перенести", Sem(a="copy", id=previous_id))])
    rows.append([btn("✏️ Введу новые", Sem(a="fresh"))])
    return Screen("\n".join(lines), markup(rows))


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
