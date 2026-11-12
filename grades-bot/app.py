"""Запуск на хостингах с панелью (fps.ms, Pterodactyl и т.п.), где указывают файл: app.py.

То же самое, что `python -m bot`. Токен берётся из файла .env рядом с этим файлом.
"""

from bot.__main__ import main

if __name__ == "__main__":
    main()
