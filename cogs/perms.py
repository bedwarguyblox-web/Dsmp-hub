"""
perms.py — /grantperms, /revokeperms, /listperms commands.
Only the bot Owner can grant or revoke per-user bot access.
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
from datetime import datetime, timezone

from utils.permissions import is_owner, CONFIG
from utils.database import grant_bot_perm, revoke_bot_perm, list_granted_perms

logger = logging.getLogger(__name__)


class PermsCog(commands.Cog, name="Perms"):
    """Bot access permission management — Owner only."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _owner_only(self, interaction: discord.Interaction) -> bool:
        return is_owner(interaction.user)

    # ── /grantperms ──────────────────────────────────────────────────────────
    @app_commands.command(
        name="grantperms",
        description="Grant a user access to all bot commands (Owner only)"
    )
    @app_commands.describe(user="The member to grant bot access to")
    async def grantperms(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)

        if not self._owner_only(interaction):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="Only the **Owner** can grant bot access.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        if user.bot:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Invalid Target",
                    description="You cannot grant permissions to a bot.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        if user.id == interaction.user.id:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Invalid Target",
                    description="You already have full access as the Owner.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        already = grant_bot_perm(user.id, interaction.guild.id, interaction.user.id)

        if not already:
            embed = discord.Embed(
                title="ℹ️ Already Granted",
                description=f"{user.mention} already has bot access.",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc),
            )
        else:
            embed = discord.Embed(
                title="✅ Access Granted",
                description=f"{user.mention} can now use all bot commands.",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Granted By", value=interaction.user.mention, inline=True)
            embed.add_field(name="User ID",    value=str(user.id),            inline=True)
            logger.info("Bot perm granted: %s → %s by %s", user.id, interaction.guild.id, interaction.user.id)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /revokeperms ─────────────────────────────────────────────────────────
    @app_commands.command(
        name="revokeperms",
        description="Revoke a user's granted bot access (Owner only)"
    )
    @app_commands.describe(user="The member to revoke bot access from")
    async def revokeperms(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)

        if not self._owner_only(interaction):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="Only the **Owner** can revoke bot access.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        removed = revoke_bot_perm(user.id, interaction.guild.id)

        if not removed:
            embed = discord.Embed(
                title="ℹ️ Not Found",
                description=f"{user.mention} does not have a granted permission to revoke.",
                color=discord.Color.blue(),
            )
        else:
            embed = discord.Embed(
                title="✅ Access Revoked",
                description=f"{user.mention}'s bot access has been removed.",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Revoked By", value=interaction.user.mention, inline=True)
            embed.add_field(name="User ID",    value=str(user.id),            inline=True)
            logger.info("Bot perm revoked: %s from %s by %s", user.id, interaction.guild.id, interaction.user.id)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /listperms ───────────────────────────────────────────────────────────
    @app_commands.command(
        name="listperms",
        description="List all users with granted bot access (Owner only)"
    )
    async def listperms(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not self._owner_only(interaction):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="Only the **Owner** can view granted permissions.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        rows = list_granted_perms(interaction.guild.id)

        embed = discord.Embed(
            title="Bot Access Grants",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )

        if not rows:
            embed.description = "No users have been granted bot access.\nUse `/grantperms @user` to add one."
        else:
            lines = []
            for row in rows:
                member = interaction.guild.get_member(row["user_id"])
                granter = interaction.guild.get_member(row["granted_by"])
                name_str    = member.mention  if member  else f"`{row['user_id']}`"
                granter_str = granter.mention if granter else f"`{row['granted_by']}`"
                lines.append(f"{name_str} — granted by {granter_str} on {str(row['granted_at'])[:10]}")
            embed.description = "\n".join(lines)
            embed.set_footer(text=f"{len(rows)} user(s) with granted access")

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PermsCog(bot))
