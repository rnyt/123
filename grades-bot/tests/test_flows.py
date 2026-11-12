"""Сквозные сценарии: пользователь пишет и нажимает кнопки, бот отвечает как в Telegram."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton

from bot.app import build_dispatcher
from bot.callbacks import Subj, WorkAct
from bot.reminders import check_reminders
from bot.storage import SQLiteStorage
from tests.conftest import onboard
from tests.harness import Client, FakeTelegram, make_bot


async def test_onboarding_builds_dashboard(env) -> None:
    c = env.client
    await c.send("/start")
    assert "Шаг 1 из 3" in c.text
    await c.press("10-балльная")
    assert "Шаг 2 из 3" in c.text
    await c.send("Высшая математика, Физика\nПрограммирование; Английский язык, физика")
    assert "Добавлено (4)" in c.text
    assert "Пропущено" in c.text and "физика" in c.text
    await c.press("Дальше")
    assert "Шаг 3 из 3" in c.text
    await c.press("Английский")
    assert "▫️ Английский язык — без ОКР" in c.labels
    await c.press("Готово")
    text = c.text
    assert "Готово!" in text and "Осень 2026" in text
    assert "Не аттестован: 3 из 4" in text
    assert "Нет ОКР: Высшая математика, Физика, Программирование" in text
    assert "🇬🇧 Английский язык · — ✅" in c.labels
    # Чистый чат: от пользователя ничего не осталось, у бота — одно сообщение.
    assert [m.from_bot for m in c.visible] == [True]


async def test_keypad_grades_undo_and_done(env) -> None:
    c = env.client
    await onboard(c)
    await c.press("Физика")
    await c.press("➕ Оценки")
    await c.press("8", exact=True)
    assert c.last_answer.text == "+8 → средний 8,00"
    await c.press("10", exact=True)
    await c.press("9", exact=True)
    assert "Только что: 8, 10, 9" in c.text
    await c.press("Убрать последнюю (9)")
    assert "Только что: 8, 10" in c.text
    await c.press("Готово")
    assert "✅ Добавлено: 8, 10" in c.text
    assert "📝 8, 10" in c.text
    assert "Средний: <b>9,00</b>" in c.text


async def test_text_grades_on_subject_card_with_undo(env) -> None:
    c = env.client
    await onboard(c)
    await c.press("Физика")
    await c.send("7, 9/10")  # прямо на карточке предмета
    assert "+7, +9, +10" in c.text
    assert "📝 7, 9, 10" in c.text
    await c.press("Отменить")
    assert "убрал 3 оценки" in c.text
    assert "Оценок пока нет" in c.text


async def test_quick_add_from_main_menu(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("прог 8 9")
    assert "Программирование</b>: +8, +9" in c.text
    await c.press("⬅️ Меню")
    await c.send("8 7")
    assert "Куда записать оценки 8, 7?" in c.text
    await c.press("Физика")
    assert "Физика</b>: +8, +7" in c.text
    await c.press("⬅️ Меню")
    await c.send("химия 5")
    assert "Не нашёл предмет «химия»" in c.text
    await c.send("язык 9")  # Английский язык — единственный «язык»
    assert "Английский язык</b>: +9" in c.text


async def test_quick_add_rejects_bad_grades(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ 8 11")
    assert "Не понял оценки: «11»" in c.text
    view = (await env.repo.views(await env.repo.get_user(c.id)))[1]
    assert view.values == []


async def test_quick_open_subject_and_unknown_text(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("матан")
    assert "📐 Высшая математика" in c.text and "➕ Оценки" in c.labels
    await c.send("привет, как дела")
    assert "Не понял" in c.text


async def test_okr_quick_and_attestation(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ окр 3")
    assert "ОКР — <b>3</b> ❌ ниже 4" in c.text
    await c.press("⬅️ Меню")
    assert "ОКР ниже 4: Физика — 3" in c.text
    await c.send("физ окр 7")
    assert "ОКР — <b>7</b> ✅ сдана (было 3)" in c.text
    await c.press("Отменить")
    assert "Вернул прежнюю оценку за ОКР: 3" in c.text


async def test_okr_screen_keypad(env) -> None:
    c = env.client
    await onboard(c)
    await c.press("Физика")
    await c.press("📌 ОКР")
    assert "Сейчас: оценки нет" in c.text
    await c.press("6", exact=True)
    assert "ОКР: <b>6</b> ✅" in c.text
    await c.press("📌 ОКР")
    await c.send("8")  # можно и текстом
    assert "ОКР — <b>8</b> ✅ сдана (было 6)" in c.text


async def test_works_flow(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("прог")
    assert not any("Работы" in label for label in c.labels)
    await c.press("+ Лабораторная")
    await c.press("+3")
    assert "Добавлено: ЛР 1–3" in c.text
    assert "📂 Работы · защищено 0 из 3" in c.labels
    await c.press("+ Практическая")
    await c.send("Сортировки, Графы")
    assert "Добавлено: Сортировки, Графы" in c.text
    await c.press("Работы")
    await c.press("ЛР 2")
    assert "Лабораторная №2" in c.text and "✅ Защитил" in c.labels
    await c.press("Защитил")
    assert "🎉 <b>Лабораторная №2</b> защищена!" in c.text
    assert "✅ ЛР 2" in c.labels
    await c.press("⬅️ К предмету")
    assert "Не защищены: ЛР 1, ЛР 3, Сортировки, Графы" in c.text
    await c.press("⬅️ Меню")
    assert "⏳ Не защищены: Программирование (ЛР 1, ЛР 3, Сортировки и ещё 1)" in c.text


async def test_work_rename_undefend_delete(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ")
    await c.press("+ Лабораторная")
    await c.press("➕ 1")
    await c.press("Работы")
    await c.press("ЛР 1")
    await c.press("Защитил")
    await c.press("ЛР 1")
    await c.press("Не защищена")
    assert "Статус: ⏳ не защищена" in c.text
    await c.press("Название")
    await c.send("Закон Ома")
    assert "Теперь работа называется «Закон Ома»" in c.text
    await c.press("Удалить")
    await c.press("Да, удалить")
    assert "«Закон Ома» удалена" in c.text


async def test_all_works_defended_celebration(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ")
    await c.press("+ Лабораторная")
    await c.press("➕ 1")
    await c.press("Работы")
    await c.press("ЛР 1")
    await c.press("Защитил")
    assert "Все работы по предмету защищены" in c.text


async def test_edit_grades_delete_and_restore(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ 6 7")
    await c.send("физ окр 9")
    await c.press("Изменить оценки")
    assert "📌9 ·" in " ".join(c.labels)
    await c.press("6 ·")
    assert "Удалена оценка 6" in c.text
    await c.press("Вернуть 6")
    assert "Оценка 6 возвращена" in c.text
    assert len([label for label in c.labels if "·" in label]) == 3


async def test_subject_settings(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ")
    await c.press("⚙️ Предмет")
    await c.press("нет ОКР")
    assert "ОКР в предмете: нет" in c.text
    await c.press("Переименовать")
    await c.send("🔭 Физика и астрономия")
    assert "→ «Физика и астрономия»" in c.text
    await c.press("Выше")
    assert "Место в списке: 1 из 4" in c.text
    await c.press("Удалить предмет")
    await c.press("Да, удалить")
    assert "удалён" in c.text
    assert not any("Физика" in label for label in c.labels)


async def test_add_subjects_later(env) -> None:
    c = env.client
    await onboard(c)
    await c.press("➕ Предметы")
    await c.send("Химия, Биология")
    assert "Добавлено: Химия, Биология" in c.text
    assert any("Химия" in label for label in c.labels)
    await c.press("➕ Предметы")
    await c.send("физ 8 9")  # перепутал экран — это оценки, а не предмет
    assert "Физика</b>: +8, +9" in c.text


async def test_reminder_flow(env) -> None:
    c = env.client
    await onboard(c)
    first_panel = c.panel.id
    panel = env.dp["panel"]
    env.clock.set_local(2026, 11, 12, 20, 5)  # на следующий день — рано (раз в 2 дня)
    assert await check_reminders(env.bot, env.repo, panel, env.storage) == 0
    env.clock.set_local(2026, 11, 13, 20, 5)
    assert await check_reminders(env.bot, env.repo, panel, env.storage) == 1
    assert "Пора обновить оценки" in c.text and "2 дня назад" in c.text
    assert c.panel.id != first_panel
    assert [m.id for m in c.visible] == [c.panel.id]  # старая панель убрана
    assert await check_reminders(env.bot, env.repo, panel, env.storage) == 0  # второй раз — нет
    await c.press("Напомнить через 2 часа")
    assert "напомню в 22:05" in c.text
    env.clock.advance(hours=2, minutes=1)
    assert await check_reminders(env.bot, env.repo, panel, env.storage) == 1


async def test_reminder_skipped_after_activity(env) -> None:
    c = env.client
    await onboard(c)
    env.clock.set_local(2026, 11, 13, 12, 0)
    await c.send("физ 9")
    env.clock.set_local(2026, 11, 13, 20, 5)
    assert await check_reminders(env.bot, env.repo, env.dp["panel"], env.storage) == 0


async def test_reminder_resets_pending_input(env) -> None:
    c = env.client
    await onboard(c)
    await c.press("➕ Предметы")  # бот ждёт названия
    env.clock.set_local(2026, 11, 13, 20, 5)
    await check_reminders(env.bot, env.repo, env.dp["panel"], env.storage)
    await c.send("физ 8")
    assert "Физика</b>: +8" in c.text  # не превратилось в предмет «физ 8»


async def test_blocked_user_pauses_reminders(env) -> None:
    c = env.client
    await onboard(c)
    env.tg.blocked.add(c.id)
    env.clock.set_local(2026, 11, 13, 20, 5)
    assert await check_reminders(env.bot, env.repo, env.dp["panel"], env.storage) == 0
    user = await env.repo.get_user(c.id)
    assert user.blocked
    env.tg.blocked.clear()
    await c.send("/menu")  # вернулся — снова активен
    assert not (await env.repo.get_user(c.id)).blocked


async def test_reminder_settings(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("/settings")
    await c.press("Напоминания")
    await c.press("Каждый день")
    await c.press("19:00")
    assert "каждый день</b> в <b>19:00" in c.text
    await c.press("Другое время")
    await c.send("7.30")
    assert "07:30" in c.text
    await c.press("Не беспокоить в воскресенье")
    assert "нет" in " ".join(c.labels)
    await c.press("Выкл")
    assert "выключены" in c.text
    await c.press("Прислать пример")
    assert "Так будет выглядеть напоминание" in c.text


async def test_timezone_and_scale_settings(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ 9")
    await c.send("/settings")
    await c.press("Часовой пояс")
    await c.send("Asia/Novosibirsk")
    assert "у тебя сейчас 19:00" in c.text
    await c.send("Марс")
    assert "Не знаю такой пояс" in c.text
    await c.press("Назад")
    await c.press("Шкала")
    await c.press("5-балльная")
    assert c.last_answer.show_alert and "выше 5" in c.last_answer.text
    await c.press("➕")
    assert "Порог ОКР: 5" in c.labels


async def test_clean_chat_off_keeps_history(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("/settings")
    await c.press("Чистый чат")
    await c.press("⬅️ Меню")
    await c.send("физ 8")
    await c.send("физ 9")
    users = [m for m in c.visible if not m.from_bot]
    assert len(users) == 2
    bots = [m for m in c.visible if m.from_bot]
    assert all(not m.kb for m in bots[:-1])  # у старых панелей кнопки сняты
    assert "+9" in c.text


async def test_export_csv(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ 8 9")
    await c.send("физ окр 7")
    await c.send("/settings")
    await c.press("Экспорт")
    docs = [m for m in c.visible if m.document is not None]
    assert len(docs) == 1
    content = docs[0].document.decode("utf-8-sig")
    assert docs[0].filename == "Оценки — Осень 2026.csv"
    assert "Физика;8,00;3;—;да;" in content
    assert "Физика;ОКР;7;;" in content
    assert c.panel.id > docs[0].id  # меню снова внизу, под файлом


async def test_new_semester(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ 8")
    await c.send("/settings")
    await c.press("Семестры")
    await c.press("Начать новый семестр")
    await c.press("Да, начать")
    await c.press("Да, перенести")
    assert "Перенёс 4 предмета" in c.text
    assert "оценок пока нет" in c.text
    await c.press("Настройки")
    await c.press("Семестры")
    await c.press("📁 Осень 2026 · 8,00")
    assert "архив" in c.text and "⚛️ Физика — 8,00" in c.text


async def test_new_semester_with_fresh_subjects(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("/settings")
    await c.press("Семестры")
    await c.press("Начать новый семестр")
    await c.press("Да, начать")
    await c.press("Введу новые")
    await c.send("Философия, Экономика")
    await c.press("Дальше")
    await c.press("Готово")
    assert "Предметы нового семестра готовы" in c.text
    assert any("Философия" in label for label in c.labels)
    assert not any("Физика" in label for label in c.labels)


async def test_wipe_restarts_onboarding(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("/settings")
    await c.press("Удалить все данные")
    await c.press("Да, удалить всё")
    assert "Все данные удалены" in c.text and "Шаг 1 из 3" in c.text


async def test_foreign_and_stale_buttons(env) -> None:
    c = env.client
    await onboard(c)
    await c.press_data(Subj(a="v", id=999_999).pack())
    assert "Предмет не найден" in c.text
    await c.press_data(WorkAct(a="def", id=123_456).pack())
    assert "уже удалена" in c.text
    await c.press_data("garbage:data")
    assert "устарела" in c.last_answer.text


async def test_other_user_cannot_touch_data(env) -> None:
    alice = env.client
    await onboard(alice)
    await alice.send("физ")
    physics_id = Subj.unpack(next(d for t, d in alice.panel.buttons() if t == "➕ Оценки")).id
    bob = Client(env.dp, env.bot, env.tg, user_id=888, name="Боб")
    await onboard(bob)
    await bob.press_data(Subj(a="del_yes", id=physics_id).pack())
    assert "Предмет не найден" in bob.text
    assert await env.repo.get_subject(await env.repo.get_user(alice.id), physics_id) is not None


async def test_commands_and_non_text(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("/help")
    assert "Как пользоваться" in c.text
    await c.send("/stats")
    assert "Статистика" in c.text
    await c.send("/unknown")
    assert "Не знаю такой команды" in c.text
    await c.send_sticker()
    assert "понимаю текст и кнопки" in c.text


async def test_stats_screen(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ 10 10 8")
    await c.send("прог 6")
    await c.send("/stats")
    text = c.text
    assert "Средний балл: <b>7,67</b>" in text  # (9,33 + 6) / 2 — среднее по предметам
    assert "Оценок: 4 · за неделю: +4" in text
    assert "🥇 ⚛️ Физика — 9,33" in text
    assert "<pre>" in text


async def test_private_mode() -> None:
    from bot import clock  # noqa: F401  (часы по умолчанию — реальные)
    from bot.db import connect
    from bot.repo import Repo

    db = await connect(":memory:")
    try:
        repo = Repo(db)
        tg = FakeTelegram()
        bot = make_bot(tg)
        dp = build_dispatcher(repo, SQLiteStorage(db, repo.lock), allowed_users=frozenset({1}))
        stranger = Client(dp, bot, tg, user_id=2)
        await stranger.send("/start")
        assert "личный бот" in stranger.text
        assert await repo.get_user(2) is None
        await bot.session.close()
    finally:
        await db.close()


async def test_error_handler_reports_problem(env, monkeypatch) -> None:
    c = env.client
    await onboard(c)

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(env.repo, "views", boom)
    await c.send("/menu")
    assert "что-то пошло не так" in c.text


def test_all_buttons_fit_telegram_limit() -> None:
    """callback_data любой кнопки должна быть ≤ 64 байт."""
    from bot.callbacks import Pick, UndoDelGrade

    for data in (
        Pick(s=9_999_999, v="-".join(["10"] * 15), o=1),
        UndoDelGrade(s=9_999_999, v=10, o=3, t=2_000_000_000, p=99),
    ):
        InlineKeyboardButton(text="x", callback_data=data.pack())
        assert len(data.pack().encode()) <= 64


async def test_many_subjects_in_one_message(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ 8 9, матан 7\nпрог окр 6\nхимия 5\nангл 10")
    text = c.text
    assert "Записал" in text
    assert "⚛️ Физика: +8, +9" in text and "📐 Высшая математика: +7" in text
    assert "💻 Программирование: ОКР — 6 ✅" in text
    assert "🇬🇧 Английский язык: +10" in text
    assert "<code>химия 5</code> — нет такого предмета" in text
    await c.press("Отменить оценки")
    assert "убрал 4 оценки" in c.text
    views = {v.name: v for v in await env.repo.views(await env.repo.get_user(c.id))}
    assert views["Физика"].values == [] and views["Программирование"].values == [6]  # ОКР осталась


async def test_numbers_then_subject_with_comma_is_single_entry(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("8 9, физ")
    assert "Физика</b>: +8, +9" in c.text


async def test_weekly_trend_on_dashboard(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ 6 6")
    env.clock.advance(days=8)
    await c.send("физ 10 10")
    await c.press("⬅️ Меню")
    assert "🗓 За неделю: +2 оценки · средний ▲2,00" in c.text  # было 6,00 → стало 8,00


async def test_add_works_screen_understands_grades(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("прог")
    await c.press("+ Лабораторная")
    await c.send("физ 8 9")  # перепутал экран
    assert "Физика</b>: +8, +9" in c.text
    view = next(v for v in await env.repo.views(await env.repo.get_user(c.id)) if v.name == "Программирование")
    assert view.works == []


async def test_fast_taps_are_processed_in_order(env) -> None:
    import asyncio

    c = env.client
    await onboard(c)
    await c.press("Физика")
    await c.press("➕ Оценки")
    panel = c.panel
    data = {text: d for text, d in panel.buttons()}
    await asyncio.gather(*(c.press_data(data[v], panel.id) for v in ("7", "8", "9", "10")))
    assert "Только что: 7, 8, 9, 10" in c.text
    view = next(v for v in await env.repo.views(await env.repo.get_user(c.id)) if v.name == "Физика")
    assert view.values == [7, 8, 9, 10]
