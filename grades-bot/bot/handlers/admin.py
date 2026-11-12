"""Админ-панель владельца (/apanel): доступ по приглашению, заявки, тихий просмотр результатов."""

from __future__ import annotations

import logging
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.callback_answer import CallbackAnswer

from .. import screens
from ..access import parse_usernames
from ..callbacks import Adm
from ..context import Ctx
from ..screens import esc
from ..states import AdmSt
from .common import Event, go_main

router = Router(name="admin")
log = logging.getLogger(__name__)

ADD_TITLE = "➕ <b>Добавить пользователей</b>"
ADD_BODY = (
    "Отправь юзернеймы — можно несколько: <code>@ivan, @petr</code>\n"
    "Человек сможет пользоваться ботом, как только напишет ему /start. У каждого — свои данные."
)


async def notify(bot: Bot, user_id: int, text: str) -> bool:
    """Сообщение пользователю (если он ещё ни разу не писал боту — Telegram не даст)."""
    try:
        await bot.send_message(user_id, text)
        return True
    except TelegramAPIError:
        log.debug("Не удалось написать %s", user_id, exc_info=True)
        return False


async def _main(event: Event, ctx: Ctx, notice: str | None = None, fresh: bool = False) -> None:
    await ctx.mode()
    is_open = await ctx.access.is_open() if ctx.access else False
    screen = await screens.admin_screen(ctx.repo, is_open, notice)
    if fresh:
        await ctx.fresh(event, screen)
    else:
        await ctx.show(event, screen)


@router.message(Command("apanel"))
async def cmd_apanel(message: Message, ctx: Ctx) -> None:
    if not ctx.is_owner:
        # Для остальных команды как будто нет.
        await go_main(message, ctx, notice="🤷 Не знаю такой команды. Все команды — /help.", fresh=True)
        return
    await _main(message, ctx, fresh=True)


@router.callback_query(Adm.filter())
async def admin_action(cq: CallbackQuery, callback_data: Adm, ctx: Ctx, callback_answer: CallbackAnswer) -> None:
    if not ctx.is_owner:
        callback_answer.text = "Эта кнопка устарела — открываю меню"
        await go_main(cq, ctx)
        return
    action, target = callback_data.a, callback_data.id
    if action == "main":
        await _main(cq, ctx)
    elif action == "add":
        await ctx.mode(AdmSt.add)
        await ctx.show(cq, screens.prompt_screen(ADD_TITLE, ADD_BODY, Adm(a="main")))
    elif action == "users":
        owner_id = await ctx.access.owner_id() if ctx.access else None
        await ctx.mode()
        await ctx.show(cq, await screens.admin_users_screen(ctx.repo, owner_id, callback_data.p))
    elif action == "user":
        await _user(cq, ctx, target)
    elif action == "revoke":
        target_user = await ctx.repo.get_user(target)
        await ctx.repo.revoke(user_id=target, username=target_user.username if target_user else None)
        await _user(cq, ctx, target, "🔒 Доступ закрыт. Данные пользователя сохранены.")
    elif action == "unlist":
        rows = [r for r in await ctx.repo.access_list() if r["id"] == target]
        if rows:
            await ctx.repo.revoke(user_id=rows[0]["user_id"], username=rows[0]["username"])
        owner_id = await ctx.access.owner_id() if ctx.access else None
        await ctx.show(cq, await screens.admin_users_screen(ctx.repo, owner_id, notice="🗑 Приглашение убрано."))
    elif action == "requests":
        await ctx.mode()
        await ctx.show(cq, await screens.admin_requests_screen(ctx.repo))
    elif action in ("ok", "no"):
        await _decide(cq, ctx, target, approve=action == "ok")
    elif action == "open":
        is_open = await ctx.access.is_open() if ctx.access else False
        await ctx.repo.set_meta("open_access", "0" if is_open else "1")
        notice = "🔒 Бот снова только для приглашённых." if is_open else "🔓 Бот открыт для всех."
        await _main(cq, ctx, notice)
    else:
        await _main(cq, ctx)


async def _user(event: Event, ctx: Ctx, target_id: int, notice: str | None = None) -> None:
    target = await ctx.repo.get_user(target_id)
    if target is None:
        owner_id = await ctx.access.owner_id() if ctx.access else None
        await ctx.show(event, await screens.admin_users_screen(ctx.repo, owner_id, notice="Пользователь не найден."))
        return
    has_access = await ctx.repo.has_access(target.id, target.username)
    await ctx.mode()
    await ctx.show(event, await screens.admin_user_screen(ctx.repo, target, has_access, notice))


async def _decide(event: Event, ctx: Ctx, user_id: int, approve: bool) -> None:
    requests = {r["user_id"]: r for r in await ctx.repo.requests()}
    request = requests.get(user_id)
    if approve:
        await ctx.repo.grant(user_id=user_id, username=request["username"] if request else None)
        sent = await notify(ctx.bot, user_id, "✅ Владелец открыл тебе доступ к боту! Нажми /start, чтобы начать.")
        name = esc(request["name"]) if request else str(user_id)
        notice = f"✅ Доступ открыт: {name}" + ("" if sent else " (написать ему не получилось)")
    else:
        await ctx.repo.drop_request(user_id)
        await notify(ctx.bot, user_id, "❌ Владелец бота отклонил заявку на доступ.")
        notice = "❌ Заявка отклонена."
    await ctx.mode()
    await ctx.show(event, await screens.admin_requests_screen(ctx.repo, notice))


@router.message(AdmSt.add, F.text)
async def admin_add_text(message: Message, ctx: Ctx) -> None:
    if not ctx.is_owner:
        await go_main(message, ctx)
        return
    names, ids, bad = parse_usernames(message.text or "")
    added: list[str] = []
    already: list[str] = []
    for username in names:
        (added if await ctx.repo.grant(username=username) else already).append(f"@{username}")
        known = await ctx.repo.find_user_by_username(username)
        if known is not None:
            with suppress(Exception):
                await ctx.repo.drop_request(known.id)
            await notify(ctx.bot, known.id, "✅ Владелец открыл тебе доступ к боту! Нажми /start, чтобы начать.")
    for user_id in ids:
        (added if await ctx.repo.grant(user_id=user_id) else already).append(str(user_id))
    lines = []
    if added:
        lines.append("✅ Добавлены: " + ", ".join(esc(x) for x in added) + ". Пусть напишут боту /start.")
    if already:
        lines.append("↪️ Уже были: " + ", ".join(esc(x) for x in already))
    if bad:
        lines.append("⚠️ Не похоже на юзернейм: " + ", ".join(esc(x) for x in bad[:5]))
    if not lines:
        await ctx.show(
            message, screens.prompt_screen(ADD_TITLE, ADD_BODY, Adm(a="main"), "⚠️ Отправь юзернейм, например @ivan")
        )
        return
    await _main(message, ctx, "\n".join(lines))
