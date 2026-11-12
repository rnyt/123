"""Экономия памяти, чтобы бот влезал в бесплатные хостинги со 128 МБ (fps.ms и т.п.).

aiogram при импорте сразу собирает валидаторы pydantic для всех ~300 типов Telegram
(вызывает model_rebuild() у каждого): это ~110 МБ памяти и пара секунд. Здесь сборка
откладывается до первого использования типа. В работе боту нужен лишь десяток типов
(Update, Message, кнопки, несколько методов), так что он занимает ~70 МБ вместо ~165 МБ
и запускается в несколько раз быстрее.

Опирается на внутренности pydantic, поэтому всё обёрнуто в проверки: если что-то не так
(другая версия pydantic, aiogram уже импортирован), ничего не меняем — бот работает
как обычно, только ест больше памяти. Отключить вручную: LOW_MEMORY=0 в .env.
"""

from __future__ import annotations

import inspect
import os
import sys
from typing import Any

_AIOGRAM = "aiogram."
_enabled = False


def is_enabled() -> bool:
    return _enabled


def enable() -> bool:
    """Включить отложенную сборку. Вызывать до первого импорта aiogram. True — включено."""
    global _enabled
    if _enabled:
        return True
    if os.getenv("LOW_MEMORY", "1").strip().lower() in {"0", "no", "off", "false"}:
        return False
    if "aiogram" in sys.modules:
        return False
    try:
        from pydantic import BaseModel
        from pydantic._internal import _config
    except Exception:  # pragma: no cover - без pydantic aiogram всё равно не заработает
        return False

    defaults = getattr(_config, "config_defaults", None)
    original = getattr(BaseModel.model_rebuild, "__func__", None)
    if not isinstance(defaults, dict) or "defer_build" not in defaults or original is None:
        return False
    try:
        params = inspect.signature(original).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return False
    if not {"force", "raise_errors", "_parent_namespace_depth", "_types_namespace"} <= params.keys():
        return False

    names: dict[str, Any] = {}

    def model_rebuild(
        cls: Any,
        *,
        force: bool = False,
        raise_errors: bool = True,
        _parent_namespace_depth: int = 2,
        _types_namespace: Any = None,
    ) -> bool | None:
        if cls.__module__.startswith(_AIOGRAM):
            if _types_namespace is not None and not force:
                # Это aiogram при импорте: запоминаем имена всех типов, а собираем потом.
                if not names:
                    names.update(_types_namespace)
                return None
            if names:
                # Ленивая сборка при первом использовании: подставляем имена типов,
                # иначе pydantic не найдёт ссылки вида "Message" внутри других типов.
                _types_namespace = {**names, **(_types_namespace or {})}
        return original(
            cls,
            force=force,
            raise_errors=raise_errors,
            _parent_namespace_depth=_parent_namespace_depth + 1,  # +1 — кадр этой обёртки
            _types_namespace=_types_namespace,
        )

    BaseModel.model_rebuild = classmethod(model_rebuild)  # type: ignore[assignment,method-assign]
    previous = defaults["defer_build"]
    defaults["defer_build"] = True
    try:
        import aiogram  # noqa: F401  (все модели aiogram создаются сейчас, но без сборки)
    except BaseException:
        BaseModel.model_rebuild = classmethod(original)  # type: ignore[assignment,method-assign]
        raise
    finally:
        defaults["defer_build"] = previous
    _enabled = True
    return True
