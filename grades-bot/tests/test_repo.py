from __future__ import annotations

from aiogram.fsm.storage.base import StorageKey

from bot.logic import MAX_SUBJECTS, MAX_WORKS_PER_SUBJECT
from bot.models import LAB, PRAC
from bot.repo import Repo
from bot.states import Inp
from bot.storage import SQLiteStorage


async def new_user(repo: Repo, uid: int = 1):
    user = await repo.ensure_user(uid, "Тест")
    return await repo.ensure_semester(user)


async def test_new_user_defaults(repo: Repo) -> None:
    user = await new_user(repo)
    assert (user.scale, user.pass_mark, user.remind_every, user.remind_time) == (10, 3, 2, "20:00")
    assert user.clean_chat and user.skip_sunday and not user.onboarded
    semester = await repo.get_semester(user.id, user.semester_id)
    assert semester is not None and semester.label == "1 курс · 1 семестр"  # ноябрь — 1-й семестр


async def test_add_subjects_skips_duplicates_and_garbage(repo: Repo) -> None:
    user = await new_user(repo)
    added, skipped = await repo.add_subjects(user, ["Физика", "физика", "Ёлки", "елки", "8 9", "Химия"])
    assert [s.name for s in added] == ["Физика", "Ёлки", "Химия"]
    assert skipped == ["физика", "елки", "8 9"]
    added, skipped = await repo.add_subjects(user, ["🧪 Физика"])  # та же физика, но с иконкой
    assert added == [] and skipped == ["🧪 Физика"]


async def test_subject_limit(repo: Repo) -> None:
    user = await new_user(repo)
    added, skipped = await repo.add_subjects(user, [f"Предмет {i}" for i in range(MAX_SUBJECTS + 3)])
    assert len(added) == MAX_SUBJECTS and len(skipped) == 3


async def test_grades_and_okr(repo: Repo) -> None:
    user = await new_user(repo)
    (physics,), _ = await repo.add_subjects(user, ["Физика"])
    ids = await repo.add_grades(user, physics.id, [8, 9, 10])
    assert ids == sorted(ids) and len(ids) == 3
    assert await repo.set_okr(user, physics.id, 1, 3) is None
    assert await repo.set_okr(user, physics.id, 1, 7) == 3  # замена, а не вторая оценка
    view = await repo.view(user, physics.id)
    assert view is not None
    assert view.values == [8, 9, 10, 7]
    assert view.ok
    assert await repo.set_okr(user, physics.id, 1, None) == 7
    view = await repo.view(user, physics.id)
    assert view is not None and not view.ok


async def test_reducing_okr_count_keeps_grades(repo: Repo) -> None:
    user = await new_user(repo)
    (chem,), _ = await repo.add_subjects(user, ["Химия"], okr_count=2)
    await repo.set_okr(user, chem.id, 1, 6)
    await repo.set_okr(user, chem.id, 2, 8)
    assert await repo.set_okr_count(user, chem.id, 1) == 1
    view = await repo.view(user, chem.id)
    assert view is not None
    assert sorted(view.values) == [6, 8]  # оценка за ОКР №2 стала обычной
    assert [g.value for g in view.regular] == [8]


async def test_undo_grades_only_touches_own_regular_grades(repo: Repo) -> None:
    user = await new_user(repo)
    other = await new_user(repo, 2)
    (mine,), _ = await repo.add_subjects(user, ["Физика"])
    (theirs,), _ = await repo.add_subjects(other, ["Физика"])
    their_ids = await repo.add_grades(other, theirs.id, [5, 6])
    assert await repo.delete_grades(user, theirs.id, their_ids) == 0
    assert await repo.delete_grades(user, mine.id, their_ids) == 0
    assert await repo.delete_grade(user, their_ids[0]) is None


async def test_works_numbering_and_limit(repo: Repo) -> None:
    user = await new_user(repo)
    (prog,), _ = await repo.add_subjects(user, ["Программирование"])
    labs = await repo.add_works(user, prog.id, LAB, [None, None, None])
    assert [w.num for w in labs] == [1, 2, 3]
    pracs = await repo.add_works(user, prog.id, PRAC, ["Сортировки"])
    assert (pracs[0].num, pracs[0].full) == (1, "Сортировки")
    await repo.delete_works(user, prog.id, [labs[2].id])
    more = await repo.add_works(user, prog.id, LAB, [None])
    assert more[0].num == 3  # номера продолжаются
    many = await repo.add_works(user, prog.id, LAB, [None] * (MAX_WORKS_PER_SUBJECT + 5))
    assert len(many) == MAX_WORKS_PER_SUBJECT - 4


