"""Сквозные сценарии: пользователь пишет и нажимает кнопки, бот отвечает как в Telegram.

Каждое сообщение бота проходит проверку HTML, как у Telegram (tests/harness.py).
"""

from __future__ import annotations

import asyncio

import pytest
from aiogram.types import InlineKeyboardButton

from bot.callbacks import Adm, Subj, WorkAct
from bot.reminders import check_reminders
from tests.conftest import onboard
from tests.harness import Client, read_xlsx


def subject_id(client: Client) -> int:
    return Subj.unpack(next(d for t, d in client.panel.buttons() if t == "➕ Оценки")).id


async def views(env, client: Client) -> dict:
    user = await env.repo.get_user(client.id)
    return {v.name: v for v in await env.repo.views(user)}


async def remind(env) -> int:
    return await check_reminders(env.bot, env.repo, env.dp["panel"], env.storage)


# ---------------------------------------------------------------- знакомство и главное меню


async def test_onboarding_college_flow(env) -> None:
    c = env.client
    await c.send("/start")
    assert "Шаг 1 из 4" in c.text and "✅ 1 семестр" in c.labels  # ноябрь — 1-й семестр
    await c.press("2 семестр")
    assert "✅ 2 семестр" in c.labels
    await c.press("1 семестр")
    await c.press("2 курс")
    assert "Шаг 2 из 4" in c.text
    await c.send("Высшая математика, Физика\nТеоретические основы электротехники (ТОЭ); Физкультура, физика")
    assert "Добавлено (4)" in c.text and "(ТОЭ)" in c.text
    assert "Пропущено" in c.text
    await c.press("Дальше")
    assert "Шаг 3 из 4" in c.text and "✅ 📌 ОКР" in c.labels
    await c.press("Физкультура")
    assert "▫️ Физкультура — без ОКР" in c.labels
    await c.press("🎓 Экзамены")
    await c.press("Высшая математика")
    assert "🎓 Высшая математика — экзамен" in c.labels
    await c.press("ТОЭ")
    await c.press("ТОЭ")
    assert "📝 ТОЭ — диф. зачёт" in c.labels
    await c.press("📐 Курсовые")
    await c.press("ТОЭ")
    assert "📐 ТОЭ — курсовой" in c.labels
    await c.press("Дальше")
    assert "Шаг 4 из 4" in c.text and "Когда сессия" in c.text
    await c.send("22 декабря – 14 января")
    assert "✅ Сессия: 22.12–14.01" in c.text and "каникулы" in c.text
    await c.send("15.01-08.02")
    text = c.text
    assert "Готово!" in text and "2 курс · 1 семестр" in text
    assert "До сессии 41 день (22.12–14.01)" in text
    assert "🎓 Сессия: 1 экзамен, 1 диф. зачёт" in text
    assert "Нет ОКР: Высшая математика, Физика, ТОЭ" in text
    assert "Курсовой не защищён: ТОЭ" in text
    assert any(label.startswith("🏃 Физкультура") and label.endswith("▫️") for label in c.labels)  # оценок ещё нет
    assert [m.from_bot for m in c.visible] == [True]  # чистый чат: одно сообщение бота


async def test_onboarding_can_skip_dates(env) -> None:
    c = env.client
    await onboard(c)
    assert "Готово!" in c.text and "До сессии" not in c.text


# ---------------------------------------------------------------- оценки


async def test_keypad_grades_undo_and_done(env) -> None:
    c = env.client
    await onboard(c)
    await c.press("Физика")
    await c.press("➕ Оценки")
    await c.press("8", exact=True)
    assert c.last_answer.text == "+8 → за семестр 8,00"
    await c.press("10", exact=True)
    await c.press("9", exact=True)
    assert "Только что: 8, 10, 9" in c.text
    await c.press("Убрать последнюю (9)")
    assert "Только что: 8, 10" in c.text
    await c.press("Готово")
    assert "✅ Добавлено: 8, 10" in c.text
    assert "📝 8, 10" in c.text
    assert "За семестр: <b>9</b> (9,00)" in c.text


