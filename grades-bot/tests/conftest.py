from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from bot import clock
from bot.app import build_dispatcher
from bot.db import connect
from bot.repo import Repo
from bot.storage import SQLiteStorage
from tests.harness import Client, FakeTelegram, make_bot

# Среда, 11 ноября 2026, 15:00 по Минску.
START = int(datetime(2026, 11, 11, 15, 0, tzinfo=ZoneInfo("Europe/Minsk")).timestamp())


class FakeClock:
    def __init__(self, ts: int) -> None:
        self.ts = ts

    def __call__(self) -> int:
        return self.ts

    def advance(self, *, days: float = 0, hours: float = 0, minutes: float = 0) -> None:
        self.ts += int(days * 86400 + hours * 3600 + minutes * 60)

    def set_local(self, year: int, month: int, day: int, hour: int, minute: int = 0, tz: str = "Europe/Minsk") -> None:
        self.ts = int(datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz)).timestamp())

    @property
    def utc(self) -> datetime:
        return datetime.fromtimestamp(self.ts, UTC)


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock(START)
    monkeypatch.setattr(clock, "now", fake)
    return fake


@pytest_asyncio.fixture
async def db():
    conn = await connect(":memory:")
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def repo(db, fake_clock) -> Repo:
    return Repo(db, default_tz="Europe/Minsk")


@pytest_asyncio.fixture
async def env(repo: Repo, fake_clock: FakeClock):
    tg = FakeTelegram()
    bot = make_bot(tg)
    storage = SQLiteStorage(repo.db, repo.lock)
    dp = build_dispatcher(repo, storage)
    client = Client(dp, bot, tg)
    yield SimpleNamespace(tg=tg, bot=bot, dp=dp, repo=repo, storage=storage, client=client, clock=fake_clock)
    await bot.session.close()


SUBJECTS = "Высшая математика, Физика, Программирование, Английский язык"


async def onboard(
    client: Client, subjects: str = SUBJECTS, scale: str = "10-балльная", no_okr: tuple[str, ...] = ("Английский",)
) -> None:
    """Пройти знакомство: шкала → предметы → где нет ОКР → готово."""
    await client.send("/start")
    await client.press(scale)
    await client.send(subjects)
    await client.press("Дальше")
    for name in no_okr:
        await client.press(name)
    await client.press("Готово")
