"""Tournaments for #tournament-hub: solo (1v1) and 3v3 team single elimination.

UI: rendered bracket images (bracket_render.py), interactive registration
(buttons; team mode uses a modal + user-picker), match-ready announcement
cards, score reporting, and a champion banner.

The bracket runs over "entrant ids": a user id in solo mode, a team id in
team mode. matches.p1_user_id/p2_user_id/winner_user_id hold entrant ids.
"""
from __future__ import annotations

import logging
import random

import discord
from discord import app_commands
from discord.ext import commands

from ..bracket_render import MAX_BRACKET_SIZE, render_bracket, render_champion, round_label
from ..checks import is_staff, staff_only
from ..tournament import build_bracket

log = logging.getLogger(__name__)

GAMES = ["Brawl Stars", "Clash Royale", "Other"]
TEAM_SIZE = 3  # Brawl Stars is 3v3

JOIN_ID = "tpcd_tourney_join"
LEAVE_ID = "tpcd_tourney_leave"
TEAM_REG_ID = "tpcd_team_register"
TEAM_WITHDRAW_ID = "tpcd_team_withdraw"

WIN_LINES = [
    "Absolutely cooked. 🔥",
    "Built different.",
    "A masterclass, honestly.",
    "The bracket trembles.",
    "Someone check if that was even fair.",
    "Certified demon hours.",
    "Clean. Surgical. Ruthless.",
]
LOSE_LINES = [
    "You made them earn every bit of it.",
    "Heads high — that was a proper scrap.",
    "Revenge arc loading…",
    "GGs only. Run it back next season.",
    "Down, but never out.",
    "The bracket was rigged (probably).",
]


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
        if t["mode"] == "team":
            await interaction.response.send_message(
                "This is a 3v3 team tournament — use the **Register team** button.", ephemeral=True
            )
            return
        ok = await self.cog.db.add_participant(
            t["id"], interaction.user.id, interaction.user.display_name
        )
        if ok:
            await self.cog.refresh_registration(t["id"])
            await interaction.response.send_message(
                f"You're in **{t['name']}**. Good luck! ⚔️", ephemeral=True
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
    """Persistent Register/Withdraw buttons for 3v3 tournaments."""

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
        await interaction.response.send_modal(TeamNameModal(self.cog, t["id"]))

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

    def __init__(self, cog: "Tournaments", tid: int) -> None:
        super().__init__(title=f"Register your {TEAM_SIZE}v{TEAM_SIZE} team")
        self.cog = cog
        self.tid = tid

    async def on_submit(self, interaction: discord.Interaction) -> None:
        view = TeammatePickView(self.cog, self.tid, str(self.team_name), interaction.user)
        await interaction.response.send_message(
            f"**{self.team_name}** — now pick your {TEAM_SIZE - 1} teammates:",
            view=view,
            ephemeral=True,
        )


class TeammatePickView(discord.ui.View):
    """Ephemeral follow-up to the name modal: pick 2 teammates, confirm."""

    def __init__(self, cog: "Tournaments", tid: int, team_name: str, captain: discord.Member) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.tid = tid
        self.team_name = team_name
        self.captain = captain
        self.mates: list[discord.Member] = []

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Pick your 2 teammates",
        min_values=TEAM_SIZE - 1,
        max_values=TEAM_SIZE - 1,
    )
    async def pick(self, interaction: discord.Interaction, select: discord.ui.UserSelect) -> None:
        self.mates = list(select.values)
        await interaction.response.defer()

    @discord.ui.button(label="Confirm team", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if len(self.mates) != TEAM_SIZE - 1:
            await interaction.response.send_message(
                "Pick your teammates first.", ephemeral=True
            )
            return
        t = await self.cog.db.get_tournament(self.tid)
        if t is None or t["status"] != "open":
            await interaction.response.edit_message(
                content="Registration closed while you were picking. 😬", view=None
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


class Tournaments(commands.Cog):
    tournament = app_commands.Group(name="tournament", description="Run inter-club tournaments")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db

    async def cog_load(self) -> None:
        # persistent views: one instance of each handles every open tournament
        self.bot.add_view(SoloRegView(self))
        self.bot.add_view(TeamRegView(self))

    # -- mode-aware helpers ---------------------------------------------------------

    async def names_map(self, t) -> dict[int, str]:
        """entrant id -> display name (team name in 3v3, player name in solo)."""
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
        """All user ids behind an entrant, for pinging."""
        if t["mode"] == "team":
            return await self.db.team_member_ids(t["id"], entrant_id)
        return [entrant_id]

    async def entrant_ping(self, t, entrant_id: int, names: dict[int, str]) -> str:
        ids = await self.entrant_member_ids(t, entrant_id)
        pings = " ".join(f"<@{u}>" for u in ids)
        if t["mode"] == "team":
            return f"**{names.get(entrant_id, '?')}** ({pings})"
        return pings

    async def register_team(
        self, t, captain: discord.Member, mates: list[discord.Member], team_name: str
    ) -> tuple[bool, str]:
        """Shared by the slash command and the button flow."""
        members = [captain, *mates]
        if any(m.bot for m in members):
            return False, "Teams can't include bots."
        if len({m.id for m in members}) != TEAM_SIZE:
            return False, f"A team needs {TEAM_SIZE} different players (you're the captain)."
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

    # -- embeds + images ---------------------------------------------------------

    async def registration_embed(self, tid: int) -> discord.Embed:
        t = await self.db.get_tournament(tid)
        if t["mode"] == "team":
            fmt = f"⚔️ Single elimination · 👥 **{TEAM_SIZE}v{TEAM_SIZE} teams**"
            how = "Captains: hit **Register team**, name your squad, pick your 2 teammates."
        else:
            fmt = "⚔️ Single elimination · 👤 **solo (1v1)**"
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

    def text_bracket_embed(self, t, matches, names) -> discord.Embed:
        """Fallback if image rendering fails or the bracket is huge."""
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
        """kwargs for channel.send / interaction response: image + slim embed."""
        names = await self.names_map(t)
        matches_rows = await self.db.get_matches(t["id"])
        matches = [dict(m) for m in matches_rows]
        rounds = t["rounds"] or 1

        done = sum(1 for m in matches if m["status"] == "done")
        ready = [m for m in matches if m["status"] == "ready"]
        cur_rounds = [m["round"] for m in matches if m["status"] != "done"]
        entrant_word = "teams" if t["mode"] == "team" else "players"

        embed = discord.Embed(color=discord.Color.gold())
        if t["status"] == "finished":
            embed.description = (
                f"🏁 **{t['name']}** is complete — "
                f"**{names.get(t['winner_user_id'], '?')}** takes the crown!"
            )
        else:
            stage = round_label(min(cur_rounds), rounds).title() if cur_rounds else "…"
            embed.description = (
                f"⚔️ **{stage}** · {done}/{len(matches)} matches played"
            )
            if ready:
                nums = ", ".join(f"`#{m['match_no']}`" for m in ready)
                embed.add_field(name="Awaiting results", value=nums, inline=False)
            embed.set_footer(text="Staff report results with /tournament report")

        if (t["bracket_size"] or 0) <= MAX_BRACKET_SIZE:
            try:
                seeds = await self.seeds_map(t)
                sub = f"{t['game']} · {t['mode']} · {t['bracket_size']} slots · {entrant_word}"
                buf = render_bracket(
                    t["name"], sub, rounds, t["bracket_size"], matches, names, seeds,
                    champion=t["winner_user_id"] if t["status"] == "finished" else None,
                )
                file = discord.File(buf, filename="bracket.png")
                embed.set_image(url="attachment://bracket.png")
                return {"embed": embed, "files": [file]}
            except Exception:  # noqa: BLE001 — degrade to text bracket
                log.exception("Bracket render failed; falling back to text")
        return {"embed": self.text_bracket_embed(t, matches, names)}

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

    async def announce_ready(self, channel, t, match_nos: list[int], heading: str) -> None:
        """Ping the players of newly-ready matches so they know they're up."""
        if not match_nos:
            return
        names = await self.names_map(t)
        rounds = t["rounds"] or 1
        lines = [heading]
        for no in match_nos:
            m = await self.db.get_match(t["id"], no)
            if m is None or m["p1_user_id"] is None or m["p2_user_id"] is None:
                continue
            p1 = await self.entrant_ping(t, m["p1_user_id"], names)
            p2 = await self.entrant_ping(t, m["p2_user_id"], names)
            lines.append(
                f"⚔️ `#{m['match_no']}` · {round_label(m['round'], rounds).title()} — {p1} vs {p2}"
            )
        lines.append("*Play your set, then staff reports with `/tournament report`.*")
        await channel.send(
            "\n".join(lines), allowed_mentions=discord.AllowedMentions(users=True)
        )

    async def resolve(self, interaction: discord.Interaction, tid: int | None):
        if tid is not None:
            return await self.db.get_tournament(tid)
        return await self.db.latest_active_tournament(interaction.guild_id)

    # -- commands ---------------------------------------------------------

    @tournament.command(name="create", description="Create a new tournament")
    @app_commands.describe(name="Tournament name", game="Which game", mode="Solo 1v1 or 3v3 teams")
    @app_commands.choices(
        game=[app_commands.Choice(name=g, value=g) for g in GAMES],
        mode=[
            app_commands.Choice(name="Solo (1v1)", value="solo"),
            app_commands.Choice(name="Team (3v3)", value="team"),
        ],
    )
    @staff_only()
    async def create(
        self, interaction: discord.Interaction, name: str, game: app_commands.Choice[str],
        mode: app_commands.Choice[str] | None = None,
    ) -> None:
        mode_val = mode.value if mode else "solo"
        team_size = TEAM_SIZE if mode_val == "team" else 1
        tid = await self.db.create_tournament(
            name, game.value, interaction.guild_id, interaction.channel_id, interaction.user.id,
            mode=mode_val, team_size=team_size,
        )
        view = SoloRegView(self) if mode_val == "solo" else TeamRegView(self)
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
        if t["mode"] == "team":
            await interaction.response.send_message(
                "This is a 3v3 team tournament — use the **Register team** button on the "
                "tournament post, or `/tournament register`.", ephemeral=True
            )
            return
        ok = await self.db.add_participant(
            t["id"], interaction.user.id, interaction.user.display_name
        )
        await self.refresh_registration(t["id"])
        await interaction.response.send_message(
            f"You're in **{t['name']}**! ⚔️" if ok else "You already joined.", ephemeral=True
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

    @tournament.command(name="register", description="Register your team of 3 (alternative to the button)")
    @app_commands.describe(
        team_name="Your team's name", player2="Second team member", player3="Third team member"
    )
    async def register(
        self, interaction: discord.Interaction, team_name: str,
        player2: discord.Member, player3: discord.Member,
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
        ok, msg = await self.register_team(t, interaction.user, [player2, player3], team_name[:28])
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
            await interaction.followup.send(
                f"**{t['name']}** is already {t['status']}.", ephemeral=True
            )
            return

        # entrants are teams (3v3) or individual players (solo)
        if t["mode"] == "team":
            entrants = await self.db.get_teams(t["id"])
            noun = "teams"
        else:
            entrants = await self.db.get_participants(t["id"])
            noun = "players"
        if len(entrants) < 2:
            await interaction.followup.send(f"Need at least 2 {noun} to start.", ephemeral=True)
            return

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
        kwargs = await self.bracket_message(t)
        await interaction.channel.send(
            content=f"🏆 **{t['name']}** has started — {len(entrants)} {noun}, single elimination. "
            "May the best win!",
            **kwargs,
        )
        ready_rows = [m for m in await self.db.get_matches(t["id"]) if m["status"] == "ready"]
        await self.announce_ready(
            interaction.channel, t, [m["match_no"] for m in ready_rows],
            "**First matchups — you're up:**",
        )
        await interaction.followup.send("Bracket posted.", ephemeral=True)

    @tournament.command(name="report", description="Report a match result")
    @app_commands.describe(
        match="Match number (the #N in the bracket)",
        winner="The winner (in 3v3, pick any member of the winning team)",
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

        # result card: congrats + respect, everyone pinged
        win_label = await self.entrant_ping(t, win_entrant, names)
        lose_label = await self.entrant_ping(t, lose_entrant, names)
        score_txt = f" `{score}`" if score else ""
        await interaction.channel.send(
            f"⚔️ **Match #{match} · {round_label(m['round'], rounds).title()}**{score_txt}\n"
            f"🎉 {win_label} — {random.choice(WIN_LINES)}\n"
            f"🫡 {lose_label} — {random.choice(LOSE_LINES)}",
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        if finished:
            member_ids = await self.entrant_member_ids(t, win_entrant)
            all_players = {
                p["user_id"]: p["display_name"] for p in await self.db.get_participants(t["id"])
            }
            roster = [all_players.get(u, "?") for u in member_ids]
            champ_ping = " ".join(f"<@{u}>" for u in member_ids)
            try:
                buf = render_champion(t["name"], names.get(win_entrant, "?"), roster)
                file = discord.File(buf, filename="champion.png")
                await interaction.channel.send(
                    f"🏆 **{names.get(win_entrant, '?')}** wins **{t['name']}**! {champ_ping}\n"
                    "GGs to everyone who entered.",
                    file=file,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except Exception:  # noqa: BLE001
                log.exception("Champion banner render failed")
                await interaction.channel.send(
                    f"🏆🏆 **{names.get(win_entrant, '?')}** wins **{t['name']}**! {champ_ping}",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )

        kwargs = await self.bracket_message(t)
        await interaction.channel.send(**kwargs)
        if promoted:
            await self.announce_ready(interaction.channel, t, promoted, "**Up next:**")
        await interaction.followup.send(f"Recorded match #{match}.", ephemeral=True)

    @tournament.command(name="bracket", description="Show the current bracket")
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
        kwargs = await self.bracket_message(t)
        await interaction.response.send_message(ephemeral=True, **kwargs)

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
            line = f"`#{r['id']}` {icon.get(r['status'], '')}{mode_icon} **{r['name']}** ({r['game']})"
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tournaments(bot))
