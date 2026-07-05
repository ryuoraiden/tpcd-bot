"""Welcome + goodbye messages (replaces Mimu/Koya for TPCD).

Requires the privileged Server Members Intent (enabled in __main__ only when
WELCOME_CHANNEL_ID / GOODBYE_CHANNEL_ID are configured). If neither is set,
the listeners simply never fire.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands

from ..config import config

log = logging.getLogger(__name__)

# TPCD quick-start channels (single-server bot, so constants are fine)
CH_RULES = 1265582601410707489
CH_SELF_ROLES = 1372529993304903751
CH_APPLY = 1366486541626511521
CH_DAILY_POLLS = 1399995153850306640
CH_GIVEAWAY = 1302595319208607844

# optional banner shown at the bottom of the welcome embed; drop a PNG here
BANNER_PATH = Path(__file__).parent.parent / "data" / "assets" / "welcome_banner.png"

WELCOME_LINES = [
    "Another hunter joins the ranks. Teamers, beware. 🎯",
    "The Department grows stronger.",
    "Reinforcements have arrived. 🫡",
    "A new challenger appears!",
    "Fresh recruit on deck. Show them the ropes.",
    "The pest control roster just got bigger.",
]

GOODBYE_LINES = [
    "o7 Safe travels.",
    "The Department salutes your service.",
    "Gone, but the ban hammer remembers.",
    "One less hunter on the field.",
]


def _fmt_duration(joined_at: datetime | None) -> str | None:
    if joined_at is None:
        return None
    days = (datetime.now(timezone.utc) - joined_at).days
    if days < 1:
        return "less than a day"
    if days < 60:
        return f"{days} day{'s' if days != 1 else ''}"
    months, rem = divmod(days, 30)
    return f"about {months} months" if rem < 15 else f"about {months + 1} months"


class Greetings(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not config.welcome_channel_id or member.bot:
            return
        channel = self.bot.get_channel(config.welcome_channel_id)
        if channel is None:
            return

        embed = discord.Embed(
            title=f"Welcome to TPCD, {member.display_name}! 🎉",
            description=(
                f"*{random.choice(WELCOME_LINES)}*\n\n"
                f"**Get started:**\n"
                f"📜 Read the <#{CH_RULES}>\n"
                f"🎭 Grab your roles in <#{CH_SELF_ROLES}> (📊 Poll Ping!)\n"
                f"🎟️ Join one of our clubs → <#{CH_APPLY}>\n"
                f"📊 Vote in today's <#{CH_DAILY_POLLS}>\n"
                f"🎁 Giveaways and events → <#{CH_GIVEAWAY}>"
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(
            text=f"Member #{member.guild.member_count} · Teamer Pest Control Department"
        )

        kwargs: dict = {"embed": embed}
        if BANNER_PATH.exists():
            kwargs["file"] = discord.File(BANNER_PATH, filename="welcome_banner.png")
            embed.set_image(url="attachment://welcome_banner.png")
        try:
            await channel.send(
                content=f"🎉 {member.mention} just landed!",
                allowed_mentions=discord.AllowedMentions(users=True),
                **kwargs,
            )
        except discord.HTTPException:
            log.exception("Failed to send welcome message")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not config.goodbye_channel_id or member.bot:
            return
        channel = self.bot.get_channel(config.goodbye_channel_id)
        if channel is None:
            return

        lines = [f"**{member.display_name}** left the server. {random.choice(GOODBYE_LINES)}"]
        stayed = _fmt_duration(member.joined_at)
        if stayed:
            lines.append(f"Was with us for **{stayed}**.")
        embed = discord.Embed(
            description="\n".join(lines),
            color=discord.Color.dark_grey(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"User ID {member.id} · {member.guild.member_count} members remain")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("Failed to send goodbye message")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Greetings(bot))
