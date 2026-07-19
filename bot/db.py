"""aiosqlite storage: question bank, posted polls, key-value state."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id           TEXT PRIMARY KEY,
    category     TEXT NOT NULL,
    question     TEXT NOT NULL,
    options_json TEXT NOT NULL,
    style        TEXT NOT NULL DEFAULT 'preference',
    source       TEXT NOT NULL DEFAULT 'bank',
    added_by     INTEGER,
    used_at      TEXT,
    skipped      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS polls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id   TEXT NOT NULL REFERENCES questions(id),
    message_id    INTEGER,
    channel_id    INTEGER,
    posted_at     TEXT NOT NULL,
    closes_at     TEXT NOT NULL,
    total_votes   INTEGER,
    winner_option TEXT,
    results_json  TEXT,
    finalized     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS tournaments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    game           TEXT NOT NULL,
    format         TEXT NOT NULL DEFAULT 'single_elim',
    status         TEXT NOT NULL DEFAULT 'open',
    guild_id       INTEGER,
    channel_id     INTEGER,
    message_id     INTEGER,
    created_by     INTEGER,
    created_at     TEXT NOT NULL,
    bracket_size   INTEGER,
    rounds         INTEGER,
    winner_user_id INTEGER
);
CREATE TABLE IF NOT EXISTS participants (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id),
    user_id       INTEGER NOT NULL,
    display_name  TEXT NOT NULL,
    seed          INTEGER,
    UNIQUE(tournament_id, user_id)
);
CREATE TABLE IF NOT EXISTS matches (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id  INTEGER NOT NULL REFERENCES tournaments(id),
    match_no       INTEGER NOT NULL,
    round          INTEGER NOT NULL,
    pos            INTEGER NOT NULL,
    p1_user_id     INTEGER,
    p2_user_id     INTEGER,
    winner_user_id INTEGER,
    status         TEXT NOT NULL DEFAULT 'pending',
    next_match_no  INTEGER,
    next_slot      INTEGER,
    UNIQUE(tournament_id, match_no)
);
CREATE TABLE IF NOT EXISTS teams (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id   INTEGER NOT NULL REFERENCES tournaments(id),
    name            TEXT NOT NULL,
    captain_user_id INTEGER NOT NULL,
    seed            INTEGER
);
CREATE TABLE IF NOT EXISTS giveaways (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    prize          TEXT NOT NULL,
    description    TEXT,
    host_id        INTEGER,
    guild_id       INTEGER,
    channel_id     INTEGER,
    message_id     INTEGER,
    winners_count  INTEGER NOT NULL DEFAULT 1,
    required_roles TEXT,
    role_logic     TEXT NOT NULL DEFAULT 'all',
    bonus_role_id  INTEGER,
    bonus_entries  INTEGER NOT NULL DEFAULT 0,
    image_name     TEXT,
    created_by     INTEGER,
    created_at     TEXT NOT NULL,
    ends_at        TEXT NOT NULL,
    ended          INTEGER NOT NULL DEFAULT 0,
    cancelled      INTEGER NOT NULL DEFAULT 0,
    winners_json   TEXT
);
CREATE TABLE IF NOT EXISTS giveaway_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    giveaway_id INTEGER NOT NULL REFERENCES giveaways(id),
    user_id     INTEGER NOT NULL,
    entries     INTEGER NOT NULL DEFAULT 1,
    joined_at   TEXT NOT NULL,
    UNIQUE(giveaway_id, user_id)
);
"""

# Applied on connect for tables that predate a column. Each is tried once;
# "duplicate column name" means it already ran, which is fine.
MIGRATIONS = [
    "ALTER TABLE tournaments ADD COLUMN mode TEXT NOT NULL DEFAULT 'solo'",
    "ALTER TABLE tournaments ADD COLUMN team_size INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE participants ADD COLUMN team_id INTEGER",
    "ALTER TABLE participants ADD COLUMN is_captain INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE matches ADD COLUMN score TEXT",
]

