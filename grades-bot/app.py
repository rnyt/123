"""Запуск на хостингах с панелью (fps.ms, Pterodactyl и т.п.): в Startup указан файл app.py.

Делает то же, что `python -m bot`, но сначала проверяет версию Python и библиотеки
(если их нет, сам ставит из requirements.txt) и пишет в консоль по-русски, что не так.
Токен берётся из файла .env рядом с этим файлом.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY_MIN, PY_MAX = (3, 10), (3, 14)  # что поддерживает aiogram 3.31
RETRY_FLAG = "GRADES_BOT_DEPS_INSTALLED"


def say(text: str) -> None:
    print(text, flush=True)


def check_python() -> None:
    if PY_MIN <= sys.version_info[:2] <= PY_MAX:
        return
    say(f"❌ Python {sys.version.split()[0]} не подходит: нужен от 3.10 до 3.14.")
    say("   В панели: Startup → Docker Image → выбери Python 3.12, потом нажми Restart.")
    sys.exit(1)


def deps_dir() -> Path:
    return HERE / ".deps" / f"py{sys.version_info[0]}{sys.version_info[1]}"


def install_requirements(missing: str) -> bool:
    target = deps_dir()
    say(f"⏳ Не нашёл библиотеку {missing} — ставлю всё из requirements.txt сам (1–2 минуты)…")
    cmd = [
        sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
        "--upgrade", "--target", str(target), "-r", str(HERE / "requirements.txt"),
    ]  # fmt: skip
    try:
        return subprocess.run(cmd, check=False).returncode == 0
    except OSError:
        return False


def main() -> None:
    say(f"▶ Запускаю бота оценок · Python {sys.version.split()[0]} · папка {HERE}")
    check_python()
    if deps_dir().is_dir():
        sys.path.insert(1, str(deps_dir()))
    try:
        from bot.__main__ import main as run_bot
    except ImportError as exc:
        missing = (exc.name or "").split(".")[0]
        if not missing or missing == "bot":
            raise
        if os.environ.get(RETRY_FLAG) or not install_requirements(missing):
            say(f"❌ Не получилось поставить библиотеку {missing}.")
            say("   Проверь в панели: Startup → Requirements file = requirements.txt,")
            say("   и что requirements.txt лежит рядом с app.py (в корне, не в отдельной папке).")
            sys.exit(1)
        say("✅ Библиотеки поставлены, запускаю бота заново…")
        os.environ[RETRY_FLAG] = "1"
        os.execv(sys.executable, [sys.executable, *sys.argv])
    run_bot()


if __name__ == "__main__":
    main()
