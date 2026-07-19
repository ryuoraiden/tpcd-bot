"""Keep @Club Member in sync with the seven TPCD club roles.

Anyone holding any of the TPCD¹..TPCD⁷ roles gets @Club Member; anyone
holding @Club Member without a club role loses it. Two mechanisms:

- live: on_member_update reacts the moment club roles change
- reconcile: a full sweep on startup and every 6 hours catches anything
  that happened while the bot was offline (or roles granted by other bots)
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..checks import staff_only

log = logging.getLogger(__name__)

CLUB_ROLE_IDS = {
    1358000283157925919,  # TPCD¹
    1358000362421878856,  # TPCD²
    1358000448250052648,  # TPCD³
    1358000710695911485,  # TPCD⁴
    1358000849443623075,  # TPCD⁵
    1358000936886472746,  # TPCD⁶
    1358001065772978207,  # TPCD⁷
}
CLUB_MEMBER_ROLE_ID = 1372179943538299021


class ClubSync(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.reconcile.start()

    async def cog_unload(self) -> None:
        self.reconcile.cancel()

    @staticmethod
    def _wants_club_member(member: discord.Member) -> bool:
        return any(r.id in CLUB_ROLE_IDS for r in member.roles)

    async def sync_member(self, member: discord.Member) -> str | None:
        """Returns 'added' / 'removed' / None. Never raises on permissions."""
        if member.bot:
            return None
        role = member.guild.get_role(CLUB_MEMBER_ROLE_ID)
        if role is None:
            return None
        has = role in member.roles
        wants = self._wants_club_member(member)
        try:
            if wants and not has:
                await member.add_roles(role, reason="In a TPCD club")
                return "added"
            if has and not wants:
                await member.remove_roles(role, reason="No longer in a TPCD club")
                return "removed"
        except discord.Forbidden:
            log.warning("No permission to sync Club Member for %s", member)
        except discord.HTTPException:
            log.exception("Failed syncing Club Member for %s", member)
        return None

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.roles == after.roles:
            return
        # only react when club roles or the member role itself changed
        changed = {r.id for r in before.roles} ^ {r.id for r in after.roles}
        if changed & (CLUB_ROLE_IDS | {CLUB_MEMBER_ROLE_ID}):
            await self.sync_member(after)

    @tasks.loop(hours=6)
    async def reconcile(self) -> None:
        added = removed = 0
        for guild in self.bot.guilds:
            if guild.get_role(CLUB_MEMBER_ROLE_ID) is None:
                continue
            for member in guild.members:
                result = await self.sync_member(member)
                if result == "added":
                    added += 1
                elif result == "removed":
                    removed += 1
        if added or removed:
            log.info("Club Member reconcile: +%d added, -%d removed", added, removed)

    @reconcile.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="clubsync", description="Sync @Club Member with the TPCD club roles now"
    )
    @staff_only()
    async def clubsync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        added = removed = have = 0
        for member in interaction.guild.members:
            result = await self.sync_member(member)
            if result == "added":
                added += 1
            elif result == "removed":
                removed += 1
        role = interaction.guild.get_role(CLUB_MEMBER_ROLE_ID)
        have = len(role.members) if role else 0
        await interaction.followup.send(
            f"Synced. Added {added}, removed {removed}. "
            f"<@&{CLUB_MEMBER_ROLE_ID}> now has **{have}** members.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ClubSync(bot))
