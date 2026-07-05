"""Self-role panel with toggle buttons (upgrade from Carl-bot reaction roles).

Same architecture as NITC Bot's panel: each button is a discord.py
DynamicItem with the role id encoded in its custom_id, so components keep
working across restarts with no per-message state.

Needs Manage Roles and the bot's top role positioned above every role it
hands out.
"""
from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import staff_only

log = logging.getLogger(__name__)

# (role_id, emoji, button label, description)
PANEL_ROLES = [
    (1372181510328680458, "🎮", "LFTeammates", "Find teammates for matches"),
    (1372184101653581835, "💬", "Revive Chat", "Get pinged when we revive the chat"),
    (1372184183224664144, "📊", "Polls", "Never miss the daily poll"),
    (1372184564524388492, "🎉", "Events", "Events and tournaments"),
    (1372184654043676744, "🎁", "Giveaways", "Don't miss giveaways"),
    (1372184739842359326, "📋", "Recruitment", "Stay updated on club recruitment"),
    (1372184839033196544, "🕹️", "Game Updates", "Game updates and balance changes"),
]


class RoleButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"tpcd:role:(?P<role_id>[0-9]+)",
):
    def __init__(self, role_id: int, label: str | None = None, emoji: str | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.secondary,
                custom_id=f"tpcd:role:{role_id}",
            )
        )
        self.role_id = role_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]
    ) -> "RoleButton":
        return cls(int(match["role_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        role = interaction.guild.get_role(self.role_id)
        if role is None:
            await interaction.response.send_message(
                "That role no longer exists. Ping a Manager.", ephemeral=True
            )
            return
        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Self-role panel")
                await interaction.response.send_message(
                    f"Removed {role.mention}.", ephemeral=True
                )
            else:
                await member.add_roles(role, reason="Self-role panel")
                await interaction.response.send_message(
                    f"You now have {role.mention}. You can remove it anytime with the same button.",
                    ephemeral=True,
                )
        except discord.Forbidden:
            log.warning("Missing permissions to toggle role %s", role.name)
            await interaction.response.send_message(
                "I can't manage that role yet. Staff: give me **Manage Roles** and move my "
                "role above the ping roles in Server Settings → Roles.",
                ephemeral=True,
            )


def build_panel_view() -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for role_id, emoji, label, _ in PANEL_ROLES:
        view.add_item(RoleButton(role_id, label=label, emoji=emoji))
    return view


def build_panel_embed() -> discord.Embed:
    lines = [
        f"{emoji} <@&{role_id}> — {desc}"
        for role_id, emoji, _, desc in PANEL_ROLES
    ]
    embed = discord.Embed(
        title="🔔 Notification Roles",
        description=(
            "Tap a button to get the role, tap again to remove it.\n\n"
            + "\n".join(lines)
        ),
        color=discord.Color.blurple(),
    )
    return embed


class SelfRoles(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(RoleButton)

    @app_commands.command(name="rolepanel", description="Post the self-role button panel here")
    @staff_only()
    async def rolepanel(self, interaction: discord.Interaction) -> None:
        me = interaction.guild.me
        problems = []
        if not me.guild_permissions.manage_roles:
            problems.append("I don't have the **Manage Roles** permission")
        blocked = [
            f"<@&{rid}>" for rid, *_ in PANEL_ROLES
            if (r := interaction.guild.get_role(rid)) and r >= me.top_role
        ]
        if blocked:
            problems.append(f"my top role is below: {', '.join(blocked)}")
        if problems:
            await interaction.response.send_message(
                "Panel posted would not work yet: " + "; ".join(problems)
                + ".\nFix in Server Settings → Roles, then run this again.",
                ephemeral=True,
            )
            return
        await interaction.channel.send(embed=build_panel_embed(), view=build_panel_view())
        await interaction.response.send_message("Panel posted.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SelfRoles(bot))
