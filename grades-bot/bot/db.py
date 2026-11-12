"""Подключение к SQLite и миграции схемы (через PRAGMA user_version)."""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

# Каждая миграция — отдельный SQL-скрипт. Новые добавляются только в конец списка.
MIGRATIONS: list[str] = [
    # v1 — исходная схема
    """
    CREATE TABLE users (
        id                INTEGER PRIMARY KEY,           -- Telegram user id
        name              TEXT    NOT NULL DEFAULT '',
        created_at        INTEGER NOT NULL,
        onboarded         INTEGER NOT NULL DEFAULT 0,
        scale             INTEGER NOT NULL DEFAULT 10,   -- 5 или 10
        pass_mark         INTEGER NOT NULL DEFAULT 4,    -- с какой оценки ОКР сдана
        semester_id       INTEGER,                       -- текущий семестр
        remind_every      INTEGER NOT NULL DEFAULT 2,    -- дней; 0 = не напоминать
        remind_time       TEXT    NOT NULL DEFAULT '20:00',
        skip_sunday       INTEGER NOT NULL DEFAULT 1,
        tz                TEXT    NOT NULL DEFAULT 'Europe/Minsk',
        clean_chat        INTEGER NOT NULL DEFAULT 1,
        panel_msg_id      INTEGER,
        last_data_at      INTEGER,
        last_reminded_at  INTEGER,
        snooze_until      INTEGER,
        blocked           INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE semesters (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title        TEXT    NOT NULL,
        created_at   INTEGER NOT NULL,
        archived_at  INTEGER
    );
    CREATE INDEX ix_semesters_user ON semesters(user_id);

    CREATE TABLE subjects (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        semester_id  INTEGER NOT NULL REFERENCES semesters(id) ON DELETE CASCADE,
        name         TEXT    NOT NULL,
        okr_count    INTEGER NOT NULL DEFAULT 1,         -- сколько ОКР в предмете (0 = нет)
        position     INTEGER NOT NULL DEFAULT 0,
        created_at   INTEGER NOT NULL
    );
    CREATE INDEX ix_subjects_semester ON subjects(semester_id, position);

    CREATE TABLE works (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id   INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
        kind         TEXT    NOT NULL CHECK (kind IN ('lab', 'prac')),
        num          INTEGER NOT NULL,
        title        TEXT,
        defended_at  INTEGER,                            -- NULL = не защищена
        created_at   INTEGER NOT NULL
    );
    CREATE INDEX ix_works_subject ON works(subject_id);

    CREATE TABLE grades (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id   INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
        value        INTEGER NOT NULL,
        okr_no       INTEGER,                            -- NULL = обычная оценка, 1..N = ОКР №
        created_at   INTEGER NOT NULL
    );
    CREATE INDEX ix_grades_subject ON grades(subject_id);

    CREATE TABLE fsm (
        key    TEXT PRIMARY KEY,
        state  TEXT,
        data   TEXT NOT NULL DEFAULT '{}'
    );
    """,
    # v2 — колледж (ССО): курс/семестр и даты сессии, экзамены и зачёты, курсовые,
    # «не зачтено» у работ, оценка за работу, доступ по приглашению и админ-панель.
    """
    ALTER TABLE users ADD COLUMN username TEXT;
    ALTER TABLE users ADD COLUMN works_reminders INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE users ADD COLUMN final_mode TEXT NOT NULL DEFAULT 'avg';
    ALTER TABLE users ADD COLUMN calc_mode TEXT NOT NULL DEFAULT 'sso';
    CREATE INDEX ix_users_username ON users(username);

    ALTER TABLE semesters ADD COLUMN course INTEGER;
    ALTER TABLE semesters ADD COLUMN term INTEGER;
    ALTER TABLE semesters ADD COLUMN session_start TEXT;
    ALTER TABLE semesters ADD COLUMN session_end TEXT;
    ALTER TABLE semesters ADD COLUMN holidays_start TEXT;
    ALTER TABLE semesters ADD COLUMN holidays_end TEXT;
    ALTER TABLE semesters ADD COLUMN archive_asked_at INTEGER;
    ALTER TABLE semesters ADD COLUMN archive_ask INTEGER NOT NULL DEFAULT 1;

    ALTER TABLE subjects ADD COLUMN short TEXT;
    ALTER TABLE subjects ADD COLUMN control TEXT NOT NULL DEFAULT 'none';
    ALTER TABLE subjects ADD COLUMN exam_grade INTEGER;
    ALTER TABLE subjects ADD COLUMN credit INTEGER;
    ALTER TABLE subjects ADD COLUMN project INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE subjects ADD COLUMN project_grade INTEGER;
    ALTER TABLE subjects ADD COLUMN general INTEGER NOT NULL DEFAULT 0;

    ALTER TABLE works ADD COLUMN failed INTEGER NOT NULL DEFAULT 0;

    ALTER TABLE grades ADD COLUMN work_id INTEGER REFERENCES works(id) ON DELETE CASCADE;
    CREATE INDEX ix_grades_work ON grades(work_id);

    CREATE TABLE access (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER UNIQUE,
        username   TEXT UNIQUE,                         -- без @, в нижнем регистре
        added_at   INTEGER NOT NULL
    );

    CREATE TABLE access_requests (
        user_id       INTEGER PRIMARY KEY,
        username      TEXT,
        name          TEXT NOT NULL DEFAULT '',
        requested_at  INTEGER NOT NULL
    );

    CREATE TABLE meta (
        key    TEXT PRIMARY KEY,
        value  TEXT
    );

    -- Порог сдачи в ССО — 3 (п. 4 Правил № 282), а не 4.
    UPDATE users SET pass_mark = 3 WHERE scale = 10 AND pass_mark = 4;
    """,
]


async def connect(path: str) -> aiosqlite.Connection:
    """Открывает базу, включает WAL и внешние ключи, применяет миграции."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA busy_timeout = 5000")
    if path != ":memory:":
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA synchronous = NORMAL")
    await migrate(db)
    return db


async def migrate(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    version = int(row[0]) if row else 0
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        log.info("Применяю миграцию базы данных v%s", number)
        await db.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
