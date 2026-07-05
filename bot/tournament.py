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
