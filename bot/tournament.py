"""Pure single-elimination bracket logic. No Discord, no DB — so it can be
unit-tested offline. The cog turns these structures into DB rows + embeds.
"""
from __future__ import annotations

from dataclasses import dataclass


def next_power_of_two(n: int) -> int:
    """Smallest power of two >= n (minimum 2)."""
    if n <= 2:
        return 2
    return 1 << (n - 1).bit_length()


def seed_positions(size: int) -> list[int]:
    """Standard tournament seeding order for a bracket of `size` (a power of
    two). Returns the seed number that belongs in each bracket slot, so that
    seed 1 and seed 2 can only meet in the final and byes (the highest seeds)
    are spread against the top seeds instead of each other.
    """
    seeds = [1]
    while len(seeds) < size:
        length = len(seeds) * 2
        nxt: list[int] = []
        for s in seeds:
            nxt.append(s)
            nxt.append(length + 1 - s)
        seeds = nxt
    return seeds


@dataclass
class BracketMatch:
    match_no: int          # human-facing, sequential within the tournament
    round: int             # 1 = first round
    pos: int               # 0-based index within the round
    p1: int | None         # user id, or None for a bye (round 1) / TBD (later)
    p2: int | None
    next_match_no: int | None
    next_slot: int | None  # 1 or 2 — which slot of the next match the winner fills


def build_bracket(participants: list[int]) -> tuple[int, int, list[BracketMatch]]:
    """Build a single-elimination bracket.

    `participants` is an ordered list of user ids (index 0 = seed 1). Returns
    (bracket_size, rounds, matches). Round-1 matches carry real players or a
    None where a bye sits; later rounds start empty (TBD) and fill as winners
    report in.
    """
    n = len(participants)
    if n < 2:
        raise ValueError("need at least 2 participants")
    size = next_power_of_two(n)
    rounds = (size - 1).bit_length()  # log2(size)
    order = seed_positions(size)
    # slot i holds the participant whose seed sits there, or None (bye)
    slots: list[int | None] = [
        participants[order[i] - 1] if order[i] <= n else None for i in range(size)
    ]

    # assign a stable human match number to every (round, pos)
    match_no: dict[tuple[int, int], int] = {}
    counter = 1
    for r in range(1, rounds + 1):
        for pos in range(size // (2 ** r)):
            match_no[(r, pos)] = counter
            counter += 1

    matches: list[BracketMatch] = []
    for r in range(1, rounds + 1):
        for pos in range(size // (2 ** r)):
            if r == 1:
                p1, p2 = slots[pos * 2], slots[pos * 2 + 1]
            else:
                p1 = p2 = None
            if r < rounds:
                nxt = match_no[(r + 1, pos // 2)]
                nslot = (pos % 2) + 1
            else:
                nxt, nslot = None, None
            matches.append(BracketMatch(match_no[(r, pos)], r, pos, p1, p2, nxt, nslot))
    return size, rounds, matches


def build_round_robin(entrants: list[int]) -> tuple[int, int, list[BracketMatch]]:
    """Round-robin schedule via the circle method: every entrant plays every
    other exactly once. Returns (n_entrants, n_rounds, matches). Matches have
    no next pointers (winners don't advance; standings decide the champion).
    """
    ids = list(entrants)
    n = len(ids)
    if n < 2:
        raise ValueError("need at least 2 entrants")
    arr: list[int | None] = ids[:]
    if n % 2 == 1:
        arr.append(None)  # phantom entrant → whoever faces it sits out that round
    m = len(arr)
    rounds = m - 1
    half = m // 2

    matches: list[BracketMatch] = []
    match_no = 1
    lst = arr[:]
    for r in range(rounds):
        pos = 0
        for i in range(half):
            a, b = lst[i], lst[m - 1 - i]
            if a is not None and b is not None:
                matches.append(BracketMatch(match_no, r + 1, pos, a, b, None, None))
                match_no += 1
                pos += 1
        # rotate everything except the first element one step clockwise
        lst = [lst[0], lst[-1], *lst[1:-1]]
    return n, rounds, matches


def round_robin_standings(entrants: list[int], matches: list[dict]) -> list[dict]:
    """Rank entrants by wins, then game differential (from scores), then
    head-to-head. `matches` are dicts with p1_user_id, p2_user_id,
    winner_user_id, score, status. Returns a list of standings rows sorted
    best-first, each: {entrant, played, wins, losses, diff}.
    """
    stats = {
        e: {"entrant": e, "played": 0, "wins": 0, "losses": 0, "diff": 0} for e in entrants
    }
    beat: set[tuple[int, int]] = set()
    for mt in matches:
        if mt.get("status") != "done" or mt.get("winner_user_id") is None:
            continue
        w = mt["winner_user_id"]
        loser = mt["p2_user_id"] if w == mt["p1_user_id"] else mt["p1_user_id"]
        if w not in stats or loser not in stats:
            continue
        stats[w]["wins"] += 1
        stats[w]["played"] += 1
        stats[loser]["losses"] += 1
        stats[loser]["played"] += 1
        beat.add((w, loser))
        d = _score_margin(mt.get("score"))
        stats[w]["diff"] += d
        stats[loser]["diff"] -= d

    ranked = sorted(stats.values(), key=lambda s: (-s["wins"], -s["diff"]))
    # break exact (wins, diff) ties between adjacent pairs by head-to-head
    for i in range(len(ranked) - 1):
        a, b = ranked[i], ranked[i + 1]
        if a["wins"] == b["wins"] and a["diff"] == b["diff"]:
            if (b["entrant"], a["entrant"]) in beat:
                ranked[i], ranked[i + 1] = ranked[i + 1], ranked[i]
    return ranked


def _score_margin(score: str | None) -> int:
    """'2-1' -> 1. Unparseable or missing -> 0."""
    if not score or "-" not in score:
        return 0
    a, _, b = score.partition("-")
    try:
        return abs(int(a.strip()) - int(b.strip()))
    except ValueError:
        return 0
