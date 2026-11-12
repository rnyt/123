"""Фоновые напоминания: раз в N дней в выбранное время, если давно ничего не вносил."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.storage.base import BaseStorage, StorageKey

from . import clock, screens
from .callbacks import Nav
from .logic import ago, reminder_due
from .models import User
from .panel import Panel
from .repo import Repo
from .screens import btn

log = logging.getLogger(__name__)


def reminder_notice(user: User, now: int, test: bool = False) -> str:
    head = "🔔 <b>Так будет выглядеть напоминание</b>" if test else "🔔 <b>Пора обновить оценки!</b>"
    return "\n".join(
        [
            head,
            "Появились новые оценки или защитил работы? Внеси их — так средний балл и аттестация будут точными.",
            f"Последнее обновление: {ago(user.last_data_at, now, user.tz)}.",
        ]
    )


async def send_reminder(
    bot: Bot,
    repo: Repo,
    panel: Panel,
    user: User,
    storage: BaseStorage | None = None,
    test: bool = False,
) -> None:
    now = clock.now()
    top = [[btn("⏰ Напомнить через 2 часа", Nav(to="snooze"))]]
    screen = await screens.main_screen(repo, user, reminder_notice(user, now, test), top)
    await panel.send(bot, user, screen)
    if storage is not None:
        # Панель сменилась — незаконченный ввод (например, «отправь название») сбрасываем.
        key = StorageKey(bot_id=bot.id, chat_id=user.id, user_id=user.id)
        await storage.set_state(key, None)
        await storage.set_data(key, {})
    if not test:
        await repo.update_user(user.id, last_reminded_at=now, snooze_until=None)


async def check_reminders(bot: Bot, repo: Repo, panel: Panel, storage: BaseStorage | None) -> int:
    """Один проход по пользователям. Возвращает число отправленных напоминаний."""
    sent = 0
    now = clock.now()
    for user in await repo.reminder_candidates():
        kind = reminder_due(user, now)
        if kind is None:
            continue
        if kind == "clear_snooze":
            await repo.update_user(user.id, snooze_until=None)
            continue
        try:
            await send_reminder(bot, repo, panel, user, storage)
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
