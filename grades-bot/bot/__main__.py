"""Запуск: python -m bot"""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError

from .access import Access
from .app import build_dispatcher
from .commands import setup_bot_profile
from .config import Config, load_config
from .db import connect
from .reminders import reminder_loop
from .repo import Repo
from .storage import SQLiteStorage

log = logging.getLogger("bot")


async def run(config: Config) -> None:
    db = await connect(config.db_path)
    repo = Repo(db, default_tz=config.default_tz)
    storage = SQLiteStorage(db, repo.lock)
    session = AiohttpSession(proxy=config.proxy) if config.proxy else AiohttpSession()
    bot = Bot(
        token=config.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    access = Access(repo, owner=config.owner, allowed_ids=config.allowed_users)
    dp = build_dispatcher(repo, storage, access)
    tasks: list[asyncio.Task[None]] = []

    async def on_startup(bot: Bot) -> None:
        me = await bot.me()
        await setup_bot_profile(bot, await access.owner_id())
        tasks.append(asyncio.create_task(reminder_loop(bot, repo, dp["panel"], storage, config.reminder_check_seconds)))
        log.info("Бот @%s запущен. Владелец: %s. База: %s", me.username, config.owner, config.db_path)

    async def on_shutdown() -> None:
        for task in tasks:
            task.cancel()
        log.info("Бот остановлен")

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    try:
        try:
            await bot.get_me()
        except TelegramUnauthorizedError:
            raise SystemExit("Telegram не принял токен. Проверь BOT_TOKEN в .env (его выдаёт @BotFather).") from None
        except TelegramNetworkError as exc:
            raise SystemExit(
                f"Не получается подключиться к Telegram ({exc}). Проверь интернет или задай PROXY_URL в .env."
            ) from None
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await db.close()


def main() -> None:
    config = load_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    try:
        asyncio.run(run(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