async def test_text_grades_on_subject_card_with_undo(env) -> None:
    c = env.client
    await onboard(c)
    await c.press("Физика")
    await c.send("7, 9/10")
    assert "+7, +9, +10" in c.text and "📝 7, 9, 10" in c.text
    await c.press("Отменить")
    assert "убрал 3 оценки" in c.text and "Оценок пока нет" in c.text


async def test_glued_digits_for_several_subjects(env) -> None:
    c = env.client
    await onboard(c, subjects="Физическая культура, Охрана труда, Физика, ТОЭ", no_okr=())
    await c.send("физра 10910\nохрана 99910\nфизика 101010\nтоэ 1078105")
    text = c.text
    assert "🏃 Физическая культура: +10, +9, +10" in text
    assert "🦺 Охрана труда: +9, +9, +9, +10" in text
    assert "⚛️ Физика: +10, +10, +10" in text
    assert "ТОЭ: +10, +7, +8, +10, +5" in text
    found = await views(env, c)
    assert found["Охрана труда"].values == [9, 9, 9, 10]
    await c.press("Отменить оценки")
    assert "убрал 15 оценок" in c.text


async def test_quick_add_from_main_menu(env) -> None:
    c = env.client
    await onboard(c, subjects="Высшая математика, Физика, Теоретические основы электротехники (ТОЭ)", no_okr=())
    await c.send("тоэ 8 9")
    assert "Теоретические основы электротехники</b>: +8, +9" in c.text
    await c.press("⬅️ Меню")
    await c.send("1078")
    assert "Куда записать оценки 10, 7, 8?" in c.text
    await c.press("Физика")
    assert "Физика</b>: +10, +7, +8" in c.text
    await c.press("⬅️ Меню")
    await c.send("химия 5")
    assert "Не нашёл предмет «химия»" in c.text


