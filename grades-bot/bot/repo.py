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
from .dates import next_term, term_for
from .logic import (
    MAX_SUBJECTS,
    MAX_WORKS_PER_SUBJECT,
    SCALES,
    SubjectView,
    display_name,
    local_dt,
    norm,
    parse_subject_entry,
)
from .models import WORK_KINDS, Grade, Semester, Subject, User, Work

_USER_FIELDS = {
    "name", "username", "onboarded", "scale", "pass_mark", "semester_id", "remind_every", "remind_time",
    "skip_sunday", "works_reminders", "tz", "clean_chat", "panel_msg_id", "last_data_at",
    "last_reminded_at", "snooze_until", "blocked", "final_mode", "calc_mode",
}  # fmt: skip
_SEMESTER_FIELDS = {
    "title", "course", "term", "session_start", "session_end", "holidays_start", "holidays_end",
    "archive_asked_at", "archive_ask",
}  # fmt: skip
_SUBJECT_FIELDS = {
    "name", "short", "okr_count", "control", "exam_grade", "credit", "project", "project_grade", "general",
}  # fmt: skip
HAS_LETTERS = re.compile(r"[^\W\d_]")


def semester_label(course: int | None, term: int | None) -> str:
    return f"{course} курс · {term} семестр" if course and term else "Семестр"


