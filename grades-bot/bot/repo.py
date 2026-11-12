"""Доступ к данным. Каждый метод проверяет, что данные принадлежат пользователю."""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from . import clock
from .logic import (
    MAX_SUBJECTS,
    MAX_WORKS_PER_SUBJECT,
    SubjectView,
    display_name,
    local_dt,
    norm,
    semester_title,
)
from .models import WORK_KINDS, Grade, Semester, Subject, User, Work

_USER_FIELDS = {
    "name", "onboarded", "scale", "pass_mark", "semester_id", "remind_every", "remind_time",
    "skip_sunday", "tz", "clean_chat", "panel_msg_id", "last_data_at", "last_reminded_at",
    "snooze_until", "blocked",
}  # fmt: skip


def _user(row: aiosqlite.Row) -> User:
    return User(
        id=row["id"],
        name=row["name"],
        created_at=row["created_at"],
        onboarded=bool(row["onboarded"]),
        scale=row["scale"],
        pass_mark=row["pass_mark"],
        semester_id=row["semester_id"],
        remind_every=row["remind_every"],
        remind_time=row["remind_time"],
        skip_sunday=bool(row["skip_sunday"]),
        tz=row["tz"],
        clean_chat=bool(row["clean_chat"]),
        panel_msg_id=row["panel_msg_id"],
        last_data_at=row["last_data_at"],
        last_reminded_at=row["last_reminded_at"],
        snooze_until=row["snooze_until"],
        blocked=bool(row["blocked"]),
    )


def _semester(row: aiosqlite.Row) -> Semester:
    return Semester(row["id"], row["user_id"], row["title"], row["created_at"], row["archived_at"])


def _subject(row: aiosqlite.Row) -> Subject:
    return Subject(
        row["id"], row["user_id"], row["semester_id"], row["name"], row["okr_count"],
        row["position"], row["created_at"],
    )  # fmt: skip


def _grade(row: aiosqlite.Row) -> Grade:
    return Grade(row["id"], row["subject_id"], row["value"], row["okr_no"], row["created_at"])


def _work(row: aiosqlite.Row) -> Work:
    return Work(
        row["id"], row["subject_id"], row["kind"], row["num"], row["title"], row["defended_at"],
        row["created_at"],
    )  # fmt: skip


