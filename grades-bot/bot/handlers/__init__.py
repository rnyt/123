"""Сборка роутеров. Порядок важен: команды — первыми, «всё остальное» — последним."""

from aiogram import Router

from . import menu, settings, start, subjects, text, works

ORDER = (start, menu, subjects, works, settings, text)


def build_router() -> Router:
    root = Router(name="root")
    for module in ORDER:
        router = module.router
        parent = router.parent_router
        if parent is not None:
            # Повторная сборка (например, в тестах): отцепляем от прежнего диспетчера.
            parent.sub_routers.remove(router)
            router._parent_router = None
        root.include_router(router)
    return root