# matches.p1_user_id / p2_user_id / winner_user_id hold an "entrant" id:
# a user id in solo tournaments, a team id in team (3v3) tournaments.


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not connected"
        return self._conn

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        for statement in MIGRATIONS:
            try:
                await self._conn.execute(statement)
            except aiosqlite.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # -- kv ------------------------------------------------------------

    async def kv_get(self, key: str) -> str | None:
        async with self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return row["value"] if row else None

    async def kv_set(self, key: str, value: str | None) -> None:
        if value is None:
            await self.conn.execute("DELETE FROM kv WHERE key = ?", (key,))
        else:
            await self.conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        await self.conn.commit()

    # -- seeding ---------------------------------------------------------

    async def seed(self, bank: dict[str, Any]) -> tuple[int, int]:
        """Insert bank questions not already present. Historical (pre-used)
        entries are stored with used_at set so they can never be picked.
        Then purge unused questions in any retired category (so removing a
        category from the bank actually removes it from the live pool).
        Returns (inserted, retired) counts.
        """
        inserted = 0
        for q in bank.get("questions", []):
            cur = await self.conn.execute(
                "INSERT OR IGNORE INTO questions (id, category, question, options_json, style) "
                "VALUES (?, ?, ?, ?, ?)",
                (q["id"], q["category"], q["question"], json.dumps(q["options"]), q.get("style", "preference")),
            )
            inserted += cur.rowcount
        for q in bank.get("pre_used", []):
            await self.conn.execute(
                "INSERT OR IGNORE INTO questions (id, category, question, options_json, style, source, used_at) "
                "VALUES (?, ?, ?, ?, 'preference', 'history', ?)",
                (q["id"], q.get("category", "history"), q["question"], json.dumps(q.get("options", [])), q.get("used_at", utcnow())),
            )
        retired = 0
        for category in bank.get("retired_categories", []):
            cur = await self.conn.execute(
                "DELETE FROM questions WHERE category = ? AND used_at IS NULL", (category,)
            )
            retired += cur.rowcount
        await self.conn.commit()
        return inserted, retired

    # -- questions ---------------------------------------------------------

    async def unused_count(self) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM questions WHERE used_at IS NULL AND skipped = 0"
        ) as cur:
            return (await cur.fetchone())["n"]

    async def unused_by_category(self) -> dict[str, int]:
        async with self.conn.execute(
            "SELECT category, COUNT(*) AS n FROM questions "
            "WHERE used_at IS NULL AND skipped = 0 GROUP BY category ORDER BY n DESC"
        ) as cur:
            return {row["category"]: row["n"] for row in await cur.fetchall()}

    async def recent_categories(self, n: int = 2) -> list[str]:
        async with self.conn.execute(
            "SELECT q.category FROM polls p JOIN questions q ON q.id = p.question_id "
            "ORDER BY p.posted_at DESC LIMIT ?",
            (n,),
        ) as cur:
            return [row["category"] for row in await cur.fetchall()]

    async def pick_random_unused(self, exclude_categories: list[str]) -> aiosqlite.Row | None:
        """Random unused question, avoiding recently used categories when possible."""
        placeholders = ",".join("?" for _ in exclude_categories)
        base = "SELECT * FROM questions WHERE used_at IS NULL AND skipped = 0"
        if exclude_categories:
            query = f"{base} AND category NOT IN ({placeholders}) ORDER BY RANDOM() LIMIT 1"
            async with self.conn.execute(query, exclude_categories) as cur:
                row = await cur.fetchone()
            if row:
                return row
        async with self.conn.execute(f"{base} ORDER BY RANDOM() LIMIT 1") as cur:
            return await cur.fetchone()

    async def get_question(self, question_id: str) -> aiosqlite.Row | None:
        async with self.conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)) as cur:
            return await cur.fetchone()

    async def mark_used(self, question_id: str) -> None:
        await self.conn.execute(
            "UPDATE questions SET used_at = ? WHERE id = ?", (utcnow(), question_id)
        )
        await self.conn.commit()

    async def mark_skipped(self, question_id: str) -> None:
        await self.conn.execute("UPDATE questions SET skipped = 1 WHERE id = ?", (question_id,))
        await self.conn.commit()

    async def add_question(
        self, question_id: str, category: str, question: str, options: list[str], added_by: int
    ) -> None:
        await self.conn.execute(
            "INSERT INTO questions (id, category, question, options_json, style, source, added_by) "
            "VALUES (?, ?, ?, ?, 'preference', 'manual', ?)",
            (question_id, category, question, json.dumps(options), added_by),
        )
        await self.conn.commit()

    async def question_exists(self, text: str) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM questions WHERE lower(question) = lower(?)", (text,)
        ) as cur:
            return await cur.fetchone() is not None

    # -- polls ---------------------------------------------------------

    async def record_poll(
        self, question_id: str, message_id: int, channel_id: int, posted_at: str, closes_at: str
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO polls (question_id, message_id, channel_id, posted_at, closes_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (question_id, message_id, channel_id, posted_at, closes_at),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def finalize_poll(
        self, poll_id: int, total_votes: int, winner_option: str | None, results: dict[str, int]
    ) -> None:
        await self.conn.execute(
            "UPDATE polls SET total_votes = ?, winner_option = ?, results_json = ?, finalized = 1 "
            "WHERE id = ?",
            (total_votes, winner_option, json.dumps(results), poll_id),
        )
        await self.conn.commit()

    async def unfinalized_polls(self) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM polls WHERE finalized = 0 AND message_id IS NOT NULL"
        ) as cur:
            return list(await cur.fetchall())

    async def poll_history(self, limit: int = 10) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT p.*, q.question, q.category FROM polls p "
            "JOIN questions q ON q.id = p.question_id "
            "ORDER BY p.posted_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            return list(await cur.fetchall())

    async def polls_since(self, iso_ts: str) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT p.*, q.question, q.category FROM polls p "
            "JOIN questions q ON q.id = p.question_id "
            "WHERE p.posted_at >= ? AND p.finalized = 1 ORDER BY p.posted_at",
            (iso_ts,),
        ) as cur:
            return list(await cur.fetchall())

    async def stats(self) -> dict[str, Any]:
        async with self.conn.execute(
            "SELECT COUNT(*) AS posted, AVG(total_votes) AS avg_votes "
            "FROM polls WHERE finalized = 1"
        ) as cur:
            overall = dict(await cur.fetchone())
        async with self.conn.execute(
            "SELECT COUNT(*) AS ties FROM polls WHERE finalized = 1 AND winner_option IS NULL"
        ) as cur:
            overall["ties"] = (await cur.fetchone())["ties"]
        async with self.conn.execute(
            "SELECT q.category, AVG(p.total_votes) AS avg_votes, COUNT(*) AS n "
            "FROM polls p JOIN questions q ON q.id = p.question_id "
            "WHERE p.finalized = 1 GROUP BY q.category ORDER BY avg_votes DESC"
        ) as cur:
            overall["by_category"] = [dict(r) for r in await cur.fetchall()]
        return overall

    # -- tournaments ---------------------------------------------------------

    async def create_tournament(
        self, name: str, game: str, guild_id: int, channel_id: int, created_by: int,
        mode: str = "solo", team_size: int = 1, fmt: str = "single_elim",
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO tournaments (name, game, guild_id, channel_id, created_by, created_at, "
            "mode, team_size, format) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, game, guild_id, channel_id, created_by, utcnow(), mode, team_size, fmt),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_tournament(self, tid: int) -> aiosqlite.Row | None:
        async with self.conn.execute("SELECT * FROM tournaments WHERE id = ?", (tid,)) as cur:
            return await cur.fetchone()

    async def get_tournament_by_message(self, message_id: int) -> aiosqlite.Row | None:
        async with self.conn.execute(
            "SELECT * FROM tournaments WHERE message_id = ?", (message_id,)
        ) as cur:
            return await cur.fetchone()

    async def latest_active_tournament(self, guild_id: int) -> aiosqlite.Row | None:
        async with self.conn.execute(
            "SELECT * FROM tournaments WHERE guild_id = ? AND status IN ('open', 'running') "
            "ORDER BY id DESC LIMIT 1",
            (guild_id,),
        ) as cur:
            return await cur.fetchone()

    async def list_tournaments(self, guild_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM tournaments WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
            (guild_id, limit),
        ) as cur:
            return list(await cur.fetchall())

    async def open_tournaments(self) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM tournaments WHERE status = 'open' AND message_id IS NOT NULL"
        ) as cur:
            return list(await cur.fetchall())

    async def set_tournament_message(self, tid: int, message_id: int) -> None:
        await self.conn.execute(
            "UPDATE tournaments SET message_id = ? WHERE id = ?", (message_id, tid)
        )
        await self.conn.commit()

    async def set_tournament_status(self, tid: int, status: str) -> None:
        await self.conn.execute("UPDATE tournaments SET status = ? WHERE id = ?", (status, tid))
        await self.conn.commit()

    async def set_tournament_mode(self, tid: int, mode: str) -> None:
        await self.conn.execute("UPDATE tournaments SET mode = ? WHERE id = ?", (mode, tid))
        await self.conn.commit()

    async def set_tournament_bracket(self, tid: int, size: int, rounds: int) -> None:
        await self.conn.execute(
            "UPDATE tournaments SET bracket_size = ?, rounds = ?, status = 'running' WHERE id = ?",
            (size, rounds, tid),
        )
        await self.conn.commit()

    async def set_tournament_winner(self, tid: int, user_id: int) -> None:
        await self.conn.execute(
            "UPDATE tournaments SET winner_user_id = ?, status = 'finished' WHERE id = ?",
            (user_id, tid),
        )
        await self.conn.commit()

    # -- participants ---------------------------------------------------------

    async def add_participant(
        self, tid: int, user_id: int, display_name: str,
        team_id: int | None = None, is_captain: int = 0,
    ) -> bool:
        try:
            await self.conn.execute(
                "INSERT INTO participants (tournament_id, user_id, display_name, team_id, is_captain) "
                "VALUES (?, ?, ?, ?, ?)",
                (tid, user_id, display_name, team_id, is_captain),
            )
            await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def is_registered(self, tid: int, user_id: int) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM participants WHERE tournament_id = ? AND user_id = ?", (tid, user_id)
        ) as cur:
            return await cur.fetchone() is not None

    async def assign_participant_team(
        self, tid: int, user_id: int, team_id: int, is_captain: int
    ) -> None:
        """Attach an already-registered solo participant to a team (scramble draw)."""
        await self.conn.execute(
            "UPDATE participants SET team_id = ?, is_captain = ? "
            "WHERE tournament_id = ? AND user_id = ?",
            (team_id, is_captain, tid, user_id),
        )
        await self.conn.commit()

    async def remove_participant(self, tid: int, user_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM participants WHERE tournament_id = ? AND user_id = ?", (tid, user_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def get_participants(self, tid: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM participants WHERE tournament_id = ? ORDER BY seed IS NULL, seed, id",
            (tid,),
        ) as cur:
            return list(await cur.fetchall())

    async def set_participant_seed(self, tid: int, user_id: int, seed: int) -> None:
        await self.conn.execute(
            "UPDATE participants SET seed = ? WHERE tournament_id = ? AND user_id = ?",
            (seed, tid, user_id),
        )
        await self.conn.commit()

    async def participant_names(self, tid: int) -> dict[int, str]:
        return {p["user_id"]: p["display_name"] for p in await self.get_participants(tid)}

    # -- matches ---------------------------------------------------------

    async def insert_match(
        self, tid: int, match_no: int, rnd: int, pos: int,
        p1: int | None, p2: int | None, status: str, next_no: int | None, next_slot: int | None,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO matches (tournament_id, match_no, round, pos, p1_user_id, p2_user_id, "
            "status, next_match_no, next_slot) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, match_no, rnd, pos, p1, p2, status, next_no, next_slot),
        )
        await self.conn.commit()

    async def get_match(self, tid: int, match_no: int) -> aiosqlite.Row | None:
        async with self.conn.execute(
            "SELECT * FROM matches WHERE tournament_id = ? AND match_no = ?", (tid, match_no)
        ) as cur:
            return await cur.fetchone()

    async def get_matches(self, tid: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM matches WHERE tournament_id = ? ORDER BY round, pos", (tid,)
        ) as cur:
            return list(await cur.fetchall())

    async def resolve_match(
        self, tid: int, match_no: int, winner_id: int, score: str | None = None
    ) -> None:
        await self.conn.execute(
            "UPDATE matches SET winner_user_id = ?, status = 'done', score = ? "
            "WHERE tournament_id = ? AND match_no = ?",
            (winner_id, score, tid, match_no),
        )
        await self.conn.commit()

    async def fill_match_slot(self, tid: int, match_no: int, slot: int, user_id: int) -> None:
        col = "p1_user_id" if slot == 1 else "p2_user_id"
        await self.conn.execute(
            f"UPDATE matches SET p1_user_id = CASE WHEN ? = 1 THEN ? ELSE p1_user_id END, "
            f"p2_user_id = CASE WHEN ? = 2 THEN ? ELSE p2_user_id END WHERE tournament_id = ? "
            f"AND match_no = ?",
            (slot, user_id, slot, user_id, tid, match_no),
        )
        # promote to ready once both slots are filled
        await self.conn.execute(
            "UPDATE matches SET status = 'ready' WHERE tournament_id = ? AND match_no = ? "
            "AND status = 'pending' AND p1_user_id IS NOT NULL AND p2_user_id IS NOT NULL",
            (tid, match_no),
        )
        await self.conn.commit()

    # -- teams (3v3 mode) ---------------------------------------------------------

    async def create_team(self, tid: int, name: str, captain_user_id: int) -> int:
        cur = await self.conn.execute(
            "INSERT INTO teams (tournament_id, name, captain_user_id) VALUES (?, ?, ?)",
            (tid, name, captain_user_id),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_team(self, tid: int, team_id: int) -> aiosqlite.Row | None:
        async with self.conn.execute(
            "SELECT * FROM teams WHERE tournament_id = ? AND id = ?", (tid, team_id)
        ) as cur:
            return await cur.fetchone()

    async def get_teams(self, tid: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM teams WHERE tournament_id = ? ORDER BY seed IS NULL, seed, id", (tid,)
        ) as cur:
            return list(await cur.fetchall())

    async def team_of_user(self, tid: int, user_id: int) -> int | None:
        async with self.conn.execute(
            "SELECT team_id FROM participants WHERE tournament_id = ? AND user_id = ?",
            (tid, user_id),
        ) as cur:
            row = await cur.fetchone()
        return row["team_id"] if row else None

    async def team_member_ids(self, tid: int, team_id: int) -> list[int]:
        async with self.conn.execute(
            "SELECT user_id FROM participants WHERE tournament_id = ? AND team_id = ? "
            "ORDER BY is_captain DESC, id",
            (tid, team_id),
        ) as cur:
            return [r["user_id"] for r in await cur.fetchall()]

    async def team_members(self, tid: int, team_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM participants WHERE tournament_id = ? AND team_id = ? "
            "ORDER BY is_captain DESC, id",
            (tid, team_id),
        ) as cur:
            return list(await cur.fetchall())

    async def remove_team(self, tid: int, team_id: int) -> None:
        await self.conn.execute(
            "DELETE FROM participants WHERE tournament_id = ? AND team_id = ?", (tid, team_id)
        )
        await self.conn.execute(
            "DELETE FROM teams WHERE tournament_id = ? AND id = ?", (tid, team_id)
        )
        await self.conn.commit()

    async def set_team_seed(self, tid: int, team_id: int, seed: int) -> None:
        await self.conn.execute(
            "UPDATE teams SET seed = ? WHERE tournament_id = ? AND id = ?", (seed, tid, team_id)
        )
        await self.conn.commit()

    async def team_names(self, tid: int) -> dict[int, str]:
        return {t["id"]: t["name"] for t in await self.get_teams(tid)}

    # -- giveaways ---------------------------------------------------------

    async def create_giveaway(
        self, *, prize: str, description: str | None, host_id: int, guild_id: int,
        channel_id: int, winners_count: int, required_roles: list[int], role_logic: str,
        bonus_role_id: int | None, bonus_entries: int, image_name: str | None,
        created_by: int, ends_at: str,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO giveaways (prize, description, host_id, guild_id, channel_id, "
            "winners_count, required_roles, role_logic, bonus_role_id, bonus_entries, "
            "image_name, created_by, created_at, ends_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (prize, description, host_id, guild_id, channel_id, winners_count,
             json.dumps(required_roles), role_logic, bonus_role_id, bonus_entries,
             image_name, created_by, utcnow(), ends_at),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_giveaway(self, gid: int) -> aiosqlite.Row | None:
        async with self.conn.execute("SELECT * FROM giveaways WHERE id = ?", (gid,)) as cur:
            return await cur.fetchone()

    async def set_giveaway_message(self, gid: int, message_id: int) -> None:
        await self.conn.execute(
            "UPDATE giveaways SET message_id = ? WHERE id = ?", (message_id, gid)
        )
        await self.conn.commit()

    async def latest_active_giveaway(self, guild_id: int) -> aiosqlite.Row | None:
        async with self.conn.execute(
            "SELECT * FROM giveaways WHERE guild_id = ? AND ended = 0 AND cancelled = 0 "
            "ORDER BY id DESC LIMIT 1",
            (guild_id,),
        ) as cur:
            return await cur.fetchone()

    async def latest_giveaway(self, guild_id: int) -> aiosqlite.Row | None:
        async with self.conn.execute(
            "SELECT * FROM giveaways WHERE guild_id = ? ORDER BY id DESC LIMIT 1", (guild_id,)
        ) as cur:
            return await cur.fetchone()

    async def active_giveaways(self, guild_id: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM giveaways WHERE guild_id = ? AND ended = 0 AND cancelled = 0 "
            "ORDER BY ends_at",
            (guild_id,),
        ) as cur:
            return list(await cur.fetchall())

    async def unfinished_giveaways(self) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM giveaways WHERE ended = 0 AND cancelled = 0 AND message_id IS NOT NULL"
        ) as cur:
            return list(await cur.fetchall())

    async def end_giveaway(self, gid: int, winners: list[int]) -> None:
        await self.conn.execute(
            "UPDATE giveaways SET ended = 1, winners_json = ? WHERE id = ?",
            (json.dumps(winners), gid),
        )
        await self.conn.commit()

    async def cancel_giveaway(self, gid: int) -> None:
        await self.conn.execute("UPDATE giveaways SET cancelled = 1 WHERE id = ?", (gid,))
        await self.conn.commit()

    async def set_giveaway_winners(self, gid: int, winners: list[int]) -> None:
        await self.conn.execute(
            "UPDATE giveaways SET winners_json = ? WHERE id = ?", (json.dumps(winners), gid)
        )
        await self.conn.commit()

    async def has_entry(self, gid: int, user_id: int) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?", (gid, user_id)
        ) as cur:
            return await cur.fetchone() is not None

    async def add_entry(self, gid: int, user_id: int, entries: int) -> None:
        await self.conn.execute(
            "INSERT INTO giveaway_entries (giveaway_id, user_id, entries, joined_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(giveaway_id, user_id) DO UPDATE SET entries = excluded.entries",
            (gid, user_id, entries, utcnow()),
        )
        await self.conn.commit()

    async def remove_entry(self, gid: int, user_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?", (gid, user_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def get_entry(self, gid: int, user_id: int) -> aiosqlite.Row | None:
        async with self.conn.execute(
            "SELECT * FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?", (gid, user_id)
        ) as cur:
            return await cur.fetchone()

    async def giveaway_entries(self, gid: int) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM giveaway_entries WHERE giveaway_id = ?", (gid,)
        ) as cur:
            return list(await cur.fetchall())

    async def count_giveaway_entries(self, gid: int) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM giveaway_entries WHERE giveaway_id = ?", (gid,)
        ) as cur:
            return (await cur.fetchone())["n"]

    async def list_giveaways(self, guild_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        async with self.conn.execute(
            "SELECT * FROM giveaways WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
            (guild_id, limit),
        ) as cur:
            return list(await cur.fetchall())
