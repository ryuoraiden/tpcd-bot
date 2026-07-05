"""Validate the question bank without needing a bot token or dependencies.

Run: python -m bot.seed_check
Checks: unique ids, no duplicate question text (including vs the 13
historical polls), 2-10 options each, no empty options, category counts.
Exits non-zero on any failure so it can gate deploys.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

BANK_PATH = Path(__file__).parent / "data" / "question_bank.json"


def main() -> int:
    bank = json.loads(BANK_PATH.read_text(encoding="utf-8"))
    questions = bank.get("questions", [])
    pre_used = bank.get("pre_used", [])
    errors: list[str] = []

    ids = [q["id"] for q in questions + pre_used]
    for qid, n in Counter(ids).items():
        if n > 1:
            errors.append(f"duplicate id: {qid}")

    texts = Counter(q["question"].strip().lower() for q in questions + pre_used)
    for text, n in texts.items():
        if n > 1:
            errors.append(f"duplicate question text: {text!r}")

    for q in questions:
        opts = q.get("options", [])
        if not 2 <= len(opts) <= 10:
            errors.append(f"{q['id']}: {len(opts)} options (must be 2-10)")
        if any(not str(o).strip() for o in opts):
            errors.append(f"{q['id']}: empty option")
        if len(set(o.strip().lower() for o in opts)) != len(opts):
            errors.append(f"{q['id']}: duplicate options")
        if len(q["question"]) > 300:
            errors.append(f"{q['id']}: question longer than Discord's 300-char poll limit")
        for o in opts:
            if len(str(o)) > 55:
                errors.append(f"{q['id']}: option longer than Discord's 55-char answer limit: {o!r}")

    by_cat = Counter(q["category"] for q in questions)
    print(f"Question bank: {len(questions)} questions, {len(pre_used)} historical pre-used")
    for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:22s} {n}")

    if errors:
        print(f"\nFAILED: {len(errors)} problem(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\nOK: bank is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
