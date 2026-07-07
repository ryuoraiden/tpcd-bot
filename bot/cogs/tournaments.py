"""Tournaments for #tournament-hub.

Modes: solo (1v1), duo (2v2), trio (3v3). Formats: single elimination and
round robin. Solo entrants are users; team entrants are teams. Match rows
store "entrant ids" (a user id or a team id) in p1_user_id/p2_user_id.

UI: rendered bracket and standings images, interactive registration, score
reporting, a champion banner, and a promo announcement with a one-click
participant-role button.
"""
from __future__ import annotations

import logging
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from ..bracket_render import (
    MAX_BRACKET_SIZE,
    render_bracket,
    render_champion,
    render_standings,
    round_label,
)
from ..checks import is_staff, staff_only
from ..config import config
from ..tournament import build_bracket, build_round_robin, round_robin_standings

log = logging.getLogger(__name__)

GAMES = ["Brawl Stars", "Clash Royale", "Other"]
DEFAULT_TEAM_SIZE = 3
PARTICIPANT_ROLE_ID = 1380414459050332160
EVENT_BANNER = Path(__file__).parent.parent / "data" / "assets" / "welcome_banner.png"

JOIN_ID = "tpcd_tourney_join"
LEAVE_ID = "tpcd_tourney_leave"
TEAM_REG_ID = "tpcd_team_register"
TEAM_WITHDRAW_ID = "tpcd_team_withdraw"


def team_size_of(t) -> int:
    if t["mode"] in ("team", "scramble"):
        return t["team_size"] or DEFAULT_TEAM_SIZE
    return 1


def registers_individually(t) -> bool:
    """True when people sign up solo: pure solo, or scramble (drawn into teams)."""
    return t["mode"] in ("solo", "scramble")


def vs_label(t) -> str:
    s = t["team_size"] or 1
    return f"{s}v{s}"


def entrant_noun(t) -> str:
    return "teams" if t["mode"] == "team" else "players"


def is_round_robin(t) -> bool:
    return t["format"] == "round_robin"


def result_verb(score: str | None) -> str:
    """Human phrasing for a result line, score-aware when possible."""
    if score:
        tail = score.split("-")[-1].strip()
        if tail == "0":
            return "swept"
        if tail.isdigit() and int(tail) >= 1 and score[0].isdigit():
            first = score.split("-")[0].strip()
            if first.isdigit() and int(first) - int(tail) == 1:
                return "edged out"
    return random.choice(["beat", "took down", "got past"])


# ---------------------------------------------------------------------------
# registration views
# ---------------------------------------------------------------------------


