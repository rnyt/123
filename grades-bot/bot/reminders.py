"""Фоновые напоминания: внести оценки, защитить работы, после сессии — отправить семестр в архив."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.storage.base import BaseStorage, StorageKey

from . import clock, screens
from .callbacks import Nav
from .dates import in_range, to_date
from .logic import Overview, ago, days_between, in_window, local_dt, reminder_due, short_list
from .models import Semester, User
from .panel import Panel
from .repo import Repo
from .screens import btn, esc

log = logging.getLogger(__name__)
ASK_ARCHIVE_EVERY_DAYS = 3


def reminder_notice(user: User, now: int, ov: Overview, kind: str = "regular", test: bool = False) -> str:
    if test:
        head = "🔔 <b>Так будет выглядеть напоминание</b>"
    elif kind == "works":
        head = "🔔 <b>Не забудь про работы!</b>"
    else:
        head = "🔔 <b>Пора обновить оценки!</b>"
    lines = [head]
    if kind != "works" or test:
        lines.append("Появились новые оценки или защитил работы? Внеси их — так средний балл и допуск будут точными.")
        lines.append(f"Последнее обновление: {ago(user.last_data_at, now, user.tz)}.")
    if user.works_reminders and ov.open_works:
        parts = [f"{esc(v.label)} — {short_list([esc(w.short) for w in works], 4)}" for v, works in ov.open_works[:6]]
        lines.append("🔬 Осталось зачесть: " + "; ".join(parts))
    return "\n".join(lines)


async def _reset_input(bot: Bot, storage: BaseStorage | None, user: User) -> None:
    """Панель сменилась — незаконченный ввод (например, «отправь название») сбрасываем."""
    if storage is None:
        return
    key = StorageKey(bot_id=bot.id, chat_id=user.id, user_id=user.id)
    await storage.set_state(key, None)
    await storage.set_data(key, {})


async def send_reminder(
    bot: Bot,
    repo: Repo,
    panel: Panel,
    user: User,
    storage: BaseStorage | None = None,
    test: bool = False,
    kind: str = "regular",
) -> None:
    now = clock.now()
    ov = Overview(await repo.views(user))
    top = [[btn("⏰ Напомнить через 2 часа", Nav(to="snooze"))]]
    screen = await screens.main_screen(repo, user, reminder_notice(user, now, ov, kind, test), top)
    await panel.send(bot, user, screen)
    await _reset_input(bot, storage, user)
    if not test:
        await repo.update_user(user.id, last_reminded_at=now, snooze_until=None)


def on_holidays(today_ts: int, user: User, semester: Semester | None, previous: Semester | None) -> bool:
    today = local_dt(today_ts, user.tz).date()
    return any(s is not None and in_range(today, s.holidays_start, s.holidays_end) for s in (semester, previous))


def archive_due(now: int, user: User, semester: Semester | None, has_subjects: bool) -> bool:
    """Сессия закончилась, семестр не в архиве — пора спросить (не чаще раза в 3 дня, в час напоминаний)."""
    if semester is None or not semester.archive_ask or not has_subjects or semester.archived_at:
        return False
    end = to_date(semester.session_end)
    if end is None or local_dt(now, user.tz).date() <= end:
        return False
    if semester.archive_asked_at and days_between(semester.archive_asked_at, now, user.tz) < ASK_ARCHIVE_EVERY_DAYS:
        return False
    return in_window(user, now)


async def check_reminders(bot: Bot, repo: Repo, panel: Panel, storage: BaseStorage | None) -> int:
    """Один проход по пользователям. Возвращает число отправленных сообщений."""
    sent = 0
    now = clock.now()
    for user in await repo.reminder_candidates():
        try:
            semester = await repo.get_semester(user.id, user.semester_id)
            views = await repo.views(user)
            if archive_due(now, user, semester, bool(views)) and semester is not None:
                await panel.send(bot, user, screens.archive_prompt(semester, Overview(views)))
                await _reset_input(bot, storage, user)
                await repo.update_semester(user.id, semester.id, archive_asked_at=now)
                await repo.update_user(user.id, last_reminded_at=now)
                sent += 1
                continue
            previous = await repo.last_archived(user.id)
            paused = on_holidays(now, user, semester, previous)
            works_due = any(v.open_works for v in views)
            kind = reminder_due(user, now, paused=paused, works_due=works_due)
            if kind is None:
                continue
            if kind == "clear_snooze":
                await repo.update_user(user.id, snooze_until=None)
                continue
            await send_reminder(bot, repo, panel, user, storage, kind="works" if kind == "works" else "regular")
            sent += 1
        except TelegramForbiddenError:
            log.info("Пользователь %s заблокировал бота — напоминания на паузе", user.id)
            await repo.update_user(user.id, blocked=True, snooze_until=None)
        except TelegramRetryAfter as exc:
            log.warning("Флуд-контроль Telegram, жду %s с", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
        except TelegramAPIError:
            log.warning("Не удалось отправить напоминание %s", user.id, exc_info=True)
        await asyncio.sleep(0.05)
    return sent


async def reminder_loop(bot: Bot, repo: Repo, panel: Panel, storage: BaseStorage | None, interval: int = 60) -> None:
    log.info("Напоминания включены, проверка каждые %s с", interval)
    while True:
        try:
            await check_reminders(bot, repo, panel, storage)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — цикл напоминаний не должен умирать
            log.exception("Ошибка в цикле напоминаний")
        await asyncio.sleep(interval)
