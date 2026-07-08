"""Slash commands for running the poll system: /poll post, preview, skip,
add, bank, history, stats. Gated to leadership roles + owner.
"""
from __future__ import annotations

import json
import logging
import uuid

import discord
from discord import app_commands
from discord.ext import commands

from ..config import config
from .daily_polls import DailyPolls

log = logging.getLogger(__name__)

CATEGORIES = [
    "brawl_stars",
    "gaming_general",
    "hot_takes",
    "this_or_that",
    "food",
    "hypotheticals",
    "sports_pop_culture",
]


def is_poll_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.id == config.owner_id:
        return True
    if isinstance(interaction.user, discord.Member):
        if interaction.user.guild_permissions.manage_guild:
            return True
        member_roles = {role.id for role in interaction.user.roles}
        if member_roles.intersection(config.admin_role_ids):
            return True
    return False


def admin_check() -> app_commands.check:
    async def predicate(interaction: discord.Interaction) -> bool:
        if not is_poll_admin(interaction):
            await interaction.response.send_message(
                "This command is for Captain / Commander / Manager roles.", ephemeral=True
            )
            return False
        return True

    return app_commands.check(predicate)


class PreviewView(discord.ui.View):
    """Ephemeral preview with a reroll button."""

    def __init__(self, cog: DailyPolls) -> None:
        super().__init__(timeout=120)
        self.cog = cog

    @discord.ui.button(label="🎲 Reroll", style=discord.ButtonStyle.secondary)
    async def reroll(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        question = await self.cog.next_question(reroll=True)
        if question is None:
            await interaction.response.edit_message(content="Question bank is empty!", embed=None, view=None)
            return
        await interaction.response.edit_message(embed=preview_embed(question), view=self)


def preview_embed(question) -> discord.Embed:
    options = json.loads(question["options_json"])
    embed = discord.Embed(
        title="Next queued poll",
        description=f"**{question['question']}**\n\n" + "\n".join(f"➤ {o}" for o in options),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"category: {question['category']} · style: {question['style']}")
    return embed


class PollAdmin(commands.Cog):
    poll = app_commands.Group(name="poll", description="TPCD daily poll controls")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db

    @property
    def engine(self) -> DailyPolls:
        return self.bot.get_cog("DailyPolls")

    # -- posting -------------------------------------------------------

    @poll.command(name="post", description="Post the daily poll right now")
    @app_commands.describe(force="Post even if the bot already posted today")
    @admin_check()
    async def post(self, interaction: discord.Interaction, force: bool = False) -> None:
        await interaction.response.defer(ephemeral=True)
        status = await self.engine.post_daily_poll(force=force)
        await interaction.followup.send(status, ephemeral=True)

    @poll.command(name="preview", description="Peek at the next queued question (with reroll)")
    @admin_check()
    async def preview(self, interaction: discord.Interaction) -> None:
        question = await self.engine.next_question()
        if question is None:
            await interaction.response.send_message("Question bank is empty!", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=preview_embed(question), view=PreviewView(self.engine), ephemeral=True
        )

    @poll.command(name="skip", description="Skip the next queued question and pick another")
    @admin_check()
    async def skip(self, interaction: discord.Interaction) -> None:
        question = await self.engine.next_question()
        if question is None:
            await interaction.response.send_message("Question bank is empty!", ephemeral=True)
            return
        await self.db.mark_skipped(question["id"])
        replacement = await self.engine.next_question(reroll=True)
        if replacement is None:
            await interaction.response.send_message(
                f"Skipped **{question['question']}** — but the bank is now empty!", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Skipped **{question['question']}**.\nNext up: **{replacement['question']}**",
            ephemeral=True,
        )

    # -- bank management -------------------------------------------------------

    @poll.command(name="add", description="Add a question to the bank")
    @app_commands.describe(
        question="The poll question",
        options="2-10 options separated by | (pipe)",
        category="Question category",
    )
    @app_commands.choices(
        category=[app_commands.Choice(name=c.replace("_", " "), value=c) for c in CATEGORIES]
    )
    @admin_check()
    async def add(
        self,
        interaction: discord.Interaction,
        question: str,
        options: str,
        category: app_commands.Choice[str],
    ) -> None:
        opts = [o.strip() for o in options.split("|") if o.strip()]
        if not 2 <= len(opts) <= 10:
            await interaction.response.send_message(
                f"Polls need 2-10 options; you gave {len(opts)}. Separate them with |.",
                ephemeral=True,
            )
            return
        if await self.db.question_exists(question):
            await interaction.response.send_message(
                "That question is already in the bank (rule 7: no repeats).", ephemeral=True
            )
            return
        qid = f"manual_{uuid.uuid4().hex[:8]}"
        await self.db.add_question(qid, category.value, question, opts, interaction.user.id)
        await interaction.response.send_message(
            f"Added to **{category.value}**: **{question}** ({len(opts)} options)", ephemeral=True
        )

    @poll.command(name="bank", description="Question pool status")
    @admin_check()
    async def bank(self, interaction: discord.Interaction) -> None:
        by_cat = await self.db.unused_by_category()
        total = sum(by_cat.values())
        lines = [f"`{cat}` — {n}" for cat, n in by_cat.items()] or ["(empty)"]
        embed = discord.Embed(
            title=f"Question bank: {total} unused",
            description="\n".join(lines),
            color=discord.Color.green() if total >= 14 else discord.Color.red(),
        )
        if total < 14:
            embed.set_footer(text="Running low! Top up with /poll add")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- analytics -------------------------------------------------------

    @poll.command(name="history", description="Recent polls with results")
    @app_commands.describe(count="How many to show (default 5)")
    @admin_check()
    async def history(self, interaction: discord.Interaction, count: int = 5) -> None:
        rows = await self.db.poll_history(min(count, 15))
        if not rows:
            await interaction.response.send_message("No polls posted yet.", ephemeral=True)
            return
        lines = []
        for p in rows:
            date = p["posted_at"][:10]
            if p["finalized"]:
                outcome = p["winner_option"] or "TIE"
                lines.append(f"`{date}` **{p['question']}** → {outcome} ({p['total_votes']} votes)")
            else:
                lines.append(f"`{date}` **{p['question']}** → still open")
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Poll history", description="\n".join(lines), color=discord.Color.blurple()
            ),
            ephemeral=True,
        )

    @poll.command(name="stats", description="Engagement analytics")
    @admin_check()
    async def stats(self, interaction: discord.Interaction) -> None:
        data = await self.db.stats()
        posted = data["posted"] or 0
        if posted == 0:
            await interaction.response.send_message(
                "No finalized polls yet — stats show up after the first poll closes.",
                ephemeral=True,
            )
            return
        avg = data["avg_votes"] or 0
        tie_rate = 100 * data["ties"] / posted
        # Old manual-poll era baseline from the channel export: ~8-11 avg votes, 50% recent tie rate
        embed = discord.Embed(title="Poll engagement stats", color=discord.Color.gold())
        embed.add_field(name="Polls finalized", value=str(posted))
        embed.add_field(name="Avg votes", value=f"{avg:.1f} (old baseline ~8-11)")
        embed.add_field(name="Tie rate", value=f"{tie_rate:.0f}%")
        if data["by_category"]:
            cat_lines = [
                f"`{c['category']}` — {c['avg_votes']:.1f} avg over {c['n']}"
                for c in data["by_category"][:8]
            ]
            embed.add_field(name="By category", value="\n".join(cat_lines), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PollAdmin(bot))
