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
    assert (user.scale, user.pass_mark, user.remind_every, user.remind_time) == (10, 4, 2, "20:00")
    assert user.clean_chat and user.skip_sunday and not user.onboarded
    semester = await repo.get_semester(user.id, user.semester_id)
    assert semester is not None and semester.title == "Осень 2026"


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
    assert work is not None and not work.defended
    await repo.set_defended(user, lab.id, True)
    work = await repo.get_work(user, lab.id)
    assert work is not None and work.defended


async def test_semesters_archive_and_copy(repo: Repo) -> None:
    user = await new_user(repo)
    (physics, _chem), _ = await repo.add_subjects(user, ["Физика", "Химия"])
    await repo.add_grades(user, physics.id, [9])
    await repo.set_okr_count(user, physics.id, 2)
    old_semester = user.semester_id
    new = await repo.start_new_semester(user)
    user = await repo.update_user(user.id)
    assert user.semester_id == new.id
    assert new.title == "Осень 2026 (2)"  # то же время года — номер, а не дубль
    assert await repo.get_subject(user, physics.id) is None  # архив только для чтения
    assert await repo.copy_subjects(user.id, old_semester, new.id) == 2
    copied = await repo.list_subjects(user.id, new.id)
    assert [(s.name, s.okr_count) for s in copied] == [("Физика", 2), ("Химия", 1)]
    archived = await repo.views(user, old_semester)
    assert archived[0].values == [9]


async def test_move_subject(repo: Repo) -> None:
    user = await new_user(repo)
    added, _ = await repo.add_subjects(user, ["А-предмет", "Б-предмет", "В-предмет"])
    assert await repo.move_subject(user, added[2].id, -1)
    assert [s.name for s in await repo.list_subjects(user.id, user.semester_id)] == ["А-предмет", "В-предмет", "Б-предмет"]
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
