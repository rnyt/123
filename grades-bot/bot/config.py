"""Настройки бота: переменные окружения и (необязательно) файл .env."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

TOKEN_HELP = (
    "Не задан BOT_TOKEN.\n"
    "1) Создай бота у @BotFather в Telegram (команда /newbot) и скопируй токен.\n"
    "2) Скопируй файл .env.example в .env и впиши токен в строку BOT_TOKEN=...\n"
    "3) Запусти снова: python -m bot"
)


def load_dotenv(path: str | os.PathLike[str]) -> None:
    """Мини-загрузчик .env: строки KEY=VALUE, комментарии через #.

    Уже заданные переменные окружения не перезаписываются.
    """
    file = Path(path)
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Config:
    bot_token: str
    db_path: str = str(PROJECT_DIR / "data" / "grades.db")
    default_tz: str = "Europe/Minsk"
    allowed_users: frozenset[int] = frozenset()
    proxy: str | None = None
    log_level: str = "INFO"
    reminder_check_seconds: int = 60


def _resolve_path(value: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return str(path)


def load_config() -> Config:
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(PROJECT_DIR / ".env")

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(TOKEN_HELP)
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
        raise SystemExit(
            "BOT_TOKEN выглядит неправильно: он должен быть вида 1234567890:AAH... (его выдаёт @BotFather)."
        )

    allowed = frozenset(int(part) for part in re.split(r"[,;\s]+", os.getenv("ALLOWED_USERS", "")) if part.isdigit())
    try:
        check_every = max(15, int(os.getenv("REMINDER_CHECK_SECONDS", "60")))
    except ValueError:
        check_every = 60

    return Config(
        bot_token=token,
        db_path=_resolve_path(os.getenv("DB_PATH", "data/grades.db")),
        default_tz=os.getenv("DEFAULT_TZ", "Europe/Minsk").strip() or "Europe/Minsk",
        allowed_users=allowed,
        proxy=os.getenv("PROXY_URL", "").strip() or None,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        reminder_check_seconds=check_every,
    )
