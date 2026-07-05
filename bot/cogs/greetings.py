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

# low-key openers, rotated so back-to-back joins don't look copy-pasted
WELCOME_OPENERS = [
    "Good to have you here.",
    "Glad you made it.",
    "Make yourself at home.",
    "Welcome aboard.",
]


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


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
            description=(
                f"{random.choice(WELCOME_OPENERS)} A few things to get you started:\n\n"
                f"📜 Rules live in <#{CH_RULES}>, give them a quick read\n"
                f"🎭 Pick up your roles in <#{CH_SELF_ROLES}>, Poll Ping gets you the daily poll\n"
                f"🎟️ Want to join one of our clubs? Head to <#{CH_APPLY}>\n"
                f"🎁 Giveaways and events run in <#{CH_GIVEAWAY}>\n\n"
                f"Enjoy your stay!"
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"You are our {_ordinal(member.guild.member_count)} member")

        kwargs: dict = {"embed": embed}
        if BANNER_PATH.exists():
            kwargs["file"] = discord.File(BANNER_PATH, filename="welcome_banner.png")
            embed.set_image(url="attachment://welcome_banner.png")
        try:
            await channel.send(
                content=f"Hey {member.mention}, welcome to TPCD 🎉",
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

        lines = [f"**{member.display_name}** has left the server."]
        stayed = _fmt_duration(member.joined_at)
        if stayed:
            lines.append(f"Member for {stayed}.")
        embed = discord.Embed(
            description="\n".join(lines),
            color=discord.Color.dark_grey(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"ID {member.id} · {member.guild.member_count} members")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("Failed to send goodbye message")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Greetings(bot))
