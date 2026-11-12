"""Кто может пользоваться ботом: владелец, приглашённые (по юзернейму или заявке), все — если открыт."""

from __future__ import annotations

import re

from .repo import Repo

USERNAME = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")


def parse_usernames(text: str) -> tuple[list[str], list[int], list[str]]:
    """«@ivan, petr 123456» -> (['ivan', 'petr'], [123456], [нераспознанное])."""
    names: list[str] = []
    ids: list[int] = []
    bad: list[str] = []
    for token in re.split(r"[\s,;]+", text.strip()):
        if not token:
            continue
        token = token.removeprefix("https://t.me/").removeprefix("t.me/")
        if token.isdigit():
            ids.append(int(token))
        elif match := USERNAME.match(token):
            names.append(match.group(1).lower())
        else:
            bad.append(token)
    return names, ids, bad


class Access:
    def __init__(self, repo: Repo, owner: str = "ztpst", allowed_ids: frozenset[int] = frozenset()) -> None:
        self.repo = repo
        owner = (owner or "").strip().lstrip("@")
        self.owner_id_config = int(owner) if owner.isdigit() else None
        self.owner_username = owner.lower() if owner and not owner.isdigit() else None
        self.allowed_ids = allowed_ids

    async def owner_id(self) -> int | None:
        if self.owner_id_config:
            return self.owner_id_config
        value = await self.repo.meta("owner_id")
        return int(value) if value and value.isdigit() else None

    async def check_owner(self, uid: int, username: str | None) -> tuple[bool, bool]:
        """(владелец ли, закрепили ли владельца только что).

        Владелец задаётся юзернеймом, но при первом входе запоминается его id: если потом
        кто-то займёт этот юзернейм, админом он не станет.
        """
        owner = await self.owner_id()
        if owner is not None:
            return uid == owner, False
        if self.owner_username and username and username.lower() == self.owner_username:
            await self.repo.set_meta("owner_id", str(uid))
            return True, True
        return False, False

    async def is_open(self) -> bool:
        return (await self.repo.meta("open_access")) == "1"

    async def allowed(self, uid: int, username: str | None, is_owner: bool) -> bool:
        if is_owner or uid in self.allowed_ids or await self.is_open():
            return True
        return await self.repo.has_access(uid, username)