class Repo:
    def __init__(self, db: aiosqlite.Connection, default_tz: str = "Europe/Minsk") -> None:
        self.db = db
        self.default_tz = default_tz
        # Одно соединение на всё приложение: запись идёт строго по очереди.
        self.lock = asyncio.Lock()

    # ------------------------------------------------------------ helpers

    async def _one(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self.db.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _all(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self.db.execute(sql, params) as cur:
            return list(await cur.fetchall())

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self.lock:
            try:
                yield self.db
            except BaseException:
                await self.db.rollback()
                raise
            else:
                await self.db.commit()

    @staticmethod
    def _marks(items: Sequence[Any]) -> str:
        return ",".join("?" * len(items))

    # ------------------------------------------------------------ users

    async def get_user(self, uid: int) -> User | None:
        row = await self._one("SELECT * FROM users WHERE id = ?", (uid,))
        return _user(row) if row else None

    async def ensure_user(self, uid: int, name: str = "") -> User:
        user = await self.get_user(uid)
        if user is None:
            async with self.tx() as db:
                await db.execute(
                    "INSERT OR IGNORE INTO users (id, name, created_at, tz) VALUES (?, ?, ?, ?)",
                    (uid, name or "", clock.now(), self.default_tz),
                )
            user = await self.get_user(uid)
            assert user is not None
        elif name and user.name != name:
            user = await self.update_user(uid, name=name)
        return user

    async def update_user(self, uid: int, **fields: Any) -> User:
        unknown = set(fields) - _USER_FIELDS
        if unknown:
            raise ValueError(f"Неизвестные поля пользователя: {unknown}")
        if fields:
            columns = ", ".join(f"{key} = ?" for key in fields)
            values = [int(v) if isinstance(v, bool) else v for v in fields.values()]
            async with self.tx() as db:
                await db.execute(f"UPDATE users SET {columns} WHERE id = ?", (*values, uid))
        user = await self.get_user(uid)
        assert user is not None
        return user

    async def set_panel(self, uid: int, message_id: int | None) -> None:
        async with self.tx() as db:
            await db.execute("UPDATE users SET panel_msg_id = ? WHERE id = ?", (message_id, uid))

    async def reminder_candidates(self) -> list[User]:
        rows = await self._all(
            "SELECT * FROM users WHERE onboarded = 1 AND blocked = 0 AND (remind_every > 0 OR snooze_until IS NOT NULL)"
        )
        return [_user(r) for r in rows]

    async def delete_user(self, uid: int) -> None:
        async with self.tx() as db:
            await db.execute("DELETE FROM users WHERE id = ?", (uid,))

    async def _touch(self, db: aiosqlite.Connection, uid: int, ts: int) -> None:
        await db.execute("UPDATE users SET last_data_at = ? WHERE id = ?", (ts, uid))

    # ------------------------------------------------------------ semesters

    async def ensure_semester(self, user: User) -> User:
        if user.semester_id and await self.get_semester(user.id, user.semester_id):
            return user
        title = semester_title(local_dt(clock.now(), user.tz).date())
        await self._create_semester(user.id, title, archive_id=None)
        return await self.update_user(user.id)

    async def _unique_title(self, uid: int, title: str) -> str:
        rows = await self._all("SELECT title FROM semesters WHERE user_id = ?", (uid,))
        taken = {r["title"] for r in rows}
        if title not in taken:
            return title
        n = 2
        while f"{title} ({n})" in taken:
            n += 1
        return f"{title} ({n})"

    async def _create_semester(self, uid: int, title: str, archive_id: int | None) -> Semester:
        title = await self._unique_title(uid, title)
        ts = clock.now()
        async with self.tx() as db:
            if archive_id:
                await db.execute(
                    "UPDATE semesters SET archived_at = ? WHERE id = ? AND user_id = ?",
                    (ts, archive_id, uid),
                )
            cur = await db.execute(
                "INSERT INTO semesters (user_id, title, created_at) VALUES (?, ?, ?)", (uid, title, ts)
            )
            sem_id = cur.lastrowid
            await db.execute("UPDATE users SET semester_id = ? WHERE id = ?", (sem_id, uid))
        return Semester(sem_id, uid, title, ts, None)  # type: ignore[arg-type]

    async def start_new_semester(self, user: User) -> Semester:
        """Текущий семестр уходит в архив, создаётся новый (и становится текущим)."""
        title = semester_title(local_dt(clock.now(), user.tz).date())
        return await self._create_semester(user.id, title, archive_id=user.semester_id)

    async def get_semester(self, uid: int, sem_id: int | None) -> Semester | None:
        if not sem_id:
            return None
        row = await self._one("SELECT * FROM semesters WHERE id = ? AND user_id = ?", (sem_id, uid))
        return _semester(row) if row else None

    async def list_semesters(self, uid: int) -> list[Semester]:
        rows = await self._all("SELECT * FROM semesters WHERE user_id = ? ORDER BY created_at DESC, id DESC", (uid,))
        return [_semester(r) for r in rows]

    async def copy_subjects(self, uid: int, from_sem: int, to_sem: int) -> int:
        rows = await self._all(
            "SELECT * FROM subjects WHERE user_id = ? AND semester_id = ? ORDER BY position, id",
            (uid, from_sem),
        )
        if not rows or not await self.get_semester(uid, to_sem):
            return 0
        ts = clock.now()
        async with self.tx() as db:
            for pos, row in enumerate(rows):
                await db.execute(
                    "INSERT INTO subjects (user_id, semester_id, name, okr_count, position, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (uid, to_sem, row["name"], row["okr_count"], pos, ts),
                )
        return len(rows)

    # ------------------------------------------------------------ subjects

    async def list_subjects(self, uid: int, sem_id: int | None) -> list[Subject]:
        rows = await self._all(
            "SELECT * FROM subjects WHERE user_id = ? AND semester_id = ? ORDER BY position, id",
            (uid, sem_id),
        )
        return [_subject(r) for r in rows]

    async def get_subject(self, user: User, sid: int) -> Subject | None:
        """Предмет текущего семестра пользователя (чужие и архивные — None)."""
        row = await self._one(
            "SELECT * FROM subjects WHERE id = ? AND user_id = ? AND semester_id = ?",
            (sid, user.id, user.semester_id),
        )
        return _subject(row) if row else None

    async def add_subjects(
        self, user: User, names: Iterable[str], okr_count: int = 1
    ) -> tuple[list[Subject], list[str]]:
        """Добавляет предметы, пропуская дубликаты. Возвращает (добавленные, пропущенные)."""
        existing = await self.list_subjects(user.id, user.semester_id)
        seen = {norm(display_name(s.name)) for s in existing}
        fresh: list[str] = []
        skipped: list[str] = []
        for name in names:
            key = norm(display_name(name))
            has_letters = bool(re.search(r"[^\W\d_]", key))
            if not has_letters or key in seen or len(existing) + len(fresh) >= MAX_SUBJECTS:
                skipped.append(name)
                continue
            seen.add(key)
            fresh.append(name)
        if not fresh:
            return [], skipped
        ts = clock.now()
        start = max((s.position for s in existing), default=-1) + 1
        ids = []
        async with self.tx() as db:
            for offset, name in enumerate(fresh):
                cur = await db.execute(
                    "INSERT INTO subjects (user_id, semester_id, name, okr_count, position, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (user.id, user.semester_id, name, okr_count, start + offset, ts),
                )
                ids.append(cur.lastrowid)
        added = [
            Subject(sid, user.id, user.semester_id, name, okr_count, start + i, ts)  # type: ignore[arg-type]
            for i, (sid, name) in enumerate(zip(ids, fresh, strict=True))
        ]
        return added, skipped

    async def rename_subject(self, user: User, sid: int, name: str) -> None:
        async with self.tx() as db:
            await db.execute("UPDATE subjects SET name = ? WHERE id = ? AND user_id = ?", (name, sid, user.id))

    async def delete_subject(self, user: User, sid: int) -> None:
        async with self.tx() as db:
            await db.execute("DELETE FROM subjects WHERE id = ? AND user_id = ?", (sid, user.id))

    async def delete_semester_subjects(self, user: User) -> None:
        async with self.tx() as db:
            await db.execute("DELETE FROM subjects WHERE user_id = ? AND semester_id = ?", (user.id, user.semester_id))

    async def set_okr_count(self, user: User, sid: int, count: int) -> int:
        """Меняет число ОКР. Оценки за «лишние» ОКР становятся обычными. Возвращает их число."""
        async with self.tx() as db:
            cur = await db.execute(
                "UPDATE grades SET okr_no = NULL WHERE subject_id = ? AND okr_no > ?"
                " AND subject_id IN (SELECT id FROM subjects WHERE user_id = ?)",
                (sid, count, user.id),
            )
            moved = cur.rowcount
            await db.execute("UPDATE subjects SET okr_count = ? WHERE id = ? AND user_id = ?", (count, sid, user.id))
        return moved

    async def move_subject(self, user: User, sid: int, delta: int) -> bool:
        subjects = await self.list_subjects(user.id, user.semester_id)
        index = next((i for i, s in enumerate(subjects) if s.id == sid), None)
        if index is None:
            return False
        target = index + delta
        if not 0 <= target < len(subjects):
            return False
        subjects[index], subjects[target] = subjects[target], subjects[index]
        async with self.tx() as db:
            for pos, subject in enumerate(subjects):
                await db.execute("UPDATE subjects SET position = ? WHERE id = ?", (pos, subject.id))
        return True

    # ------------------------------------------------------------ grades

    async def grades_for(self, subject_ids: Sequence[int]) -> dict[int, list[Grade]]:
        result: dict[int, list[Grade]] = defaultdict(list)
        if not subject_ids:
            return result
        rows = await self._all(
            f"SELECT * FROM grades WHERE subject_id IN ({self._marks(subject_ids)}) ORDER BY created_at, id",
            tuple(subject_ids),
        )
        for row in rows:
            result[row["subject_id"]].append(_grade(row))
        return result

    async def add_grades(self, user: User, sid: int, values: Sequence[int]) -> list[int]:
        ts = clock.now()
        ids: list[int] = []
        async with self.tx() as db:
            for value in values:
                cur = await db.execute(
                    "INSERT INTO grades (subject_id, value, okr_no, created_at) VALUES (?, ?, NULL, ?)",
                    (sid, value, ts),
                )
                ids.append(cur.lastrowid)  # type: ignore[arg-type]
            await self._touch(db, user.id, ts)
        return ids

    async def set_okr(self, user: User, sid: int, slot: int, value: int | None) -> int | None:
        """Ставит (или убирает при value=None) оценку за ОКР №slot. Возвращает прежнюю."""
        ts = clock.now()
        async with self.tx() as db:
            async with db.execute("SELECT value FROM grades WHERE subject_id = ? AND okr_no = ?", (sid, slot)) as cur:
                row = await cur.fetchone()
            previous = row["value"] if row else None
            await db.execute("DELETE FROM grades WHERE subject_id = ? AND okr_no = ?", (sid, slot))
            if value is not None:
                await db.execute(
                    "INSERT INTO grades (subject_id, value, okr_no, created_at) VALUES (?, ?, ?, ?)",
                    (sid, value, slot, ts),
                )
            await self._touch(db, user.id, ts)
        return previous

    async def delete_grade(self, user: User, gid: int) -> Grade | None:
        row = await self._one(
            "SELECT g.* FROM grades g JOIN subjects s ON s.id = g.subject_id"
            " WHERE g.id = ? AND s.user_id = ? AND s.semester_id = ?",
            (gid, user.id, user.semester_id),
        )
        if row is None:
            return None
        async with self.tx() as db:
            await db.execute("DELETE FROM grades WHERE id = ?", (gid,))
        return _grade(row)

    async def delete_grades(self, user: User, sid: int, ids: Sequence[int]) -> int:
        if not ids:
            return 0
        async with self.tx() as db:
            cur = await db.execute(
                f"DELETE FROM grades WHERE id IN ({self._marks(ids)}) AND subject_id = ?"
                " AND okr_no IS NULL AND subject_id IN (SELECT id FROM subjects WHERE user_id = ?)",
                (*ids, sid, user.id),
            )
        return cur.rowcount

    async def restore_grade(self, user: User, sid: int, value: int, okr_no: int | None, created_at: int) -> None:
        async with self.tx() as db:
            if okr_no:
                await db.execute("DELETE FROM grades WHERE subject_id = ? AND okr_no = ?", (sid, okr_no))
            await db.execute(
                "INSERT INTO grades (subject_id, value, okr_no, created_at) VALUES (?, ?, ?, ?)",
                (sid, value, okr_no, created_at),
            )
            await self._touch(db, user.id, clock.now())

    # ------------------------------------------------------------ works

    async def works_for(self, subject_ids: Sequence[int]) -> dict[int, list[Work]]:
        result: dict[int, list[Work]] = defaultdict(list)
        if not subject_ids:
            return result
        rows = await self._all(
            f"SELECT * FROM works WHERE subject_id IN ({self._marks(subject_ids)}) ORDER BY kind, num, id",
            tuple(subject_ids),
        )
        for row in rows:
            result[row["subject_id"]].append(_work(row))
        return result

    async def add_works(self, user: User, sid: int, kind: str, titles: Sequence[str | None]) -> list[Work]:
        if kind not in WORK_KINDS:
            raise ValueError(kind)
        row = await self._one("SELECT COUNT(*) AS n FROM works WHERE subject_id = ?", (sid,))
        room = MAX_WORKS_PER_SUBJECT - (row["n"] if row else 0)
        titles = list(titles)[: max(room, 0)]
        if not titles:
            return []
        ts = clock.now()
        works: list[Work] = []
        async with self.tx() as db:
            async with db.execute(
                "SELECT COALESCE(MAX(num), 0) AS n FROM works WHERE subject_id = ? AND kind = ?",
                (sid, kind),
            ) as cur:
                last = (await cur.fetchone())["n"]  # type: ignore[index]
            for offset, title in enumerate(titles, start=1):
                cur = await db.execute(
                    "INSERT INTO works (subject_id, kind, num, title, created_at) VALUES (?, ?, ?, ?, ?)",
                    (sid, kind, last + offset, title, ts),
                )
                works.append(Work(cur.lastrowid, sid, kind, last + offset, title, None, ts))  # type: ignore[arg-type]
            await self._touch(db, user.id, ts)
        return works

    async def get_work(self, user: User, wid: int) -> Work | None:
        row = await self._one(
            "SELECT w.* FROM works w JOIN subjects s ON s.id = w.subject_id"
            " WHERE w.id = ? AND s.user_id = ? AND s.semester_id = ?",
            (wid, user.id, user.semester_id),
        )
        return _work(row) if row else None

    async def set_defended(self, user: User, wid: int, defended: bool) -> None:
        ts = clock.now()
        async with self.tx() as db:
            await db.execute(
                "UPDATE works SET defended_at = ? WHERE id = ?"
                " AND subject_id IN (SELECT id FROM subjects WHERE user_id = ?)",
                (ts if defended else None, wid, user.id),
            )
            await self._touch(db, user.id, ts)

    async def rename_work(self, user: User, wid: int, title: str | None) -> None:
        async with self.tx() as db:
            await db.execute(
                "UPDATE works SET title = ? WHERE id = ? AND subject_id IN (SELECT id FROM subjects WHERE user_id = ?)",
                (title, wid, user.id),
            )

    async def delete_works(self, user: User, sid: int, ids: Sequence[int]) -> int:
        if not ids:
            return 0
        async with self.tx() as db:
            cur = await db.execute(
                f"DELETE FROM works WHERE id IN ({self._marks(ids)}) AND subject_id = ?"
                " AND subject_id IN (SELECT id FROM subjects WHERE user_id = ?)",
                (*ids, sid, user.id),
            )
        return cur.rowcount

    # ------------------------------------------------------------ сводки

    async def views(self, user: User, sem_id: int | None = None) -> list[SubjectView]:
        subjects = await self.list_subjects(user.id, sem_id or user.semester_id)
        ids = [s.id for s in subjects]
        grades = await self.grades_for(ids)
        works = await self.works_for(ids)
        return [SubjectView(s, grades.get(s.id, []), works.get(s.id, []), user.pass_mark, user.scale) for s in subjects]

    async def view(self, user: User, sid: int) -> SubjectView | None:
        subject = await self.get_subject(user, sid)
        if subject is None:
            return None
        grades = await self.grades_for([sid])
        works = await self.works_for([sid])
        return SubjectView(subject, grades.get(sid, []), works.get(sid, []), user.pass_mark, user.scale)

    async def max_grade(self, user: User) -> int:
        row = await self._one(
            "SELECT COALESCE(MAX(g.value), 0) AS m FROM grades g JOIN subjects s ON s.id = g.subject_id"
            " WHERE s.user_id = ?",
            (user.id,),
        )
        return row["m"] if row else 0