async def test_defend_work_and_ownership(repo: Repo) -> None:
    user = await new_user(repo)
    other = await new_user(repo, 2)
    (prog,), _ = await repo.add_subjects(user, ["Программирование"])
    (lab,) = await repo.add_works(user, prog.id, LAB, [None])
    assert await repo.get_work(other, lab.id) is None
    await repo.set_defended(other, lab.id, True)  # чужой — ничего не меняется
    work = await repo.get_work(user, lab.id)
    assert work is not None and not work.passed
    await repo.set_work_status(user, lab.id, "failed")
    work = await repo.get_work(user, lab.id)
    assert work is not None and work.failed and not work.passed
    await repo.set_work_status(user, lab.id, "passed")
    work = await repo.get_work(user, lab.id)
    assert work is not None and work.passed and not work.failed


async def test_semesters_archive_and_copy(repo: Repo) -> None:
    user = await new_user(repo)
    (physics, _chem), _ = await repo.add_subjects(user, ["Физика", "Химия"])
    await repo.add_grades(user, physics.id, [9])
    await repo.set_okr_count(user, physics.id, 2)
    old_semester = user.semester_id
    await repo.update_semester(user.id, old_semester, course=2, term=2)
    new = await repo.start_new_semester(user)
    user = await repo.update_user(user.id)
    assert user.semester_id == new.id
    assert new.label == "3 курс · 1 семестр"  # после 2-го семестра — следующий курс
    assert await repo.get_subject(user, physics.id) is None  # архив только для чтения
    assert await repo.copy_subjects(user, old_semester, new.id) == 2
    copied = await repo.list_subjects(user.id, new.id)
    assert [(s.name, s.okr_count) for s in copied] == [("Физика", 2), ("Химия", 1)]
    assert [s.prev_marks for s in copied] == ["9", None]  # отметка за прошлый семестр сохранена, у химии оценок не было
    archived = await repo.views(user, old_semester)
    assert archived[0].values == [9]


async def test_move_subject(repo: Repo) -> None:
    user = await new_user(repo)
    added, _ = await repo.add_subjects(user, ["А-предмет", "Б-предмет", "В-предмет"])
    assert await repo.move_subject(user, added[2].id, -1)
    assert [s.name for s in await repo.list_subjects(user.id, user.semester_id)] == [
        "А-предмет",
        "В-предмет",
        "Б-предмет",
    ]
    assert not await repo.move_subject(user, added[0].id, -1)


async def test_delete_user_cascades(repo: Repo) -> None:
    user = await new_user(repo)
    (physics,), _ = await repo.add_subjects(user, ["Физика"])
    await repo.add_grades(user, physics.id, [7])
    await repo.delete_user(user.id)
    assert await repo.get_user(user.id) is None
    async with repo.db.execute("SELECT COUNT(*) FROM grades") as cur:
        assert (await cur.fetchone())[0] == 0


async def test_fsm_storage_roundtrip(repo: Repo) -> None:
    storage = SQLiteStorage(repo.db, repo.lock)
    key = StorageKey(bot_id=42, chat_id=1, user_id=1)
    assert await storage.get_state(key) is None and await storage.get_data(key) == {}
    await storage.set_state(key, Inp.grades)
    await storage.set_data(key, {"sid": 5, "added": [[1, 8]], "before": None})
    assert await storage.get_state(key) == Inp.grades.state
    assert await storage.get_data(key) == {"sid": 5, "added": [[1, 8]], "before": None}
    other = StorageKey(bot_id=42, chat_id=2, user_id=2)
    assert await storage.get_state(other) is None


async def test_work_grade_counts_and_follows_the_work(repo: Repo) -> None:
    user = await new_user(repo)
    (prog,), _ = await repo.add_subjects(user, ["Программирование"], okr_count=0)
    (lab,) = await repo.add_works(user, prog.id, LAB, [None])
    await repo.add_grades(user, prog.id, [6])
    assert await repo.set_work_grade(user, lab.id, 8) is None
    assert await repo.set_work_grade(user, lab.id, 10) == 8  # замена, а не вторая оценка
    view = await repo.view(user, prog.id)
    assert view is not None and view.current == [6, 10] and view.semester_exact == 8
    await repo.delete_works(user, prog.id, [lab.id])
    view = await repo.view(user, prog.id)
    assert view is not None and view.values == [6]  # оценка ушла вместе с работой
    assert await repo.delete_grades(user, prog.id, [g.id for g in view.grades]) == 1