async def test_quick_add_rejects_bad_grades(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ 8 100")
    assert "Не понял оценки: «100»" in c.text
    assert (await views(env, c))["Физика"].values == []


async def test_quick_open_subject_and_unknown_text(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("вышка")
    assert "📐 Высшая математика" in c.text and "➕ Оценки" in c.labels
    await c.send("привет, как дела")
    assert "Не понял" in c.text


async def test_okr_quick_and_attestation(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ окр 2")
    assert "ОКР — <b>2</b> ❌ ниже 3" in c.text
    await c.press("⬅️ Меню")
    assert "ОКР ниже 3: Физика — 2" in c.text
    await c.send("физ окр 3")  # в ССО 3 — уже положительная отметка
    assert "ОКР — <b>3</b> ✅ сдана (было 2)" in c.text
    await c.press("Отменить")
    assert "Вернул прежнюю оценку за ОКР: 2" in c.text


async def test_okr_screen_keypad(env) -> None:
    c = env.client
    await onboard(c)
    await c.press("Физика")
    await c.press("📌 ОКР")
    assert "Сейчас: оценки нет" in c.text
    await c.press("6", exact=True)
    assert "ОКР: <b>6</b> ✅" in c.text
    await c.press("📌 ОКР")
    await c.send("8")
    assert "ОКР — <b>8</b> ✅ сдана (было 6)" in c.text


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


# ---------------------------------------------------------------- работы


async def test_works_pass_fail_and_grade(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("прог 6")
    assert not any("Работы" in label for label in c.labels)
    await c.press("+ Лабораторная")
    await c.press("+3")
    assert "Добавлено: ЛР 1–3" in c.text and "📂 Работы · зачтено 0 из 3" in c.labels
    await c.press("+ Практическая")
    await c.send("Сортировки, Графы")
    assert "Добавлено: Сортировки, Графы" in c.text
    await c.press("Работы")
    await c.press("ЛР 1")
    assert "✅ Зачтено" in c.labels and "❌ Не зачтено" in c.labels and "⬅️ Назад" in c.labels
    await c.press("Зачтено")
    assert "— зачтено!" in c.text and "Поставили оценку?" in c.text
    await c.press("10", exact=True)
    assert "ЛР 1</b>: оценка 10 · за семестр 6,00 → <b>8,00</b>" in c.text  # оценка за работу — в средний
    assert "✅ ЛР 1 · 10" in c.labels
    await c.press("ЛР 2")
    await c.press("Не зачтено")
    assert "не зачтено" in c.text and "❌ ЛР 2" in c.labels
    await c.press("Сортировки")
    await c.press("Зачтено")
    await c.press("Без оценки")  # практическая только «зачтено»
    assert "✅ Сортировки" in c.labels
    await c.press("⬅️ К предмету")
    assert "не зачтено: ЛР 2" in c.text and "осталось: ЛР 3, Графы" in c.text
    await c.press("⬅️ Меню")
    assert "Не зачтены работы: Программирование (ЛР 2, ЛР 3, Графы)" in c.text


async def test_work_rename_reset_delete(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ")
    await c.press("+ Лабораторная")
    await c.press("➕ 1")
    await c.press("Работы")
    await c.press("ЛР 1")
    await c.press("Зачтено")
    await c.press("8", exact=True)
    await c.press("ЛР 1")
    assert "Оценка: <b>8</b>" in c.text
    await c.press("Снять зачёт")
    assert "Статус: ⏳ ещё не сдавал" in c.text
    await c.press("Название")
    await c.send("Закон Ома")
    assert "Теперь работа называется «Закон Ома»" in c.text
    await c.press("Удалить")
    await c.press("Да, удалить")
    assert "«Закон Ома» удалена" in c.text
    assert (await views(env, c))["Физика"].values == []  # оценка ушла вместе с работой


async def test_all_works_passed_celebration(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ")
    await c.press("+ Лабораторная")
    await c.press("➕ 1")
    await c.press("Работы")
    await c.press("ЛР 1")
    await c.press("Зачтено")
    assert "Все работы по предмету зачтены" in c.text


# ---------------------------------------------------------------- экзамены, зачёты, курсовые


async def test_exam_admission_retake_and_debt(env) -> None:
    c = env.client
    await onboard(c, exams={"Физика": 1})
    await c.send("физ")
    assert "Нет допуска к экзамену" in c.text
    await c.send("9 10 9")
    await c.send("физ окр 9")
    assert "Допуск к экзамену есть" in c.text
    await c.press("Экзамен")
    assert "Итог при отметке:" in c.text and "Автомат (= 9)" in " ".join(c.labels)
    await c.press("2", exact=True)
    assert "академическая задолженность" in c.text
    assert "Задолженность:" in c.text and "п. 26" in c.text
    await c.press("⬅️ Меню")
    assert "🆘 Задолженности: Физика (экзамен на 2)" in c.text
    await c.send("физ")
    await c.press("Экзамен")
    await c.send("10")
    assert "Экзамен: <b>10</b> → итог <b>10</b>" in c.text  # (9 + 10) / 2 = 9,5 → 10


async def test_exam_automat(env) -> None:
    c = env.client
    await onboard(c, exams={"Физика": 1})
    await c.send("физ 9 10 9")
    await c.send("физ окр 9")
    await c.press("Экзамен")
    await c.press("Автомат")
    assert c.last_answer.text == "🎉 Поздравляю с автоматом!"
    assert "Итог за семестр: 9" in c.text


async def test_credit_and_session_complete(env) -> None:
    c = env.client
    await onboard(c, no_okr=("Английский", "Физика", "Программирование", "Высшая"), exams={"Английский": 3})
    await c.send("англ")
    await c.press("Зачёт")
    await c.press("Не зачтено")
    assert "Задолженность:" in c.text
    await c.press("Зачёт")
    await c.press("✅ Зачтено")
    assert "Все экзамены и зачёты сданы" in c.text
    assert "🗂 Все экзамены сданы — в архив" in c.labels


async def test_course_project(env) -> None:
    c = env.client
    await onboard(c, projects=("Программирование",))
    await c.send("прог 6 8")
    await c.send("прог окр 8")
    assert "курсовой проект не защищён" in c.text
    await c.press("Курсовой")
    await c.press("10", exact=True)
    assert "Курсовой защищён на <b>10</b>" in c.text
    assert "(ОКР 8 + курсовой 10 + текущие 7) / 3 = 8,33" in c.text  # п. 22


async def test_calc_modes_change_the_card(env) -> None:
    c = env.client
    await onboard(c, exams={"Физика": 1})
    await c.send("физ 6 8")
    await c.send("физ окр 10")
    assert "(ОКР 10 + текущие 7) / 2 = 8,50" in c.text
    await c.send("/settings")
    await c.press("Как считать")
    await c.press("Простое среднее")
    await c.press("Только экзамен")
    await c.send("физ")
    assert "За семестр: <b>8</b> (8,00)" in c.text and "🧮" not in c.text
    await c.press("Экзамен")
    assert "Итог = отметка за экзамен" in c.text


# ---------------------------------------------------------------- семестры и архив


async def test_archive_and_next_semester(env) -> None:
    c = env.client
    await onboard(c, course="1 курс")
    await c.send("физ 8")
    await c.press("⬅️ Меню")
    await c.press("🗂 Архив")
    assert "Архив пуст" in c.text
    assert "за всё обучение" not in c.text  # текущий семестр не закончен — не считаем
    await c.press("Отправить текущий семестр в архив")
    assert "Отправить «1 курс · 1 семестр» в архив?" in c.text
    await c.press("Да, в архив")
    assert "теперь в архиве" in c.text and "Начат <b>1 курс · 2 семестр</b>" in c.text
    await c.press("Перенести список")
    assert "Перенёс 4 предмета" in c.text and "оценок пока нет" in c.text
    await c.press("🗂 Архив")
    assert "🎓 Средний за всё обучение: <b>8,00</b> (семестров в архиве: 1)" in c.text
    await c.press("📁 1 курс · 1 семестр · 8,00")
    assert "архив" in c.text and "⚛️ Физика —" in c.text
    assert "📊 Средний балл: <b>8,00</b> = 8 / 1" in c.text


async def test_archive_with_fresh_subjects_and_course_change(env) -> None:
    c = env.client
    await onboard(c, course="2 курс")
    await c.send("/settings")
    await c.press("Семестр и сессия")
    await c.press("2 семестр")
    assert "2 курс · 2 семестр" in c.text
    await c.press("Отправить семестр в архив")
    await c.press("Да, в архив")
    assert "Начат <b>3 курс · 1 семестр</b>" in c.text  # после 2-го семестра — следующий курс
    await c.press("Ввести предметы")
    await c.send("Философия, Экономика")
    await c.press("Дальше")
    await c.press("Дальше")
    await c.press("Пропустить")
    await c.press("Пропустить")
    assert "Новый семестр готов" in c.text
    assert any("Философия" in label for label in c.labels)


async def test_bot_asks_to_archive_after_session(env) -> None:
    c = env.client
    await onboard(c, session="22.12-14.01", holidays="15.01-08.02")
    await c.send("физ 8")
    env.clock.set_local(2027, 1, 15, 20, 5)
    assert await remind(env) == 1
    assert "Сессия закончилась!" in c.text and "Отправить семестр в архив?" in c.text
    await c.press("Спросить позже")
    env.clock.set_local(2027, 1, 16, 20, 5)
    assert await remind(env) == 0  # спросим через пару дней, а сейчас каникулы
    env.clock.set_local(2027, 1, 18, 20, 5)
    assert await remind(env) == 1
    await c.press("Да, в архив")
    assert "теперь в архиве" in c.text


async def test_semester_dates_in_settings(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("/settings")
    await c.press("Семестр и сессия")
    await c.press("Даты сессии")
    await c.send("абв")
    assert "Не понял даты" in c.text
    await c.send("22.12–14.01")
    assert "✅ Сессия: 22.12–14.01" in c.text
    await c.press("Спрашивать про архив")
    assert "Про архив спрашивать не буду" in c.text


# ---------------------------------------------------------------- напоминания


async def test_reminder_flow(env) -> None:
    c = env.client
    await onboard(c)
    first_panel = c.panel.id
    env.clock.set_local(2026, 11, 12, 20, 5)
    assert await remind(env) == 0  # раз в 2 дня
    env.clock.set_local(2026, 11, 13, 20, 5)
    assert await remind(env) == 1
    assert "Пора обновить оценки" in c.text and "2 дня назад" in c.text
    assert c.panel.id != first_panel and [m.id for m in c.visible] == [c.panel.id]
    assert await remind(env) == 0
    await c.press("Напомнить через 2 часа")
    assert "напомню в 22:05" in c.text
    env.clock.advance(hours=2, minutes=1)
    assert await remind(env) == 1


async def test_works_reminder_even_when_grades_are_fresh(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ")
    await c.press("+ Лабораторная")
    await c.press("+2")
    env.clock.set_local(2026, 11, 12, 20, 5)
    await c.send("физ 9")  # оценки свежие
    env.clock.set_local(2026, 11, 13, 20, 5)
    assert await remind(env) == 1
    assert "Не забудь про работы!" in c.text and "Осталось зачесть: Физика — ЛР 1, ЛР 2" in c.text


async def test_no_reminders_on_holidays(env) -> None:
    c = env.client
    await onboard(c, session="22.12-14.01", holidays="15.01-08.02")
    await c.send("/settings")
    await c.press("Семестр и сессия")
    await c.press("Спрашивать про архив")
    env.clock.set_local(2027, 1, 25, 20, 5)
    assert await remind(env) == 0


async def test_reminder_resets_pending_input(env) -> None:
    c = env.client
    await onboard(c)
    await c.press("➕ Предметы")
    env.clock.set_local(2026, 11, 13, 20, 5)
    await remind(env)
    await c.send("физ 8")
    assert "Физика</b>: +8" in c.text


async def test_blocked_user_pauses_reminders(env) -> None:
    c = env.client
    await onboard(c)
    env.tg.blocked.add(c.id)
    env.clock.set_local(2026, 11, 13, 20, 5)
    assert await remind(env) == 0
    assert (await env.repo.get_user(c.id)).blocked
    env.tg.blocked.clear()
    await c.send("/menu")
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
    await c.press("Про незачтённые работы")
    assert "Про работы не напоминаю" in c.text
    await c.press("Не беспокоить в воскресенье")
    await c.press("Выкл")
    assert "выключены" in c.text
    await c.press("Прислать пример")
    assert "Так будет выглядеть напоминание" in c.text


# ---------------------------------------------------------------- прочие настройки


async def test_subject_settings(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("физ")
    await c.press("⚙️ Предмет")
    await c.press("Экзамен")
    assert "В сессию: <b>экзамен</b>" in c.text
    await c.press("нет ОКР")
    await c.press("Курсовой: нет")
    assert "Курсовой проект: <b>есть</b>" in c.text
    await c.press("Сокращение")
    await c.send("ФИЗ")
    assert "Сокращение: «ФИЗ»" in c.text
    await c.press("Общеобразовательный")
    assert "Общеобразовательный: за семестр — простое среднее" in c.text
    await c.press("Переименовать")
    await c.send("🔭 Физика и астрономия")
    assert "→ «Физика и астрономия»" in c.text
    await c.press("Выше")
    assert "Место в списке: 1 из 4" in c.text
    await c.press("Удалить предмет")
    await c.press("Да, удалить")
    assert "удалён" in c.text and not any("Физика" in label for label in c.labels)


async def test_add_subjects_later_opens_setup(env) -> None:
    c = env.client
    await onboard(c)
    await c.press("➕ Предметы")
    await c.send("Химия, Охрана труда (ОТ)")
    assert "Добавлено: Химия, Охрана труда (ОТ)" in c.text
    assert "📌 ОТ — есть ОКР" in c.labels and "✅ Готово" in c.labels  # сразу можно настроить
    await c.press("ОТ")
    await c.press("🎓 Экзамены")
    await c.press("Химия")
    await c.press("Готово")
    assert "Готово — предметы настроены" in c.text
    assert any(label.startswith("🦺 ОТ") for label in c.labels)  # на кнопке — сокращение
    found = await views(env, c)
    assert found["Охрана труда"].subject.okr_count == 0 and found["Химия"].control == "exam"
    await c.press("➕ Предметы")
    await c.send("физ 8 9")
    assert "Физика</b>: +8, +9" in c.text


async def test_subject_without_exam_and_okr(env) -> None:
    c = env.client
    await onboard(c, subjects="Физкультура, Физика", no_okr=("Физкультура",))
    await c.send("физра")
    assert "Без экзамена и ОКР — итог: отметка за семестр" in c.text
    assert "📌 ОКР" not in c.labels and "Оценок пока нет" in c.text
    await c.send("10910")
    assert "Аттестован</b> · за семестр выходит <b>10</b>" in c.text
    await c.press("+ Лабораторная")
    await c.press("➕ 1")
    assert "Не аттестован:</b> не зачтены ЛР 1" in c.text  # без ОКР и экзамена важны только работы
    await c.press("⬅️ Меню")
    assert "Нет ОКР: Физика" in c.text and "Физкультура" not in c.text.split("Нет ОКР:")[1].split("\n")[0]


async def test_bulk_toggles_in_setup(env) -> None:
    c = env.client
    await c.send("/start")
    await c.press("1 курс")
    await c.send("Физика, Химия, История")
    await c.press("Дальше")
    await c.press("Ни в одном нет")
    assert all("без ОКР" in label for label in c.labels if "—" in label)
    await c.press("Во всех есть")
    assert all("есть ОКР" in label for label in c.labels if "—" in label)
    await c.press("🎓 Экзамены")
    await c.press("Физика")
    await c.press("Химия")
    await c.press("Сбросить: все без экзамена")
    assert all("без экзамена" in label for label in c.labels if "—" in label and "Сбросить" not in label)


async def test_fresh_semester_without_okr_says_no_problems(env) -> None:
    c = env.client
    await onboard(c, no_okr=("Высшая", "Физика", "Программирование", "Английский"))
    assert "Проблем нет</b> · пока без оценок" in c.text


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
    assert "Сдано от: 3" in c.labels
    await c.press("5-балльная")
    assert c.last_answer.show_alert and "выше 5" in c.last_answer.text
    await c.press("➕")
    assert "Сдано от: 4" in c.labels


async def test_clean_chat_off_keeps_history(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("/settings")
    await c.press("Чистый чат")
    await c.press("⬅️ Меню")
    await c.send("физ 8")
    await c.send("физ 9")
    assert len([m for m in c.visible if not m.from_bot]) == 2
    bots = [m for m in c.visible if m.from_bot]
    assert all(not m.kb for m in bots[:-1])


async def test_export_xlsx(env) -> None:
    c = env.client
    await onboard(c, exams={"Физика": 1})
    await c.send("физ 8 9")
    await c.send("физ окр 7")
    await c.send("прог 6 7")
    await c.send("/settings")
    await c.press("Экспорт в Excel")
    (doc,) = [m for m in c.visible if m.document is not None]
    assert doc.filename == "Оценки 2 курс 1 семестр.xlsx"
    book = read_xlsx(doc.document)
    assert list(book) == ["Итоги", "Все оценки"]
    summary, grades = book["Итоги"], book["Все оценки"]
    assert summary[0] == ["Оценки — 2 курс · 1 семестр"]
    assert summary[3][:4] == ["Предмет", "За семестр", "Средний", "Оценок"]
    rows = {r[0]: r for r in summary if r}
    assert rows["Физика"][:8] == ["Физика", 8, 7.75, 3, 7, None, None, "экзамен"]
    assert rows["Средний балл"][1] == 7.5  # (8 + 7) / 2: отметки за семестр по физике и программированию
    assert rows["Средний балл"][10].startswith("= 15 / 2")
    assert rows["Без округления"][1] == pytest.approx(7.125)  # (7,75 + 6,5) / 2
    assert ["Физика", "ОКР", 7] in [r[:3] for r in grades]
    assert c.panel.id > doc.id


async def test_wipe_restarts_onboarding(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("/settings")
    await c.press("Удалить всё")
    await c.press("Да, удалить всё")
    assert "Все данные удалены" in c.text and "Шаг 1 из 4" in c.text


async def test_stats_screen(env) -> None:
    c = env.client
    await onboard(c, no_okr=("Английский", "Физика", "Программирование", "Высшая"), exams={"Физика": 1})
    await c.send("физ 10 10 8")
    await c.send("прог 6")
    await c.send("физ")
    await c.press("Экзамен")
    await c.press("8", exact=True)
    await c.send("/stats")
    text = c.text
    assert "Средний балл: <b>7,50</b> = 15 / 2" in text  # итог по физике (9 + 8) / 2 = 8,5 → 9 и прог 6
    assert "Без округления: 7,67" in text  # (9,33 + 6) / 2
    assert "Средний балл сессии (экзамены и диф. зачёты): <b>8,00</b>" in text
    assert "🥇 ⚛️ Физика — 9,33" in text and "<pre>" in text


# ---------------------------------------------------------------- доступ и админка


async def test_private_by_default_and_access_request(env) -> None:
    owner = env.client
    await onboard(owner)
    stranger = Client(env.dp, env.bot, env.tg, user_id=999, name="Петя", username="petya")
    await stranger.send("/start")
    assert "Это личный бот" in stranger.text and await env.repo.get_user(999) is None
    await stranger.press("Попросить доступ")
    assert "Заявка отправлена владельцу" in stranger.last_answer.text
    await stranger.press("Попросить доступ")
    assert "уже у владельца" in stranger.last_answer.text
    assert "Заявка на доступ" in owner.text and "Петя (@petya)" in owner.text
    await owner.press("Пустить")
    assert "Доступ открыт: Петя" in owner.text
    assert "открыл тебе доступ" in stranger.text
    await stranger.send("/start")
    assert "Шаг 1 из 4" in stranger.text


async def test_admin_adds_by_username_and_views_silently(env) -> None:
    owner = env.client
    await onboard(owner)
    await owner.send("/apanel")
    assert "Админ-панель" in owner.text
    await owner.press("Добавить по юзернейму")
    await owner.send("@Petya, @kolya_2000 xx")
    assert "Добавлены: @petya, @kolya_2000" in owner.text and "Не похоже на юзернейм: xx" in owner.text
    petya = Client(env.dp, env.bot, env.tg, user_id=555, name="Петя", username="petya")
    await onboard(petya)
    await petya.send("физ 9 8")
    before = len(env.tg.chat(petya.id))
    await owner.send("/apanel")
    await owner.press("Пользователи")
    assert "⏳ Приглашены, но ещё не заходили: @kolya_2000" in owner.text
    await owner.press("👤 Петя")
    assert "⚛️ Физика — 9 (8,50) ❌ (нет оценки за ОКР)" in owner.text
    assert len(env.tg.chat(petya.id)) == before  # просмотр тихий — Пете ничего не пришло
    assert (await views(env, owner))["Физика"].values == []  # у каждого свои данные
    await owner.press("Закрыть доступ")
    assert "Доступ закрыт" in owner.text
    await petya.send("/menu")
    assert "Это личный бот" in petya.text


async def test_open_access_toggle(env) -> None:
    owner = env.client
    await onboard(owner)
    await owner.send("/apanel")
    await owner.press("Открыть для всех")
    assert "Бот открыт для всех" in owner.text
    guest = Client(env.dp, env.bot, env.tg, user_id=321, name="Гость", username=None)
    await guest.send("/start")
    assert "Шаг 1 из 4" in guest.text


async def test_apanel_is_hidden_for_others(env) -> None:
    await env.repo.grant(username="bob")
    bob = Client(env.dp, env.bot, env.tg, user_id=888, name="Боб", username="bob")
    await onboard(bob)
    await bob.send("/apanel")
    assert "Не знаю такой команды" in bob.text
    await bob.press_data(Adm(a="users").pack())
    assert "Админ-панель" not in bob.text
    await bob.send("/help")
    assert "/apanel" not in bob.text


async def test_owner_is_pinned_by_id(env) -> None:
    owner = env.client
    await owner.send("/start")
    impostor = Client(env.dp, env.bot, env.tg, user_id=4242, name="Самозванец", username="ztpst")
    await impostor.send("/start")
    assert "Это личный бот" in impostor.text  # юзернейм тот же, но владелец уже закреплён по id


async def test_other_user_cannot_touch_data(env) -> None:
    alice = env.client
    await onboard(alice)
    await alice.send("физ")
    physics_id = subject_id(alice)
    await env.repo.grant(username="bob")
    bob = Client(env.dp, env.bot, env.tg, user_id=888, name="Боб", username="bob")
    await onboard(bob)
    await bob.press_data(Subj(a="del_yes", id=physics_id).pack())
    assert "Предмет не найден" in bob.text
    assert await env.repo.get_subject(await env.repo.get_user(alice.id), physics_id) is not None


# ---------------------------------------------------------------- устойчивость


async def test_foreign_and_stale_buttons(env) -> None:
    c = env.client
    await onboard(c)
    await c.press_data(Subj(a="v", id=999_999).pack())
    assert "Предмет не найден" in c.text
    await c.press_data(WorkAct(a="pass", id=123_456).pack())
    assert "уже удалена" in c.text
    await c.press_data("garbage:data")
    assert "устарела" in c.last_answer.text


async def test_commands_and_non_text(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("/help")
    assert "/archive — архив семестров" in c.text and "/apanel" in c.text  # владелец видит админку
    await c.send("/stats")
    assert "Статистика" in c.text
    await c.send("/archive")
    assert "Архив" in c.text
    await c.send("/unknown")
    assert "Не знаю такой команды" in c.text
    await c.send_sticker()
    assert "понимаю текст и кнопки" in c.text


async def test_error_handler_reports_problem(env, monkeypatch) -> None:
    c = env.client
    await onboard(c)

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(env.repo, "views", boom)
    await c.send("/menu")
    assert "что-то пошло не так" in c.text and "🏠 Меню" in c.labels


async def test_fast_taps_are_processed_in_order(env) -> None:
    c = env.client
    await onboard(c)
    await c.press("Физика")
    await c.press("➕ Оценки")
    panel = c.panel
    data = {text: d for text, d in panel.buttons()}
    await asyncio.gather(*(c.press_data(data[v], panel.id) for v in ("7", "8", "9", "10")))
    assert "Только что: 7, 8, 9, 10" in c.text
    assert (await views(env, c))["Физика"].values == [7, 8, 9, 10]


async def test_numbers_then_subject_with_comma_is_single_entry(env) -> None:
    c = env.client
    await onboard(c)
    await c.send("8 9, физ")
    assert "Физика</b>: +8, +9" in c.text


async def test_weekly_trend_on_dashboard(env) -> None:
    c = env.client
    await onboard(c, no_okr=("Английский", "Физика", "Программирование", "Высшая"))
    await c.send("физ 6 6")
    env.clock.advance(days=8)
    await c.send("физ 10 10")
    await c.press("⬅️ Меню")
    assert "📆 За неделю: +2 оценки · средний ▲2,00" in c.text


def test_all_buttons_fit_telegram_limit() -> None:
    """callback_data любой кнопки должна быть ≤ 64 байт."""
    from bot.callbacks import Pick, UndoDelGrade

    for data in (
        Pick(s=9_999_999, v="-".join(["10"] * 15), o=1),
        UndoDelGrade(s=9_999_999, v=10, o=3, t=2_000_000_000, p=99, w=9_999_999),
    ):
        InlineKeyboardButton(text="x", callback_data=data.pack())
        assert len(data.pack().encode()) <= 64