class SoloRegView(discord.ui.View):
    """Persistent Join/Leave buttons for solo tournaments."""

    def __init__(self, cog: "Tournaments") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def _tournament(self, interaction: discord.Interaction):
        t = await self.cog.db.get_tournament_by_message(interaction.message.id)
        if t is None or t["status"] != "open":
            await interaction.response.send_message(
                "Registration for this tournament is closed.", ephemeral=True
            )
            return None
        return t

    @discord.ui.button(label="Join", emoji="⚔️", style=discord.ButtonStyle.success, custom_id=JOIN_ID)
    async def join(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        t = await self._tournament(interaction)
        if t is None:
            return
        ok = await self.cog.db.add_participant(
            t["id"], interaction.user.id, interaction.user.display_name
        )
        if ok:
            await self.cog.refresh_registration(t["id"])
            await interaction.response.send_message(
                f"You're in **{t['name']}**. Good luck!", ephemeral=True
            )
        else:
            await interaction.response.send_message("You already joined this one.", ephemeral=True)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary, custom_id=LEAVE_ID)
    async def leave(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        t = await self._tournament(interaction)
        if t is None:
            return
        removed = await self.cog.db.remove_participant(t["id"], interaction.user.id)
        if removed:
            await self.cog.refresh_registration(t["id"])
            await interaction.response.send_message("You left the tournament.", ephemeral=True)
        else:
            await interaction.response.send_message("You weren't registered.", ephemeral=True)


class TeamRegView(discord.ui.View):
    """Persistent Register/Withdraw buttons for team (duo/trio) tournaments."""

    def __init__(self, cog: "Tournaments") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def _tournament(self, interaction: discord.Interaction):
        t = await self.cog.db.get_tournament_by_message(interaction.message.id)
        if t is None or t["status"] != "open":
            await interaction.response.send_message(
                "Registration for this tournament is closed.", ephemeral=True
            )
            return None
        return t

    @discord.ui.button(
        label="Register team", emoji="🛡️", style=discord.ButtonStyle.success, custom_id=TEAM_REG_ID
    )
    async def register(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        t = await self._tournament(interaction)
        if t is None:
            return
        if await self.cog.db.is_registered(t["id"], interaction.user.id):
            await interaction.response.send_message(
                "You're already on a team in this tournament.", ephemeral=True
            )
            return
        await interaction.response.send_modal(TeamNameModal(self.cog, t["id"], team_size_of(t)))

    @discord.ui.button(label="Withdraw team", style=discord.ButtonStyle.secondary, custom_id=TEAM_WITHDRAW_ID)
    async def withdraw(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        t = await self._tournament(interaction)
        if t is None:
            return
        msg = await self.cog.withdraw_team(t, interaction.user, staff=is_staff(interaction))
        await interaction.response.send_message(msg, ephemeral=True)


class TeamNameModal(discord.ui.Modal):
    team_name = discord.ui.TextInput(
        label="Team name", placeholder="e.g. Bush Campers United", min_length=2, max_length=28
    )

    def __init__(self, cog: "Tournaments", tid: int, team_size: int) -> None:
        super().__init__(title=f"Register your {team_size}v{team_size} team")
        self.cog = cog
        self.tid = tid
        self.team_size = team_size

    async def on_submit(self, interaction: discord.Interaction) -> None:
        view = TeammatePickView(
            self.cog, self.tid, str(self.team_name), interaction.user, self.team_size
        )
        need = self.team_size - 1
        await interaction.response.send_message(
            f"**{self.team_name}** — now pick your {need} teammate{'s' if need != 1 else ''}:",
            view=view,
            ephemeral=True,
        )


class TeammatePickView(discord.ui.View):
    """Ephemeral follow-up to the name modal: pick teammates, then confirm."""

    def __init__(
        self, cog: "Tournaments", tid: int, team_name: str, captain: discord.Member, team_size: int
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.tid = tid
        self.team_name = team_name
        self.captain = captain
        self.team_size = team_size
        self.need = team_size - 1
        self.mates: list[discord.Member] = []
        # size the picker to this tournament's team size
        for child in self.children:
            if isinstance(child, discord.ui.UserSelect):
                child.min_values = child.max_values = self.need
                child.placeholder = f"Pick your {self.need} teammate{'s' if self.need != 1 else ''}"

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Pick your teammates", min_values=1, max_values=2)
    async def pick(self, interaction: discord.Interaction, select: discord.ui.UserSelect) -> None:
        self.mates = list(select.values)
        await interaction.response.defer()

    @discord.ui.button(label="Confirm team", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if len(self.mates) != self.need:
            await interaction.response.send_message("Pick your teammates first.", ephemeral=True)
            return
        t = await self.cog.db.get_tournament(self.tid)
        if t is None or t["status"] != "open":
            await interaction.response.edit_message(
                content="Registration closed while you were picking.", view=None
            )
            return
        ok, msg = await self.cog.register_team(t, self.captain, self.mates, self.team_name)
        if ok:
            await interaction.response.edit_message(content=msg, view=None)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Registration cancelled.", view=None)


# ---------------------------------------------------------------------------
# announcement participant button
# ---------------------------------------------------------------------------


class ParticipantButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"tpcd:tpart:(?P<role_id>[0-9]+):(?P<goal>[0-9]+)",
):
    """One-click sign-up: toggles the tournament participant role and keeps a
    live count on the announcement embed. Role id + player goal are encoded in
    the custom_id, so it survives restarts with no stored state.
    """

    def __init__(self, role_id: int, goal: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Join tournament",
                emoji="🏆",
                style=discord.ButtonStyle.success,
                custom_id=f"tpcd:tpart:{role_id}:{goal}",
            )
        )
        self.role_id = role_id
        self.goal = goal

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str]) -> "ParticipantButton":
        return cls(int(match["role_id"]), int(match["goal"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        role = interaction.guild.get_role(self.role_id)
        if role is None:
            await interaction.response.send_message(
                "The participant role is missing. Ping a Manager.", ephemeral=True
            )
            return
        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Left tournament sign-up")
                joined = False
            else:
                await member.add_roles(role, reason="Tournament sign-up")
                joined = True
        except discord.Forbidden:
            await interaction.response.send_message(
                "I can't assign that role yet. Staff: give me **Manage Roles** and move my "
                "role above the participant role.",
                ephemeral=True,
            )
            return

        count = len(role.members)
        try:
            embed = _bump_signup(interaction.message.embeds[0], count, self.goal)
            await interaction.message.edit(embed=embed)
        except (IndexError, discord.HTTPException):
            pass

        if joined:
            reply = "You're in. You'll be pinged with the schedule and match updates."
            if self.goal and count >= self.goal:
                reply += f" We've hit the {self.goal}-player minimum, it's on!"
        else:
            reply = "You've left the sign-up. Hit the button again if you change your mind."
        await interaction.response.send_message(reply, ephemeral=True)


def _bump_signup(embed: discord.Embed, count: int, goal: int) -> discord.Embed:
    data = embed.to_dict()
    val = f"**{count}**" + (
        f" / {goal} needed{' ✅' if count >= goal else ''}" if goal else " so far"
    )
    for field in data.get("fields", []):
        if field["name"].startswith("Signed up"):
            field["value"] = val
    return discord.Embed.from_dict(data)


# ---------------------------------------------------------------------------
# cog
# ---------------------------------------------------------------------------


class Tournaments(commands.Cog):
    tournament = app_commands.Group(name="tournament", description="Run inter-club tournaments")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db

    async def cog_load(self) -> None:
        self.bot.add_view(SoloRegView(self))
        self.bot.add_view(TeamRegView(self))
        self.bot.add_dynamic_items(ParticipantButton)

    # -- entrant helpers ---------------------------------------------------------

    async def names_map(self, t) -> dict[int, str]:
        if t["mode"] == "team":
            return await self.db.team_names(t["id"])
        return await self.db.participant_names(t["id"])

    async def seeds_map(self, t) -> dict[int, int]:
        if t["mode"] == "team":
            return {tm["id"]: tm["seed"] for tm in await self.db.get_teams(t["id"]) if tm["seed"]}
        return {
            p["user_id"]: p["seed"] for p in await self.db.get_participants(t["id"]) if p["seed"]
        }

    async def entrant_member_ids(self, t, entrant_id: int) -> list[int]:
        if t["mode"] == "team":
            return await self.db.team_member_ids(t["id"], entrant_id)
        return [entrant_id]

    async def register_team(
        self, t, captain: discord.Member, mates: list[discord.Member], team_name: str
    ) -> tuple[bool, str]:
        need = team_size_of(t)
        members = [captain, *mates]
        if any(m.bot for m in members):
            return False, "Teams can't include bots."
        if len({m.id for m in members}) != need:
            return False, f"A {need}v{need} team needs {need} different players (you're the captain)."
        already = [m.mention for m in members if await self.db.is_registered(t["id"], m.id)]
        if already:
            return False, f"Already in this tournament: {', '.join(already)}."
        team_id = await self.db.create_team(t["id"], team_name, captain.id)
        for m in members:
            await self.db.add_participant(
                t["id"], m.id, m.display_name, team_id=team_id,
                is_captain=1 if m.id == captain.id else 0,
            )
        await self.refresh_registration(t["id"])
        mate_pings = " and ".join(m.mention for m in mates)
        return True, f"🛡️ **{team_name}** locked in: you + {mate_pings}. GLHF!"

    async def withdraw_team(self, t, user: discord.Member, staff: bool) -> str:
        team_id = await self.db.team_of_user(t["id"], user.id)
        team = await self.db.get_team(t["id"], team_id) if team_id else None
        if team is None:
            return "You're not on a team in this tournament."
        if team["captain_user_id"] != user.id and not staff:
            return "Only the team captain or staff can withdraw a team."
        await self.db.remove_team(t["id"], team_id)
        await self.refresh_registration(t["id"])
        return f"Withdrew team **{team['name']}**."

    # -- registration embed ---------------------------------------------------------

    async def registration_embed(self, tid: int) -> discord.Embed:
        t = await self.db.get_tournament(tid)
        fmt_name = "Round robin" if is_round_robin(t) else "Single elimination"
        size = team_size_of(t)
        if t["mode"] == "team":
            fmt = f"⚔️ {fmt_name} · 👥 **{size}v{size} teams**"
            how = f"Captains: hit **Register team**, name your squad, pick your {size - 1} teammate(s)."
        elif t["mode"] == "scramble":
            fmt = f"⚔️ {fmt_name} · 🎲 **{size}v{size}, random teams**"
            how = (
                f"Hit **Join** to enter on your own. When it starts, everyone is drawn into "
                f"random teams of {size} — no need to find a squad first."
            )
        else:
            fmt = f"⚔️ {fmt_name} · 👤 **solo (1v1)**"
            how = "Hit **Join** to enter."
        embed = discord.Embed(
            title=f"🏆 {t['name']}",
            description=f"**Game:** {t['game']}\n{fmt}\n\n{how}",
            color=discord.Color.gold(),
        )
        if t["mode"] == "team":
            teams = await self.db.get_teams(tid)
            lines = []
            for i, team in enumerate(teams, 1):
                members = await self.db.team_members(tid, team["id"])
                tag = ", ".join(
                    (f"👑 **{m['display_name']}**" if m["is_captain"] else m["display_name"])
                    for m in members
                )
                lines.append(f"`{i}.` **{team['name']}** — {tag}")
            embed.add_field(
                name=f"Teams · {len(teams)} registered",
                value="\n".join(lines) or "*No teams yet — be the first!*",
                inline=False,
            )
        else:
            players = await self.db.get_participants(tid)
            roster = "\n".join(f"`{i}.` {p['display_name']}" for i, p in enumerate(players, 1))
            embed.add_field(
                name=f"Players · {len(players)} registered",
                value=roster or "*Nobody yet — be the first!*",
                inline=False,
            )
        embed.set_footer(text=f"Tournament #{tid} · staff: /tournament start when ready")
        return embed

    async def refresh_registration(self, tid: int) -> None:
        t = await self.db.get_tournament(tid)
        if t is None or not t["message_id"]:
            return
        try:
            channel = self.bot.get_channel(t["channel_id"]) or await self.bot.fetch_channel(
                t["channel_id"]
            )
            msg = await channel.fetch_message(t["message_id"])
            await msg.edit(embed=await self.registration_embed(tid))
        except discord.HTTPException:
            pass

    # -- bracket display (single elimination) ---------------------------------------------------------

    def text_bracket_embed(self, t, matches, names) -> discord.Embed:
        rounds = t["rounds"] or 1
        embed = discord.Embed(title=f"🏆 {t['name']} — Bracket", color=discord.Color.gold())
        for r in range(1, rounds + 1):
            lines = []
            for m in matches:
                if m["round"] != r:
                    continue
                is_r1 = r == 1

                def nm(eid):
                    if eid is None:
                        return "BYE" if is_r1 else "TBD"
                    return names.get(eid, "?")

                if m["status"] == "done":
                    win = names.get(m["winner_user_id"], "?")
                    if m["p1_user_id"] is None or m["p2_user_id"] is None:
                        lines.append(f"`#{m['match_no']}` {win} *(bye)*")
                    else:
                        loser = (
                            nm(m["p2_user_id"])
                            if m["winner_user_id"] == m["p1_user_id"]
                            else nm(m["p1_user_id"])
                        )
                        score = f" {m['score']}" if m["score"] else ""
                        lines.append(f"`#{m['match_no']}` ✅ **{win}**{score} def. {loser}")
                else:
                    lines.append(f"`#{m['match_no']}` {nm(m['p1_user_id'])} vs {nm(m['p2_user_id'])}")
            embed.add_field(name=round_label(r, rounds).title(), value="\n".join(lines) or "—", inline=False)
        return embed

    async def bracket_message(self, t) -> dict:
        names = await self.names_map(t)
        matches = [dict(m) for m in await self.db.get_matches(t["id"])]
        rounds = t["rounds"] or 1

        done = sum(1 for m in matches if m["status"] == "done")
        ready = [m for m in matches if m["status"] == "ready"]
        cur_rounds = [m["round"] for m in matches if m["status"] != "done"]

        embed = discord.Embed(color=discord.Color.gold())
        if t["status"] == "finished":
            embed.description = f"Final bracket. **{names.get(t['winner_user_id'], '?')}** 🏆"
        else:
            stage = round_label(min(cur_rounds), rounds).title() if cur_rounds else "…"
            waiting = ""
            if ready:
                nums = ", ".join(f"#{m['match_no']}" for m in ready)
                waiting = f" · waiting on {nums}"
            embed.description = f"**{stage}** · {done} of {len(matches)} matches played{waiting}"
            embed.set_footer(text="Staff: /tournament report")

        if (t["bracket_size"] or 0) <= MAX_BRACKET_SIZE:
            try:
                seeds = await self.seeds_map(t)
                sub = f"{t['game']} · {vs_label(t)} · {t['bracket_size']} slots · {entrant_noun(t)}"
                buf = render_bracket(
                    t["name"], sub, rounds, t["bracket_size"], matches, names, seeds,
                    champion=t["winner_user_id"] if t["status"] == "finished" else None,
                )
                file = discord.File(buf, filename="bracket.png")
                embed.set_image(url="attachment://bracket.png")
                return {"embed": embed, "files": [file]}
            except Exception:  # noqa: BLE001
                log.exception("Bracket render failed; falling back to text")
        return {"embed": self.text_bracket_embed(t, matches, names)}

    # -- standings display (round robin) ---------------------------------------------------------

    async def standings_message(self, t) -> dict:
        names = await self.names_map(t)
        matches = [dict(m) for m in await self.db.get_matches(t["id"])]
        entrants = list(names.keys())
        standings = round_robin_standings(entrants, matches)
        done = sum(1 for m in matches if m["status"] == "done")
        champion = t["winner_user_id"] if t["status"] == "finished" else None

        embed = discord.Embed(color=discord.Color.gold())
        if t["status"] == "finished":
            embed.description = f"Final standings. **{names.get(champion, '?')}** 🏆"
        else:
            embed.description = f"**Round robin** · {done} of {len(matches)} matches played"
            unplayed = [m for m in matches if m["status"] != "done"]
            if unplayed:
                fx = "\n".join(
                    f"`#{m['match_no']}` {names.get(m['p1_user_id'], '?')} vs {names.get(m['p2_user_id'], '?')}"
                    for m in unplayed[:12]
                )
                if len(unplayed) > 12:
                    fx += f"\n…and {len(unplayed) - 12} more"
                embed.add_field(name="Remaining matches", value=fx, inline=False)
            embed.set_footer(text="Staff: /tournament report")

        try:
            sub = f"{t['game']} · {vs_label(t)} · round robin · {len(entrants)} {entrant_noun(t)}"
            buf = render_standings(t["name"], sub, standings, names, champion=champion)
            file = discord.File(buf, filename="standings.png")
            embed.set_image(url="attachment://standings.png")
            return {"embed": embed, "files": [file]}
        except Exception:  # noqa: BLE001
            log.exception("Standings render failed; falling back to text")
        lines = [
            f"`{i}.` **{names.get(s['entrant'], '?')}** — {s['wins']}W {s['losses']}L"
            for i, s in enumerate(standings, 1)
        ]
        embed.add_field(name="Standings", value="\n".join(lines) or "—", inline=False)
        return {"embed": embed}

    async def display_message(self, t) -> dict:
        if is_round_robin(t):
            return await self.standings_message(t)
        return await self.bracket_message(t)

    # -- shared announcements ---------------------------------------------------------

    async def announce_matchups(self, channel, t, match_nos: list[int], heading: str) -> None:
        if not match_nos:
            return
        names = await self.names_map(t)
        rounds = t["rounds"] or 1
        lines = []
        for no in match_nos:
            m = await self.db.get_match(t["id"], no)
            if m is None or m["p1_user_id"] is None or m["p2_user_id"] is None:
                continue
            stage = round_label(m["round"], rounds).title() if not is_round_robin(t) else f"Round {m['round']}"
            if t["mode"] == "team":
                n1, n2 = names.get(m["p1_user_id"], "?"), names.get(m["p2_user_id"], "?")
                p1 = " ".join(f"<@{u}>" for u in await self.entrant_member_ids(t, m["p1_user_id"]))
                p2 = " ".join(f"<@{u}>" for u in await self.entrant_member_ids(t, m["p2_user_id"]))
                lines.append(f"`#{m['match_no']}` {stage}: **{n1}** vs **{n2}**")
                lines.append(f"{p1} vs {p2}")
            else:
                lines.append(f"`#{m['match_no']}` {stage}: <@{m['p1_user_id']}> vs <@{m['p2_user_id']}>")
            lines.append("")
        if not lines:
            return
        body = "\n".join([heading, ""] + lines).rstrip()
        await channel.send(body[:1990], allowed_mentions=discord.AllowedMentions(users=True))

    async def send_champion(self, channel, t, champ, runner, names, final_score=None) -> None:
        champ_name = names.get(champ, "?")
        champ_ids = await self.entrant_member_ids(t, champ)
        champ_pings = " ".join(f"<@{u}>" for u in champ_ids)
        try:
            all_players = {
                p["user_id"]: p["display_name"] for p in await self.db.get_participants(t["id"])
            }
            roster = [all_players.get(u, "?") for u in champ_ids]
            file = discord.File(render_champion(t["name"], champ_name, roster), filename="champion.png")
        except Exception:  # noqa: BLE001
            log.exception("Champion banner render failed")
            file = None
        wins = "win" if t["mode"] == "team" else "wins"
        final_bit = f" ({final_score} in the final)" if final_score else ""
        msg = f"🏆 **{champ_name}** {wins} **{t['name']}**{final_bit}! {champ_pings}\n\n"
        if runner is not None:
            runner_word = "Runners-up" if t["mode"] == "team" else "Runner-up"
            runner_pings = " ".join(f"<@{u}>" for u in await self.entrant_member_ids(t, runner))
            msg += f"{runner_word}: **{names.get(runner, '?')}** {runner_pings}, great run.\n\n"
        msg += "GGs to everyone who played."
        await channel.send(msg, file=file, allowed_mentions=discord.AllowedMentions(users=True))

    async def resolve(self, interaction: discord.Interaction, tid: int | None):
        if tid is not None:
            return await self.db.get_tournament(tid)
        return await self.db.latest_active_tournament(interaction.guild_id)

    # -- commands: create + registration ---------------------------------------------------------

    @tournament.command(name="create", description="Create a new tournament")
    @app_commands.describe(
        name="Tournament name", game="Which game", size="Team size", format="Bracket style"
    )
    @app_commands.choices(
        game=[app_commands.Choice(name=g, value=g) for g in GAMES],
        size=[
            app_commands.Choice(name="Solo (1v1)", value="1"),
            app_commands.Choice(name="Duo (2v2)", value="2"),
            app_commands.Choice(name="Trio (3v3)", value="3"),
            app_commands.Choice(name="Random Trios (3v3, drawn at start)", value="s3"),
            app_commands.Choice(name="Random Duos (2v2, drawn at start)", value="s2"),
        ],
        format=[
            app_commands.Choice(name="Single elimination", value="single_elim"),
            app_commands.Choice(name="Round robin", value="round_robin"),
        ],
    )
    @staff_only()
    async def create(
        self, interaction: discord.Interaction, name: str, game: app_commands.Choice[str],
        size: app_commands.Choice[str] | None = None,
        format: app_commands.Choice[str] | None = None,
    ) -> None:
        raw = size.value if size else "1"
        if raw.startswith("s"):
            ts, mode_val = int(raw[1:]), "scramble"
        else:
            ts = int(raw)
            mode_val = "solo" if ts == 1 else "team"
        fmt = format.value if format else "single_elim"
        tid = await self.db.create_tournament(
            name, game.value, interaction.guild_id, interaction.channel_id, interaction.user.id,
            mode=mode_val, team_size=ts, fmt=fmt,
        )
        view = SoloRegView(self) if registers_individually({"mode": mode_val}) else TeamRegView(self)
        msg = await interaction.channel.send(embed=await self.registration_embed(tid), view=view)
        await self.db.set_tournament_message(tid, msg.id)
        await interaction.response.send_message(
            f"Created **{name}** (#{tid}). Registration is open.", ephemeral=True
        )

    @tournament.command(name="join", description="Join the active solo tournament")
    async def join(self, interaction: discord.Interaction) -> None:
        t = await self.db.latest_active_tournament(interaction.guild_id)
        if t is None or t["status"] != "open":
            await interaction.response.send_message(
                "No tournament is open for registration right now.", ephemeral=True
            )
            return
        if not registers_individually(t):
            await interaction.response.send_message(
                f"This is a {vs_label(t)} team tournament — use the **Register team** button on the "
                "tournament post, or `/tournament register`.", ephemeral=True
            )
            return
        ok = await self.db.add_participant(
            t["id"], interaction.user.id, interaction.user.display_name
        )
        await self.refresh_registration(t["id"])
        if t["mode"] == "scramble":
            reply = f"You're in **{t['name']}**! You'll be drawn into a team when it starts."
        else:
            reply = f"You're in **{t['name']}**!"
        await interaction.response.send_message(
            reply if ok else "You already joined.", ephemeral=True
        )

    @tournament.command(name="leave", description="Leave the active solo tournament")
    async def leave(self, interaction: discord.Interaction) -> None:
        t = await self.db.latest_active_tournament(interaction.guild_id)
        if t is None or t["status"] != "open":
            await interaction.response.send_message("No open tournament to leave.", ephemeral=True)
            return
        removed = await self.db.remove_participant(t["id"], interaction.user.id)
        await self.refresh_registration(t["id"])
        await interaction.response.send_message(
            "You left the tournament." if removed else "You weren't registered.", ephemeral=True
        )

    @tournament.command(name="register", description="Register your team (alternative to the button)")
    @app_commands.describe(
        team_name="Your team's name",
        player2="Teammate",
        player3="Third teammate (3v3 only)",
    )
    async def register(
        self, interaction: discord.Interaction, team_name: str,
        player2: discord.Member, player3: discord.Member | None = None,
    ) -> None:
        t = await self.db.latest_active_tournament(interaction.guild_id)
        if t is None or t["status"] != "open":
            await interaction.response.send_message(
                "No tournament is open for registration.", ephemeral=True
            )
            return
        if t["mode"] != "team":
            await interaction.response.send_message(
                "This is a solo tournament. Use `/tournament join` instead.", ephemeral=True
            )
            return
        need = team_size_of(t)
        mates = [player2] + ([player3] if player3 else [])
        if len(mates) != need - 1:
            extra = "" if need == 3 else " (this is a 2v2 — name just one teammate)"
            await interaction.response.send_message(
                f"A {need}v{need} team needs {need - 1} teammate(s){extra}.", ephemeral=True
            )
            return
        ok, msg = await self.register_team(t, interaction.user, mates, team_name[:28])
        await interaction.response.send_message(msg, ephemeral=True)

    @tournament.command(name="unregister", description="Withdraw your team (captain or staff)")
    async def unregister(self, interaction: discord.Interaction) -> None:
        t = await self.db.latest_active_tournament(interaction.guild_id)
        if t is None or t["status"] != "open" or t["mode"] != "team":
            await interaction.response.send_message(
                "No open team tournament to withdraw from.", ephemeral=True
            )
            return
        msg = await self.withdraw_team(t, interaction.user, staff=is_staff(interaction))
        await interaction.response.send_message(msg, ephemeral=True)

    # -- commands: run ---------------------------------------------------------

    @tournament.command(name="start", description="Lock registration and generate the bracket")
    @app_commands.describe(tournament_id="Which tournament (defaults to the active one)")
    @staff_only()
    async def start(self, interaction: discord.Interaction, tournament_id: int | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        t = await self.resolve(interaction, tournament_id)
        if t is None:
            await interaction.followup.send("No tournament found.", ephemeral=True)
            return
        if t["status"] != "open":
            await interaction.followup.send(f"**{t['name']}** is already {t['status']}.", ephemeral=True)
            return

        # scramble: draw the solo sign-ups into random teams, then play as a team event
        was_scramble = t["mode"] == "scramble"
        if was_scramble:
            ok, err = await self._form_scramble_teams(t)
            if not ok:
                await interaction.followup.send(err, ephemeral=True)
                return
            t = await self.db.get_tournament(t["id"])  # mode is now 'team'

        if t["mode"] == "team":
            entrants = await self.db.get_teams(t["id"])
            noun = "teams"
        else:
            entrants = await self.db.get_participants(t["id"])
            noun = "players"
        if len(entrants) < 2:
            await interaction.followup.send(f"Need at least 2 {noun} to start.", ephemeral=True)
            return

        if was_scramble:
            await interaction.channel.send(
                await self._teams_drawn_text(t),
                allowed_mentions=discord.AllowedMentions(users=True),
            )

        seeded = list(entrants)
        random.shuffle(seeded)
        if t["mode"] == "team":
            for seed, e in enumerate(seeded, 1):
                await self.db.set_team_seed(t["id"], e["id"], seed)
            ids = [e["id"] for e in seeded]
        else:
            for seed, e in enumerate(seeded, 1):
                await self.db.set_participant_seed(t["id"], e["user_id"], seed)
            ids = [e["user_id"] for e in seeded]

        if is_round_robin(t):
            n, rounds, matches = build_round_robin(ids)
            await self.db.set_tournament_bracket(t["id"], n, rounds)
            for m in matches:
                await self.db.insert_match(
                    t["id"], m.match_no, m.round, m.pos, m.p1, m.p2, "ready", None, None
                )
            total = len(matches)
            t = await self.db.get_tournament(t["id"])
            await interaction.channel.send(
                content=f"**{t['name']}** is live — {len(entrants)} {noun}, round robin, "
                f"{total} matches. Everyone plays everyone. Standings below 👇",
                **await self.standings_message(t),
            )
        else:
            size, rounds, matches = build_bracket(ids)
            await self.db.set_tournament_bracket(t["id"], size, rounds)
            for m in matches:
                both = m.p1 is not None and m.p2 is not None
                status = "ready" if (m.round == 1 and both) else "pending"
                await self.db.insert_match(
                    t["id"], m.match_no, m.round, m.pos, m.p1, m.p2, status, m.next_match_no, m.next_slot
                )
            for m in matches:  # auto-advance round-1 byes
                if m.round == 1 and (m.p1 is None) != (m.p2 is None):
                    winner = m.p1 if m.p1 is not None else m.p2
                    await self.db.resolve_match(t["id"], m.match_no, winner)
                    if m.next_match_no is not None:
                        await self.db.fill_match_slot(t["id"], m.next_match_no, m.next_slot, winner)
            t = await self.db.get_tournament(t["id"])
            await interaction.channel.send(
                content=f"**{t['name']}** is underway with {len(entrants)} {noun}. Bracket below 👇",
                **await self.bracket_message(t),
            )

        ready_rows = [m for m in await self.db.get_matches(t["id"]) if m["status"] == "ready"]
        await self.announce_matchups(
            interaction.channel, t, [m["match_no"] for m in ready_rows],
            "**Opening matchups.** Play your sets and staff will report the results.",
        )
        await interaction.followup.send("Started.", ephemeral=True)

    async def _form_scramble_teams(self, t) -> tuple[bool, str | None]:
        """Draw solo sign-ups into random teams, then flip the tournament to
        team mode. Returns (ok, error message)."""
        ts = team_size_of(t)
        people = await self.db.get_participants(t["id"])
        if len(people) < 2 * ts:
            return False, (
                f"Need at least {2 * ts} players to draw random {ts}v{ts} teams "
                f"({len(people)} signed up)."
            )
        people = list(people)
        random.shuffle(people)
        num_teams = len(people) // ts  # every team ends up with ts or ts+1 players
        groups: list[list] = [[] for _ in range(num_teams)]
        for i, p in enumerate(people):
            groups[i % num_teams].append(p)
        for idx, group in enumerate(groups, 1):
            team_id = await self.db.create_team(t["id"], f"Team {idx}", group[0]["user_id"])
            for j, p in enumerate(group):
                await self.db.assign_participant_team(
                    t["id"], p["user_id"], team_id, 1 if j == 0 else 0
                )
        await self.db.set_tournament_mode(t["id"], "team")
        return True, None

    async def _teams_drawn_text(self, t) -> str:
        teams = await self.db.get_teams(t["id"])
        lines = ["🎲 **The teams have been drawn:**", ""]
        for tm in teams:
            members = await self.db.team_members(t["id"], tm["id"])
            pings = " ".join(f"<@{m['user_id']}>" for m in members)
            lines.append(f"**{tm['name']}** — {pings}")
        return "\n".join(lines)[:1990]

    @tournament.command(name="report", description="Report a match result")
    @app_commands.describe(
        match="Match number (the #N shown next to the match)",
        winner="The winner (in team modes, pick any member of the winning team)",
        score="Optional score, e.g. 2-1",
    )
    @staff_only()
    async def report(
        self, interaction: discord.Interaction, match: int, winner: discord.Member,
        score: str | None = None, tournament_id: int | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        t = await self.resolve(interaction, tournament_id)
        if t is None or t["status"] != "running":
            await interaction.followup.send("No running tournament found.", ephemeral=True)
            return
        m = await self.db.get_match(t["id"], match)
        if m is None:
            await interaction.followup.send(f"No match #{match}.", ephemeral=True)
            return
        if m["status"] == "done":
            await interaction.followup.send(f"Match #{match} is already decided.", ephemeral=True)
            return
        if m["p1_user_id"] is None or m["p2_user_id"] is None:
            await interaction.followup.send(
                f"Match #{match} isn't ready yet (waiting on an earlier round).", ephemeral=True
            )
            return

        if t["mode"] == "team":
            win_entrant = await self.db.team_of_user(t["id"], winner.id)
            if win_entrant is None:
                await interaction.followup.send(
                    f"{winner.display_name} isn't on a team in this tournament.", ephemeral=True
                )
                return
        else:
            win_entrant = winner.id
        if win_entrant not in (m["p1_user_id"], m["p2_user_id"]):
            await interaction.followup.send(
                f"{winner.display_name} isn't in match #{match}.", ephemeral=True
            )
            return
        lose_entrant = m["p2_user_id"] if win_entrant == m["p1_user_id"] else m["p1_user_id"]
        score = score.strip()[:9] if score else None
        await self.db.resolve_match(t["id"], match, win_entrant, score)

        if is_round_robin(t):
            await self._finish_report_rr(interaction, t, m, match, win_entrant, lose_entrant, score)
        else:
            await self._finish_report_elim(interaction, t, m, match, win_entrant, lose_entrant, score)
        await interaction.followup.send(f"Recorded match #{match}.", ephemeral=True)

    async def _finish_report_elim(self, interaction, t, m, match, win_entrant, lose_entrant, score):
        promoted: list[int] = []
        if m["next_match_no"] is not None:
            await self.db.fill_match_slot(t["id"], m["next_match_no"], m["next_slot"], win_entrant)
            nxt = await self.db.get_match(t["id"], m["next_match_no"])
            if nxt is not None and nxt["status"] == "ready":
                promoted.append(nxt["match_no"])
            finished = False
        else:
            await self.db.set_tournament_winner(t["id"], win_entrant)
            finished = True

        t = await self.db.get_tournament(t["id"])
        names = await self.names_map(t)
        rounds = t["rounds"] or 1
        if finished:
            await self.send_champion(interaction.channel, t, win_entrant, lose_entrant, names, score)
        else:
            await self._result_line(interaction.channel, t, match, m["round"], win_entrant, lose_entrant, score, names)
        await interaction.channel.send(**await self.bracket_message(t))
        if promoted:
            await self.announce_matchups(interaction.channel, t, promoted, "**Up next**")

    async def _finish_report_rr(self, interaction, t, m, match, win_entrant, lose_entrant, score):
        matches = [dict(x) for x in await self.db.get_matches(t["id"])]
        all_done = all(x["status"] == "done" for x in matches)
        names = await self.names_map(t)

        if all_done:
            standings = round_robin_standings(list(names.keys()), matches)
            champ = standings[0]["entrant"]
            runner = standings[1]["entrant"] if len(standings) > 1 else None
            await self.db.set_tournament_winner(t["id"], champ)
            t = await self.db.get_tournament(t["id"])
            await self._result_line(interaction.channel, t, match, m["round"], win_entrant, lose_entrant, score, names)
            await self.send_champion(interaction.channel, t, champ, runner, names)
        else:
            await self._result_line(interaction.channel, t, match, m["round"], win_entrant, lose_entrant, score, names)
        await interaction.channel.send(**await self.standings_message(t))

    async def _result_line(self, channel, t, match, rnd, win_entrant, lose_entrant, score, names):
        stage = round_label(rnd, t["rounds"] or 1).title() if not is_round_robin(t) else f"Round {rnd}"
        win_name = names.get(win_entrant, "?")
        lose_name = names.get(lose_entrant, "?")
        win_pings = " ".join(f"<@{u}>" for u in await self.entrant_member_ids(t, win_entrant))
        lose_pings = " ".join(f"<@{u}>" for u in await self.entrant_member_ids(t, lose_entrant))
        score_txt = f" {score}" if score else ""
        await channel.send(
            f"**Match #{match} · {stage}**\n"
            f"**{win_name}** {result_verb(score)} **{lose_name}**{score_txt}\n\n"
            f"GG {win_pings} {lose_pings}",
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @tournament.command(name="bracket", description="Show the current bracket or standings")
    async def bracket(self, interaction: discord.Interaction, tournament_id: int | None = None) -> None:
        t = await self.resolve(interaction, tournament_id)
        if t is None:
            await interaction.response.send_message("No tournament found.", ephemeral=True)
            return
        if t["status"] == "open":
            await interaction.response.send_message(
                embed=await self.registration_embed(t["id"]), ephemeral=True
            )
            return
        await interaction.response.send_message(ephemeral=True, **await self.display_message(t))

    @tournament.command(name="list", description="List recent tournaments")
    async def list_cmd(self, interaction: discord.Interaction) -> None:
        rows = await self.db.list_tournaments(interaction.guild_id)
        if not rows:
            await interaction.response.send_message("No tournaments yet.", ephemeral=True)
            return
        icon = {"open": "🟢", "running": "⚔️", "finished": "🏁", "cancelled": "✖️"}
        lines = []
        for r in rows:
            mode_icon = "👥" if r["mode"] == "team" else "👤"
            fmt_icon = "🔁" if is_round_robin(r) else "🪜"
            line = f"`#{r['id']}` {icon.get(r['status'], '')}{mode_icon}{fmt_icon} **{r['name']}** ({r['game']})"
            if r["status"] == "finished" and r["winner_user_id"]:
                names = await self.names_map(r)
                line += f" — 🏆 {names.get(r['winner_user_id'], '?')}"
            else:
                line += f" — {r['status']}"
            lines.append(line)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Tournaments", description="\n".join(lines), color=discord.Color.blurple()
            ),
            ephemeral=True,
        )

    @tournament.command(name="cancel", description="Cancel a tournament")
    @staff_only()
    async def cancel(self, interaction: discord.Interaction, tournament_id: int | None = None) -> None:
        t = await self.resolve(interaction, tournament_id)
        if t is None:
            await interaction.response.send_message("No tournament found.", ephemeral=True)
            return
        await self.db.set_tournament_status(t["id"], "cancelled")
        await self.refresh_registration(t["id"])
        await interaction.response.send_message(
            f"Cancelled **{t['name']}** (#{t['id']}).", ephemeral=True
        )

    # -- commands: announce ---------------------------------------------------------

    @tournament.command(name="announce", description="Post a tournament announcement with a sign-up button")
    @app_commands.describe(
        title="Announcement title",
        host="Who is hosting",
        schedule="When it runs, e.g. Sunday 24th, 8:00 PM IST",
        size="Team size",
        best_of="Match format",
        sponsor="Prize / sponsor, e.g. Brawl Pass gifted by @Executor",
        min_players="Minimum players needed to run it",
        coordinate_in="Channel where brackets and match talk happen",
        notes="Any extra details",
        ping_everyone="Ping @everyone with the announcement",
    )
    @app_commands.choices(
        size=[
            app_commands.Choice(name="1v1", value="1"),
            app_commands.Choice(name="2v2", value="2"),
            app_commands.Choice(name="3v3", value="3"),
        ],
        best_of=[
            app_commands.Choice(name="Best of 1", value="Best of 1"),
            app_commands.Choice(name="Best of 3", value="Best of 3"),
            app_commands.Choice(name="Best of 5", value="Best of 5"),
        ],
    )
    @staff_only()
    async def announce(
        self, interaction: discord.Interaction,
        title: str, host: discord.Member, schedule: str, size: app_commands.Choice[str],
        best_of: app_commands.Choice[str] | None = None,
        sponsor: str | None = None,
        min_players: int | None = None,
        coordinate_in: discord.TextChannel | None = None,
        notes: str | None = None,
        ping_everyone: bool = False,
    ) -> None:
        ts = int(size.value)
        bo = best_of.value if best_of else "Best of 3"
        goal = min_players or 0

        embed = discord.Embed(
            title=f"🏆 {title}",
            description=f"Hosted by {host.mention}\n🗓️ **{schedule}**",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Format", value=f"{ts}v{ts} · {bo}", inline=True)
        if ts == 1:
            entry = "Every player for themselves."
        else:
            entry = "Solo or as a full squad, both are fine. No team? Sign up and find one."
        embed.add_field(name="Entry", value=entry, inline=True)
        if sponsor:
            embed.add_field(name="🎁 Prize", value=sponsor, inline=False)
        role = interaction.guild.get_role(PARTICIPANT_ROLE_ID)
        current = len(role.members) if role else 0
        signup_val = f"**{current}**" + (
            f" / {min_players} needed{' ✅' if current >= min_players else ''}"
            if min_players else " so far"
        )
        embed.add_field(name="Signed up", value=signup_val, inline=False)
        if coordinate_in:
            embed.add_field(name="Coordinate in", value=coordinate_in.mention, inline=False)
        if notes:
            embed.add_field(name="Details", value=notes, inline=False)
        embed.set_footer(text="Hit Join tournament to sign up. Press it again to withdraw.")

        view = discord.ui.View(timeout=None)
        view.add_item(ParticipantButton(PARTICIPANT_ROLE_ID, goal))

        content = None
        if ping_everyone:
            content = "@everyone"
        await interaction.channel.send(
            content=content, embed=embed, view=view,
            allowed_mentions=discord.AllowedMentions(everyone=ping_everyone),
        )
        await interaction.response.send_message("Announcement posted.", ephemeral=True)

    @tournament.command(
        name="schedule",
        description="Create a Discord event (shows local time + sends reminders)",
    )
    @app_commands.describe(
        date="Date, YYYY-MM-DD",
        time="Start time, 24h HH:MM",
        title="Event name (defaults to the active tournament)",
        duration_hours="How long to block out (default 2)",
        location="Where it happens (default: this channel)",
        description="Extra details shown on the event",
        timezone="IANA timezone (default Asia/Kolkata)",
        ping_participants="Ping the participant role with the schedule",
    )
    @staff_only()
    async def schedule(
        self, interaction: discord.Interaction, date: str, time: str,
        title: str | None = None, duration_hours: int = 2, location: str | None = None,
        description: str | None = None, timezone: str | None = None,
        ping_participants: bool = False,
    ) -> None:
        if not interaction.guild.me.guild_permissions.manage_events:
            await interaction.response.send_message(
                "I need the **Manage Events** permission to create a scheduled event. "
                "Staff: enable it for my role in Server Settings → Roles.", ephemeral=True
            )
            return
        tzname = timezone or config.timezone
        try:
            tz = ZoneInfo(tzname)
        except Exception:  # noqa: BLE001
            await interaction.response.send_message(
                f"Unknown timezone `{tzname}`. Use an IANA name like `Asia/Kolkata`.", ephemeral=True
            )
            return
        try:
            start = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except ValueError:
            await interaction.response.send_message(
                "Couldn't read that. Date must be `YYYY-MM-DD` and time `HH:MM` (24h), "
                "e.g. `2026-07-12` and `20:00`.", ephemeral=True
            )
            return
        if start <= datetime.now(tz):
            await interaction.response.send_message(
                "That time is in the past. Pick a future date and time.", ephemeral=True
            )
            return

        if not title:
            active = await self.db.latest_active_tournament(interaction.guild_id)
            title = active["name"] if active else None
        if not title:
            await interaction.response.send_message(
                "No active tournament to name the event after — pass a `title`.", ephemeral=True
            )
            return

        duration_hours = max(1, min(duration_hours, 24))
        end = start + timedelta(hours=duration_hours)
        loc = (location or f"#{interaction.channel.name}")[:100]

        event_kwargs = {}
        if EVENT_BANNER.exists():
            try:
                event_kwargs["image"] = EVENT_BANNER.read_bytes()
            except OSError:
                pass

        await interaction.response.defer(ephemeral=True)
        try:
            event = await interaction.guild.create_scheduled_event(
                name=title[:100],
                start_time=start,
                end_time=end,
                entity_type=discord.EntityType.external,
                location=loc,
                privacy_level=discord.PrivacyLevel.guild_only,
                description=(description or f"{title} — running in {loc}.")[:1000],
                **event_kwargs,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Discord blocked that — check I have **Manage Events**.", ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"Discord rejected the event: {e}", ephemeral=True)
            return

        unix = int(start.timestamp())
        content = f"🗓️ **{title}** is scheduled.\n\n"
        if ping_participants:
            content += f"<@&{PARTICIPANT_ROLE_ID}>\n"
        content += (
            f"**When:** <t:{unix}:F> (<t:{unix}:R>)\n"
            f"**Where:** {loc}\n\n"
            f"Tap **Interested** on the event to get a reminder:\n{event.url}"
        )
        await interaction.channel.send(
            content,
            allowed_mentions=discord.AllowedMentions(roles=ping_participants),
        )
        await interaction.followup.send("Event created and posted.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tournaments(bot))