async def test_subject_abbreviation_and_session_fields(repo: Repo) -> None:
    user = await new_user(repo)
    (toe,), _ = await repo.add_subjects(user, ["Теоретические основы электротехники (ТОЭ)"])
    assert (toe.name, toe.short) == ("Теоретические основы электротехники", "ТОЭ")
    added, skipped = await repo.add_subjects(user, ["Что-то другое (ТОЭ)"])
    assert added == [] and skipped == ["Что-то другое (ТОЭ)"]  # сокращение занято
    toe = await repo.update_subject(user, toe.id, control="exam", project=True)
    assert toe is not None and toe.control == "exam" and toe.project
    await repo.set_session_result(user, toe.id, exam_grade=9, project_grade=10)
    assert await repo.max_grade(user) == 10
    copied_to = await repo.start_new_semester(user)
    user = await repo.update_user(user.id)
    await repo.copy_subjects(user, toe.semester_id, copied_to.id)
    (copy,) = await repo.list_subjects(user.id, copied_to.id)
    assert (copy.short, copy.control, copy.project, copy.exam_grade) == ("ТОЭ", "exam", True, None)


async def test_access_by_username_and_requests(repo: Repo) -> None:
    assert not await repo.has_access(5, "ivan")
    assert await repo.grant(username="@Ivan")
    assert not await repo.grant(username="ivan")  # уже есть
    assert await repo.has_access(5, "ivan")
    assert await repo.has_access(5, None)  # id запомнился по юзернейму
    assert await repo.add_request(7, "petr", "Пётр")
    assert not await repo.add_request(7, "petr", "Пётр")
    assert [r["user_id"] for r in await repo.requests()] == [7]
    assert await repo.grant(user_id=7, username="petr")
    assert await repo.requests() == []  # заявка закрыта
    assert await repo.revoke(user_id=5, username="ivan") == 1
    assert not await repo.has_access(5, "ivan")
    await repo.set_meta("open_access", "1")
    assert await repo.meta("open_access") == "1"


async def test_migration_from_first_version(tmp_path) -> None:
    """Старая база (v1) обновляется без потери данных, порог 4 → 3."""
    import aiosqlite

    from bot.db import MIGRATIONS, migrate

    path = tmp_path / "old.db"
    async with aiosqlite.connect(path) as db:
        await db.executescript(MIGRATIONS[0] + "\nPRAGMA user_version = 1;")
        await db.execute("INSERT INTO users (id, name, created_at) VALUES (1, 'Старый', 0)")
        await db.execute("INSERT INTO semesters (user_id, title, created_at) VALUES (1, 'Осень 2026', 0)")
        await db.execute("INSERT INTO subjects (user_id, semester_id, name, created_at) VALUES (1, 1, 'Физика', 0)")
        await db.execute("INSERT INTO grades (subject_id, value, created_at) VALUES (1, 8, 0)")
        await db.commit()
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await migrate(db)
        repo = Repo(db)
        user = await repo.get_user(1)
        assert user is not None and user.pass_mark == 3 and user.works_reminders
        user = await repo.update_user(1, semester_id=1)
        (view,) = await repo.views(user)
        assert view.values == [8] and view.subject.control == "none"


async def test_subject_continues_by_name_after_archive(repo: Repo) -> None:
    """Предмет с тем же названием в новом семестре — продолжение: отметка за прошлый семестр подтягивается."""
    user = await new_user(repo)
    (toe, _), _ = await repo.add_subjects(user, ["Теоретические основы электротехники (ТОЭ)", "История"])
    assert toe.prev_marks is None  # первый семестр — прошлых отметок нет
    await repo.add_grades(user, toe.id, [7, 8])  # 7,5 → 8
    await repo.start_new_semester(user)
    user = await repo.update_user(user.id)
    (again, other), _ = await repo.add_subjects(user, ["ТОЭ", "Физика"])
    assert (again.prev_marks, other.prev_marks) == ("8", None)
    view = await repo.view(user, again.id)
    assert view is not None and view.previous == [8] and view.course_marks == []  # оценок в этом семестре ещё нет
    await repo.add_grades(user, again.id, [9])
    view = await repo.view(user, again.id)
    assert view.course_marks == [8, 9] and view.base_grade == 9  # (8 + 9) / 2 = 8,5 → 9
