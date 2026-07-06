"""Render tournament brackets and champion banners as PNGs (Pillow).

Pure functions over plain dicts so this stays testable without Discord.
Palette matches Discord dark theme so images blend into the client.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Discord-dark palette
BG = (30, 31, 34)
CARD = (43, 45, 49)
CARD_DONE = (38, 40, 44)
BORDER = (63, 65, 71)
LINE = (75, 78, 86)
TEXT = (228, 229, 232)
DIM = (122, 125, 133)
GOLD = (240, 178, 50)
BLURPLE = (139, 148, 255)

BOX_W, BOX_H = 276, 66
GAP_X, GAP_Y = 78, 26
MARGIN = 44
HEADER = 112
MAX_BRACKET_SIZE = 32  # above this, callers should fall back to text

# Codepoint ranges the bundled fonts can't draw (CJK, emoji, symbols) —
# stripped from display names so nicknames like "龍 Ryuo Raiden 神" or
# emoji-decorated tags render as clean text instead of empty boxes.
_UNRENDERABLE = (
    (0x1F000, 0x1FAFF),  # emoji blocks
    (0x2600, 0x27BF),    # misc symbols / dingbats
    (0x2E80, 0x9FFF),    # CJK radicals through unified ideographs
    (0xAC00, 0xD7AF),    # hangul
    (0xF900, 0xFAFF),    # CJK compatibility
    (0xFE00, 0xFE0F),    # variation selectors
    (0x200B, 0x200D),    # zero-width chars
    (0x3000, 0x303F),    # CJK punctuation
)


def sanitize_name(text: str, fallback: str = "?") -> str:
    kept = "".join(
        ch for ch in text if not any(a <= ord(ch) <= b for a, b in _UNRENDERABLE)
    )
    kept = " ".join(kept.split())
    return kept or fallback

_FONT_CANDIDATES = [
    # Ubuntu
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    # Windows (local dev)
    (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for regular, bold_path in _FONT_CANDIDATES:
        path = bold_path if bold else regular
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)  # Pillow >= 10.1 bundled font


def _fit(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def round_label(r: int, rounds: int) -> str:
    if r == rounds:
        return "GRAND FINAL"
    if r == rounds - 1:
        return "SEMIFINALS"
    if r == rounds - 2:
        return "QUARTERFINALS"
    return f"ROUND {r}"


def render_bracket(
    title: str,
    subtitle: str,
    rounds: int,
    size: int,
    matches: list[dict],
    names: dict[int, str],
    seeds: dict[int, int] | None = None,
    champion: int | None = None,
) -> BytesIO:
    """matches: dicts with round, pos, match_no, p1_user_id, p2_user_id,
    winner_user_id, status, score (entrant ids). size = bracket size (power of 2).
    """
    seeds = seeds or {}
    title = sanitize_name(title)
    subtitle = sanitize_name(subtitle)
    r1_count = size // 2
    width = MARGIN * 2 + rounds * BOX_W + (rounds - 1) * GAP_X
    height = HEADER + r1_count * (BOX_H + GAP_Y) - GAP_Y + MARGIN + 20

    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    f_title = _font(30, bold=True)
    f_sub = _font(16)
    f_round = _font(15, bold=True)
    f_name = _font(17)
    f_name_b = _font(17, bold=True)
    f_side = _font(14, bold=True)
    f_foot = _font(13)

    # header
    draw.text((MARGIN, 26), title, font=f_title, fill=TEXT)
    draw.text((MARGIN, 66), subtitle, font=f_sub, fill=DIM)
    if champion is not None:
        champ_text = f"CHAMPION: {sanitize_name(names.get(champion, '?'))}"
        tl = draw.textlength(champ_text, font=_font(19, bold=True))
        draw.text((width - MARGIN - tl, 34), champ_text, font=_font(19, bold=True), fill=GOLD)
        draw.line(
            (width - MARGIN - tl, 62, width - MARGIN, 62), fill=GOLD, width=2
        )

    # geometry: vertical centers per (round, pos)
    centers: dict[tuple[int, int], float] = {}
    for i in range(r1_count):
        centers[(1, i)] = HEADER + i * (BOX_H + GAP_Y) + BOX_H / 2
    for r in range(2, rounds + 1):
        for pos in range(size // (2 ** r)):
            centers[(r, pos)] = (centers[(r - 1, 2 * pos)] + centers[(r - 1, 2 * pos + 1)]) / 2

    def col_x(r: int) -> int:
        return MARGIN + (r - 1) * (BOX_W + GAP_X)

    # round labels
    for r in range(1, rounds + 1):
        label = round_label(r, rounds)
        tl = draw.textlength(label, font=f_round)
        draw.text((col_x(r) + (BOX_W - tl) / 2, HEADER - 34), label, font=f_round, fill=BLURPLE)

    # connectors first (under the boxes)
    for m in matches:
        r, pos = m["round"], m["pos"]
        if r >= rounds:
            continue
        x_out = col_x(r) + BOX_W
        cy = centers[(r, pos)]
        target_cy = centers[(r + 1, pos // 2)]
        mid_x = x_out + GAP_X / 2
        draw.line((x_out, cy, mid_x, cy), fill=LINE, width=2)
        draw.line((mid_x, cy, mid_x, target_cy), fill=LINE, width=2)
        draw.line((mid_x, target_cy, col_x(r + 1), target_cy), fill=LINE, width=2)

    # match boxes
    by_no = {m["match_no"]: m for m in matches}
    for m in matches:
        r, pos = m["round"], m["pos"]
        x = col_x(r)
        cy = centers[(r, pos)]
        y = cy - BOX_H / 2
        done = m["status"] == "done"
        draw.rounded_rectangle(
            (x, y, x + BOX_W, y + BOX_H), radius=9,
            fill=CARD_DONE if done else CARD, outline=BORDER, width=1,
        )
        draw.line((x + 10, cy, x + BOX_W - 10, cy), fill=BORDER, width=1)
        # match number tag
        draw.text((x + BOX_W - 6, y - 2), f"#{m['match_no']}", font=f_foot, fill=DIM, anchor="rb")

        for slot, eid in ((1, m["p1_user_id"]), (2, m["p2_user_id"])):
            row_cy = y + BOX_H * (0.27 if slot == 1 else 0.73)
            if eid is None:
                text = "BYE" if r == 1 else "TBD"
                draw.text((x + 14, row_cy), text, font=f_name, fill=DIM, anchor="lm")
                continue
            is_winner = done and m["winner_user_id"] == eid
            seed = seeds.get(eid)
            name = sanitize_name(names.get(eid, str(eid)))
            label = f"{seed}  {name}" if seed else name
            color = GOLD if is_winner else (DIM if done else TEXT)
            font = f_name_b if is_winner else f_name
            label = _fit(draw, label, font, BOX_W - 64)
            draw.text((x + 14, row_cy), label, font=font, fill=color, anchor="lm")
            # right side: winner gets score / W, byes annotated
            if is_winner:
                other = m["p2_user_id"] if slot == 1 else m["p1_user_id"]
                side = "bye" if (r == 1 and other is None) else (m.get("score") or "W")
                draw.text((x + BOX_W - 12, row_cy), str(side), font=f_side,
                          fill=DIM if side == "bye" else GOLD, anchor="rm")

    draw.text((MARGIN, height - 26), "TPCD · Single Elimination", font=f_foot, fill=DIM)

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def render_champion(tournament_name: str, champion_name: str, members: list[str]) -> BytesIO:
    """Gold banner for the tournament winner."""
    champion_name = sanitize_name(champion_name)
    members = [sanitize_name(m) for m in members]
    tournament_name = sanitize_name(tournament_name)
    width, height = 900, 300
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle((10, 10, width - 10, height - 10), radius=18, outline=GOLD, width=3)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=14, outline=BORDER, width=1)

    header = " ".join("CHAMPIONS" if len(members) > 1 else "CHAMPION")
    f_head = _font(20, bold=True)
    draw.text((width / 2, 62), header, font=f_head, fill=DIM, anchor="mm")

    size = 52
    f_name = _font(size, bold=True)
    while draw.textlength(champion_name, font=f_name) > width - 140 and size > 22:
        size -= 2
        f_name = _font(size, bold=True)
    draw.text((width / 2, 128), champion_name, font=f_name, fill=GOLD, anchor="mm")

    if members and (len(members) > 1 or members[0] != champion_name):
        roster = "  •  ".join(members)
        f_roster = _font(20)
        roster_fit = _fit(draw, roster, f_roster, width - 140)
        draw.text((width / 2, 192), roster_fit, font=f_roster, fill=TEXT, anchor="mm")

    f_foot = _font(17)
    draw.text((width / 2, 244), tournament_name, font=f_foot, fill=DIM, anchor="mm")

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