def _clean(value: Any) -> Any:
    return int(value) if isinstance(value, bool) else value


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

    async def _update(self, table: str, allowed: set[str], where: str, params: Sequence[Any], **fields: Any) -> int:
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Неизвестные поля {table}: {unknown}")
        if not fields:
            return 0
        columns = ", ".join(f"{key} = ?" for key in fields)
        async with self.tx() as db:
            cur = await db.execute(
                f"UPDATE {table} SET {columns} WHERE {where}", (*map(_clean, fields.values()), *params)
            )
        return cur.rowcount

    # ------------------------------------------------------------ users

    async def get_user(self, uid: int) -> User | None:
        row = await self._one("SELECT * FROM users WHERE id = ?", (uid,))
        return User.from_row(row) if row else None

    async def ensure_user(self, uid: int, name: str = "", username: str | None = None) -> User:
        username = username.lower() if username else None
        user = await self.get_user(uid)
        if user is None:
            async with self.tx() as db:
                await db.execute(
                    "INSERT OR IGNORE INTO users (id, name, username, created_at, tz, pass_mark)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (uid, name or "", username, clock.now(), self.default_tz, SCALES[10]),
                )
            user = await self.get_user(uid)
            assert user is not None
        elif (name and user.name != name) or user.username != username:
            user = await self.update_user(uid, name=name or user.name, username=username)
        return user

    async def update_user(self, uid: int, **fields: Any) -> User:
        await self._update("users", _USER_FIELDS, "id = ?", (uid,), **fields)
        user = await self.get_user(uid)
        assert user is not None
        return user

    async def set_panel(self, uid: int, message_id: int | None) -> None:
        async with self.tx() as db:
            await db.execute("UPDATE users SET panel_msg_id = ? WHERE id = ?", (message_id, uid))

    async def reminder_candidates(self) -> list[User]:
        rows = await self._all("SELECT * FROM users WHERE onboarded = 1 AND blocked = 0")
        return [User.from_row(r) for r in rows]

    async def all_users(self) -> list[User]:
        rows = await self._all("SELECT * FROM users ORDER BY COALESCE(last_data_at, created_at) DESC")
        return [User.from_row(r) for r in rows]

    async def find_user_by_username(self, username: str) -> User | None:
        row = await self._one("SELECT * FROM users WHERE username = ?", (username.lower().lstrip("@"),))
        return User.from_row(row) if row else None

    async def delete_user(self, uid: int) -> None:
        async with self.tx() as db:
            await db.execute("DELETE FROM users WHERE id = ?", (uid,))

    async def _touch(self, db: aiosqlite.Connection, uid: int, ts: int) -> None:
        await db.execute("UPDATE users SET last_data_at = ? WHERE id = ?", (ts, uid))

    # ------------------------------------------------------------ semesters

    async def ensure_semester(self, user: User) -> User:
        if user.semester_id and await self.get_semester(user.id, user.semester_id):
            return user
        today = local_dt(clock.now(), user.tz).date()
        await self._create_semester(user.id, 1, term_for(today), archive_id=None)
        return await self.update_user(user.id)

    async def _create_semester(self, uid: int, course: int, term: int, archive_id: int | None) -> Semester:
        ts = clock.now()
        title = semester_label(course, term)
        async with self.tx() as db:
            if archive_id:
                await db.execute(
                    "UPDATE semesters SET archived_at = ? WHERE id = ? AND user_id = ?", (ts, archive_id, uid)
                )
            cur = await db.execute(
                "INSERT INTO semesters (user_id, title, created_at, course, term) VALUES (?, ?, ?, ?, ?)",
                (uid, title, ts, course, term),
            )
            sem_id = cur.lastrowid
            await db.execute("UPDATE users SET semester_id = ? WHERE id = ?", (sem_id, uid))
        semester = await self.get_semester(uid, sem_id)
        assert semester is not None
        return semester

    async def update_semester(self, uid: int, sem_id: int | None, **fields: Any) -> Semester | None:
        if "course" in fields or "term" in fields:
            current = await self.get_semester(uid, sem_id)
            if current is not None:
                course = fields.get("course", current.course)
                term = fields.get("term", current.term)
                fields["title"] = semester_label(course, term)
        await self._update("semesters", _SEMESTER_FIELDS, "id = ? AND user_id = ?", (sem_id, uid), **fields)
        return await self.get_semester(uid, sem_id)

    async def start_new_semester(self, user: User) -> Semester:
        """Текущий семестр уходит в архив, создаётся следующий (2-й того же курса или 1-й следующего)."""
        current = await self.get_semester(user.id, user.semester_id)
        course, term = next_term(current.course if current else 1, current.term if current else 1)
        return await self._create_semester(user.id, course, term, archive_id=user.semester_id)

    async def get_semester(self, uid: int, sem_id: int | None) -> Semester | None:
        if not sem_id:
            return None
        row = await self._one("SELECT * FROM semesters WHERE id = ? AND user_id = ?", (sem_id, uid))
        return Semester.from_row(row) if row else None

    async def list_semesters(self, uid: int) -> list[Semester]:
        rows = await self._all("SELECT * FROM semesters WHERE user_id = ? ORDER BY created_at DESC, id DESC", (uid,))
        return [Semester.from_row(r) for r in rows]

    async def last_archived(self, uid: int) -> Semester | None:
        row = await self._one(
            "SELECT * FROM semesters WHERE user_id = ? AND archived_at IS NOT NULL"
            " ORDER BY archived_at DESC, id DESC LIMIT 1",
            (uid,),
        )
        return Semester.from_row(row) if row else None

    async def copy_subjects(self, uid: int, from_sem: int, to_sem: int) -> int:
        rows = await self._all(
            "SELECT * FROM subjects WHERE user_id = ? AND semester_id = ? ORDER BY position, id", (uid, from_sem)
        )
        if not rows or not await self.get_semester(uid, to_sem):
            return 0
        ts = clock.now()
        async with self.tx() as db:
            for pos, row in enumerate(rows):
                await db.execute(
                    "INSERT INTO subjects (user_id, semester_id, name, short, okr_count, position, created_at,"
                    " control, project, general) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        uid, to_sem, row["name"], row["short"], row["okr_count"], pos, ts,
                        row["control"], row["project"], row["general"],
                    ),
                )  # fmt: skip
        return len(rows)

    # ------------------------------------------------------------ subjects

    async def list_subjects(self, uid: int, sem_id: int | None) -> list[Subject]:
        rows = await self._all(
            "SELECT * FROM subjects WHERE user_id = ? AND semester_id = ? ORDER BY position, id", (uid, sem_id)
        )
        return [Subject.from_row(r) for r in rows]

    async def get_subject(self, user: User, sid: int) -> Subject | None:
        """Предмет текущего семестра пользователя (чужие и архивные — None)."""
        row = await self._one(
            "SELECT * FROM subjects WHERE id = ? AND user_id = ? AND semester_id = ?",
            (sid, user.id, user.semester_id),
        )
        return Subject.from_row(row) if row else None

    async def add_subjects(
        self, user: User, entries: Iterable[str], okr_count: int = 1
    ) -> tuple[list[Subject], list[str]]:
        """Добавляет предметы («Название (СОКР)»), пропуская дубликаты. Возвращает (добавленные, пропущенные)."""
        existing = await self.list_subjects(user.id, user.semester_id)
        seen = {norm(display_name(s.name)) for s in existing} | {norm(s.short) for s in existing if s.short}
        fresh: list[tuple[str, str | None]] = []
        skipped: list[str] = []
        for entry in entries:
            name, short = parse_subject_entry(entry)
            key = norm(display_name(name))
            short_key = norm(short) if short else None
            if (
                not HAS_LETTERS.search(key)
                or key in seen
                or (short_key and short_key in seen)
                or len(existing) + len(fresh) >= MAX_SUBJECTS
            ):
                skipped.append(entry)
                continue
            seen.add(key)
            if short_key:
                seen.add(short_key)
            fresh.append((name, short))
        if not fresh:
            return [], skipped
        ts = clock.now()
        start = max((s.position for s in existing), default=-1) + 1
        ids = []
        async with self.tx() as db:
            for offset, (name, short) in enumerate(fresh):
                cur = await db.execute(
                    "INSERT INTO subjects (user_id, semester_id, name, short, okr_count, position, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user.id, user.semester_id, name, short, okr_count, start + offset, ts),
                )
                ids.append(cur.lastrowid)
        added = [s for s in await self.list_subjects(user.id, user.semester_id) if s.id in set(ids)]
        return added, skipped

    async def update_subject(self, user: User, sid: int, **fields: Any) -> Subject | None:
        await self._update("subjects", _SUBJECT_FIELDS, "id = ? AND user_id = ?", (sid, user.id), **fields)
        return await self.get_subject(user, sid)

    async def rename_subject(self, user: User, sid: int, name: str) -> None:
        await self.update_subject(user, sid, name=name)

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

    async def set_session_result(self, user: User, sid: int, **fields: Any) -> Subject | None:
        """Экзамен / зачёт / курсовой: отметка — это тоже «внёс данные» для напоминаний."""
        subject = await self.update_subject(user, sid, **fields)
        async with self.tx() as db:
            await self._touch(db, user.id, clock.now())
        return subject

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
            result[row["subject_id"]].append(Grade.from_row(row))
        return result

    async def add_grades(self, user: User, sid: int, values: Sequence[int]) -> list[int]:
        return await self.add_grades_multi(user, [(sid, values)])

    async def add_grades_multi(self, user: User, batches: Sequence[tuple[int, Sequence[int]]]) -> list[int]:
        """Оценки сразу в несколько предметов одной транзакцией (id идут подряд — удобно отменять)."""
        ts = clock.now()
        ids: list[int] = []
        async with self.tx() as db:
            for sid, values in batches:
                for value in values:
                    cur = await db.execute(
                        "INSERT INTO grades (subject_id, value, created_at) VALUES (?, ?, ?)", (sid, value, ts)
                    )
                    ids.append(cur.lastrowid)  # type: ignore[arg-type]
            if ids:
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
        return Grade.from_row(row)

    async def delete_grades(self, user: User, sid: int | None, ids: Sequence[int]) -> int:
        """Удаляет обычные оценки по id (для отмены). sid=None — в любом предмете пользователя."""
        if not ids:
            return 0
        subject_filter = "" if sid is None else " AND subject_id = ?"
        params = (*ids, *([] if sid is None else [sid]), user.id)
        async with self.tx() as db:
            cur = await db.execute(
                f"DELETE FROM grades WHERE id IN ({self._marks(ids)}){subject_filter}"
                " AND okr_no IS NULL AND work_id IS NULL"
                " AND subject_id IN (SELECT id FROM subjects WHERE user_id = ?)",
                params,
            )
        return cur.rowcount

    async def restore_grade(
        self, user: User, sid: int, value: int, okr_no: int | None, created_at: int, work_id: int | None = None
    ) -> None:
        async with self.tx() as db:
            if okr_no:
                await db.execute("DELETE FROM grades WHERE subject_id = ? AND okr_no = ?", (sid, okr_no))
            if work_id:
                async with db.execute("SELECT 1 FROM works WHERE id = ? AND subject_id = ?", (work_id, sid)) as cur:
                    if await cur.fetchone() is None:
                        work_id = None
                    else:
                        await db.execute("DELETE FROM grades WHERE work_id = ?", (work_id,))
            await db.execute(
                "INSERT INTO grades (subject_id, value, okr_no, work_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (sid, value, okr_no, work_id, created_at),
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
            result[row["subject_id"]].append(Work.from_row(row))
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
        ids: list[int] = []
        async with self.tx() as db:
            async with db.execute(
                "SELECT COALESCE(MAX(num), 0) AS n FROM works WHERE subject_id = ? AND kind = ?", (sid, kind)
            ) as cur:
                last = (await cur.fetchone())["n"]  # type: ignore[index]
            for offset, title in enumerate(titles, start=1):
                cur = await db.execute(
                    "INSERT INTO works (subject_id, kind, num, title, created_at) VALUES (?, ?, ?, ?, ?)",
                    (sid, kind, last + offset, title, ts),
                )
                ids.append(cur.lastrowid)  # type: ignore[arg-type]
            await self._touch(db, user.id, ts)
        works = await self.works_for([sid])
        return [w for w in works.get(sid, []) if w.id in set(ids)]

    async def get_work(self, user: User, wid: int) -> Work | None:
        row = await self._one(
            "SELECT w.* FROM works w JOIN subjects s ON s.id = w.subject_id"
            " WHERE w.id = ? AND s.user_id = ? AND s.semester_id = ?",
            (wid, user.id, user.semester_id),
        )
        return Work.from_row(row) if row else None

    async def set_work_status(self, user: User, wid: int, status: str) -> None:
        """status: passed (зачтено) | failed (не зачтено) | pending (ещё не сдавал)."""
        ts = clock.now()
        defended_at = ts if status == "passed" else None
        failed = 1 if status == "failed" else 0
        async with self.tx() as db:
            await db.execute(
                "UPDATE works SET defended_at = ?, failed = ? WHERE id = ?"
                " AND subject_id IN (SELECT id FROM subjects WHERE user_id = ?)",
                (defended_at, failed, wid, user.id),
            )
            await self._touch(db, user.id, ts)

    async def set_defended(self, user: User, wid: int, defended: bool) -> None:
        await self.set_work_status(user, wid, "passed" if defended else "pending")

    async def set_work_grade(self, user: User, wid: int, value: int | None) -> int | None:
        """Оценка за работу (идёт в средний). value=None — убрать. Возвращает прежнюю."""
        work = await self.get_work(user, wid)
        if work is None:
            return None
        ts = clock.now()
        async with self.tx() as db:
            async with db.execute("SELECT value FROM grades WHERE work_id = ?", (wid,)) as cur:
                row = await cur.fetchone()
            previous = row["value"] if row else None
            await db.execute("DELETE FROM grades WHERE work_id = ?", (wid,))
            if value is not None:
                await db.execute(
                    "INSERT INTO grades (subject_id, value, work_id, created_at) VALUES (?, ?, ?, ?)",
                    (work.subject_id, value, wid, ts),
                )
            await self._touch(db, user.id, ts)
        return previous

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
        return [
            SubjectView(
                s,
                grades.get(s.id, []),
                works.get(s.id, []),
                user.pass_mark,
                user.scale,
                user.calc_mode,
                user.final_mode,
            )
            for s in subjects
        ]

    async def view(self, user: User, sid: int) -> SubjectView | None:
        subject = await self.get_subject(user, sid)
        if subject is None:
            return None
        grades = await self.grades_for([sid])
        works = await self.works_for([sid])
        return SubjectView(
            subject,
            grades.get(sid, []),
            works.get(sid, []),
            user.pass_mark,
            user.scale,
            user.calc_mode,
            user.final_mode,
        )

    async def max_grade(self, user: User) -> int:
        row = await self._one(
            "SELECT MAX(m) AS m FROM ("
            " SELECT MAX(g.value) AS m FROM grades g JOIN subjects s ON s.id = g.subject_id WHERE s.user_id = ?"
            " UNION ALL SELECT MAX(exam_grade) FROM subjects WHERE user_id = ?"
            " UNION ALL SELECT MAX(project_grade) FROM subjects WHERE user_id = ?)",
            (user.id, user.id, user.id),
        )
        return (row["m"] if row else 0) or 0

    # ------------------------------------------------------------ доступ и админка

    async def meta(self, key: str) -> str | None:
        row = await self._one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str | None) -> None:
        async with self.tx() as db:
            await db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    async def access_list(self) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM access ORDER BY added_at DESC")

    async def has_access(self, uid: int, username: str | None) -> bool:
        if username:
            row = await self._one(
                "SELECT id, user_id FROM access WHERE user_id = ? OR username = ?", (uid, username.lower())
            )
        else:
            row = await self._one("SELECT id, user_id FROM access WHERE user_id = ?", (uid,))
        if row is None:
            return False
        if row["user_id"] is None:  # добавили по юзернейму — запоминаем id
            async with self.tx() as db:
                await db.execute("UPDATE access SET user_id = ? WHERE id = ?", (uid, row["id"]))
        return True

    async def grant(self, user_id: int | None = None, username: str | None = None) -> bool:
        """Открыть доступ. Возвращает False, если он уже был."""
        username = username.lower().lstrip("@") if username else None
        if user_id is None and username:
            known = await self.find_user_by_username(username)
            user_id = known.id if known else None
        existing = await self._one(
            "SELECT id FROM access WHERE (user_id IS NOT NULL AND user_id = ?) OR (username IS NOT NULL AND username = ?)",
            (user_id, username),
        )
        if existing:
            return False
        async with self.tx() as db:
            await db.execute(
                "INSERT INTO access (user_id, username, added_at) VALUES (?, ?, ?)", (user_id, username, clock.now())
            )
            if user_id is not None:
                await db.execute("DELETE FROM access_requests WHERE user_id = ?", (user_id,))
        return True

    async def revoke(self, user_id: int | None = None, username: str | None = None) -> int:
        async with self.tx() as db:
            cur = await db.execute(
                "DELETE FROM access WHERE (user_id IS NOT NULL AND user_id = ?) OR (username IS NOT NULL AND username = ?)",
                (user_id, username.lower() if username else None),
            )
        return cur.rowcount

    async def add_request(self, user_id: int, username: str | None, name: str) -> bool:
        """Заявка на доступ. False — такая уже есть."""
        if await self._one("SELECT 1 FROM access_requests WHERE user_id = ?", (user_id,)):
            return False
        async with self.tx() as db:
            await db.execute(
                "INSERT INTO access_requests (user_id, username, name, requested_at) VALUES (?, ?, ?, ?)",
                (user_id, username.lower() if username else None, name, clock.now()),
            )
        return True

    async def requests(self) -> list[aiosqlite.Row]:
        return await self._all("SELECT * FROM access_requests ORDER BY requested_at")

    async def drop_request(self, user_id: int) -> None:
        async with self.tx() as db:
            await db.execute("DELETE FROM access_requests WHERE user_id = ?", (user_id,))
