"""
perms.py — /giveperms, /removeperms, /listperms commands.
Only the bot Owner can grant or revoke per-user/per-role command access.
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
from datetime import datetime, timezone
from typing import Optional

from utils.permissions import is_owner
from utils.database import add_perm_grant, remove_perm_grant, list_perm_grants

logger = logging.getLogger(__name__)

# All grantable command names shown as choices in Discord
COMMAND_CHOICES = [
    app_commands.Choice(name="All Commands",          value="all"),
    app_commands.Choice(name="vouch",                 value="vouch"),
    app_commands.Choice(name="scamvouch",             value="scamvouch"),
    app_commands.Choice(name="checkvouches",          value="checkvouches"),
    app_commands.Choice(name="strike",                value="strike"),
    app_commands.Choice(name="removestrike",          value="removestrike"),
    app_commands.Choice(name="checkstrikes",          value="checkstrikes"),
    app_commands.Choice(name="addroles / removeroles",value="addroles"),
    app_commands.Choice(name="serverify",             value="serverify"),
    app_commands.Choice(name="partnership",           value="partnership"),
]


def _denied(interaction: discord.Interaction):
    return discord.Embed(
        title="❌ Permission Denied",
        description="Only the **Owner** can manage bot access.",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )


class PermsCog(commands.Cog, name="Perms"):
    """Bot access permission management — Owner only."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /giveperms ───────────────────────────────────────────────────────────
    @app_commands.command(
        name="giveperms",
        description="Grant a user or role access to a specific bot command (Owner only)"
    )
    @app_commands.describe(
        user="The member to grant access to (leave blank to target a role)",
        role="The role to grant access to (leave blank to target a user)",
        command="Which command to grant access to"
    )
    @app_commands.choices(command=COMMAND_CHOICES)
    async def giveperms(
        self,
        interaction: discord.Interaction,
        command: app_commands.Choice[str],
        user: Optional[discord.Member] = None,
        role: Optional[discord.Role] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if not is_owner(interaction.user):
            await interaction.followup.send(embed=_denied(interaction), ephemeral=True)
            return

        if user is None and role is None:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Missing Target",
                    description="You must specify either a **user** or a **role**.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        if user is not None and role is not None:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Too Many Targets",
                    description="Please specify either a user **or** a role, not both.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        target_type = "user" if user else "role"
        target      = user or role
        target_id   = target.id

        if target_type == "user" and target.bot:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Invalid Target",
                    description="You cannot grant permissions to a bot.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        is_new = add_perm_grant(target_type, target_id, interaction.guild.id,
                                command.value, interaction.user.id)

        cmd_label = command.name

        if not is_new:
            embed = discord.Embed(
                title="ℹ️ Already Granted",
                description=f"{target.mention} already has access to **{cmd_label}**.",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc),
            )
        else:
            embed = discord.Embed(
                title="✅ Permission Granted",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Target",     value=f"{target.mention} ({target_type})", inline=True)
            embed.add_field(name="Command",    value=f"`{cmd_label}`",                    inline=True)
            embed.add_field(name="Granted By", value=interaction.user.mention,            inline=True)
            logger.info(
                "Perm granted: %s %s → %s (%s) by %s",
                target_type, target_id, command.value, interaction.guild.id, interaction.user.id
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /removeperms ─────────────────────────────────────────────────────────
    @app_commands.command(
        name="removeperms",
        description="Revoke a user or role's granted command access (Owner only)"
    )
    @app_commands.describe(
        user="The member to revoke access from (leave blank to target a role)",
        role="The role to revoke access from (leave blank to target a user)",
        command="Which command to revoke"
    )
    @app_commands.choices(command=COMMAND_CHOICES)
    async def removeperms(
        self,
        interaction: discord.Interaction,
        command: app_commands.Choice[str],
        user: Optional[discord.Member] = None,
        role: Optional[discord.Role] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if not is_owner(interaction.user):
            await interaction.followup.send(embed=_denied(interaction), ephemeral=True)
            return

        if user is None and role is None:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Missing Target",
                    description="You must specify either a **user** or a **role**.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        if user is not None and role is not None:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Too Many Targets",
                    description="Please specify either a user **or** a role, not both.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        target_type = "user" if user else "role"
        target      = user or role

        removed = remove_perm_grant(target_type, target.id, interaction.guild.id, command.value)

        cmd_label = command.name

        if not removed:
            embed = discord.Embed(
                title="ℹ️ Not Found",
                description=f"{target.mention} doesn't have a **{cmd_label}** grant to remove.",
                color=discord.Color.blue(),
            )
        else:
            embed = discord.Embed(
                title="✅ Permission Removed",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Target",     value=f"{target.mention} ({target_type})", inline=True)
            embed.add_field(name="Command",    value=f"`{cmd_label}`",                    inline=True)
            embed.add_field(name="Removed By", value=interaction.user.mention,            inline=True)
            logger.info(
                "Perm removed: %s %s → %s (%s) by %s",
                target_type, target.id, command.value, interaction.guild.id, interaction.user.id
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /listperms ───────────────────────────────────────────────────────────
    @app_commands.command(
        name="listperms",
        description="List all granted bot access permissions (Owner only)"
    )
    async def listperms(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not is_owner(interaction.user):
            await interaction.followup.send(embed=_denied(interaction), ephemeral=True)
            return

        rows = list_perm_grants(interaction.guild.id)

        embed = discord.Embed(
            title="🔑 Bot Permission Grants",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )

        if not rows:
            embed.description = "No permissions granted yet.\nUse `/giveperms` to add one."
        else:
            lines = []
            for row in rows:
                if row["target_type"] == "user":
                    target = interaction.guild.get_member(row["target_id"])
                    label  = target.mention if target else f"`user:{row['target_id']}`"
                else:
                    target = interaction.guild.get_role(row["target_id"])
                    label  = target.mention if target else f"`role:{row['target_id']}`"

                cmd    = row["command_name"]
                date   = str(row["granted_at"])[:10]
                lines.append(f"{label} → `{cmd}` (since {date})")

            embed.description = "\n".join(lines)
            embed.set_footer(text=f"{len(rows)} grant(s) active")

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PermsCog(bot))
