"""Telegram-бот для учёта оценок, ОКР и защиты лабораторных/практических работ."""

from .lowmem import enable as _enable_low_memory

# До первого импорта aiogram: экономит ~90 МБ памяти (см. bot/lowmem.py).
_enable_low_memory()

__version__ = "1.0.0"
