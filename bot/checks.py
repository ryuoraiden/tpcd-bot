"""Shared permission helpers: owner + leadership roles + Manage Server."""
from __future__ import annotations

import discord
from discord import app_commands

from .config import config


def is_staff(interaction: discord.Interaction) -> bool:
    if interaction.user.id == config.owner_id:
        return True
    if isinstance(interaction.user, discord.Member):
        if interaction.user.guild_permissions.manage_guild:
            return True
        if {r.id for r in interaction.user.roles}.intersection(config.admin_role_ids):
            return True
    return False


def staff_only() -> app_commands.check:
    async def predicate(interaction: discord.Interaction) -> bool:
        if not is_staff(interaction):
            await interaction.response.send_message(
                "This command is for Captain / 1st Commander / Manager roles.", ephemeral=True
            )
            return False
        return True

    return app_commands.check(predicate)
