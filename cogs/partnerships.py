"""
partnerships.py — /partnership log, /partnership stats, /partnership leaderboard
Tracks how many partnerships each staff member has completed.
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
from datetime import datetime, timezone
from typing import Optional

from utils.permissions import is_authorized, CONFIG
from utils.database import (
    log_partnership, get_partnership_count,
    get_recent_partnerships, get_partnership_leaderboard,
    get_total_partnerships, log_staff_action
)

logger = logging.getLogger(__name__)


class PartnershipsCog(commands.Cog, name="Partnerships"):
    """Partnership tracking commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    partnership_group = app_commands.Group(
        name="partnership",
        description="Partnership tracking commands"
    )

    # ── /partnership log ─────────────────────────────────────────────────────
    @partnership_group.command(
        name="log",
        description="Log a completed partnership"
    )
    @app_commands.describe(
        partner_name="Name of the server or person you partnered with",
        notes="Optional notes (invite link, deal details, etc.)"
    )
    async def partnership_log(
        self,
        interaction: discord.Interaction,
        partner_name: str,
        notes: Optional[str] = None,
    ):
        await interaction.response.defer()

        if not is_authorized(interaction.user, interaction.guild, "partnership"):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="You must be **Admin** or above (or have been granted partnership access) to log partnerships.",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                ),
                ephemeral=True,
            )
            return

        guild = interaction.guild
        log_partnership(interaction.user.id, guild.id, partner_name, notes)
        total = get_partnership_count(interaction.user.id, guild.id)

        log_staff_action(
            "partnership_log", interaction.user.id, guild.id,
            details=f"Partner: {partner_name} | Notes: {notes or 'none'}"
        )

        embed = discord.Embed(
            title="🤝 Partnership Logged",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="Logged By",      value=interaction.user.mention, inline=True)
        embed.add_field(name="Partner",        value=partner_name,             inline=True)
        embed.add_field(name="Your Total",     value=str(total),               inline=True)
        if notes:
            embed.add_field(name="Notes", value=notes, inline=False)
        embed.set_footer(text=f"Staff ID: {interaction.user.id}")

        await interaction.followup.send(embed=embed)
        await self._send_to_logs(guild, embed)

    # ── /partnership stats ───────────────────────────────────────────────────
    @partnership_group.command(
        name="stats",
        description="View partnership stats for yourself or another staff member"
    )
    @app_commands.describe(user="The staff member to check (defaults to yourself)")
    async def partnership_stats(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
    ):
        await interaction.response.defer()

        if not is_authorized(interaction.user, interaction.guild, "partnership"):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="You must be **Admin** or above to view partnership stats.",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                ),
                ephemeral=True,
            )
            return

        target = user or interaction.user
        guild  = interaction.guild

        total   = get_partnership_count(target.id, guild.id)
        recent  = get_recent_partnerships(target.id, guild.id, 5)

        embed = discord.Embed(
            title=f"🤝 Partnership Stats — {target.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Staff Member",    value=target.mention, inline=True)
        embed.add_field(name="Total Partnerships", value=str(total), inline=True)

        if recent:
            lines = []
            for p in recent:
                ts    = str(p["timestamp"])[:10]
                notes = f" — {p['notes'][:50]}" if p["notes"] else ""
                lines.append(f"• **{p['partner_name']}** on {ts}{notes}")
            embed.add_field(
                name=f"Recent (last {len(recent)})",
                value="\n".join(lines),
                inline=False,
            )
        else:
            embed.add_field(name="Recent", value="No partnerships logged yet.", inline=False)

        embed.set_footer(text=f"User ID: {target.id}")
        await interaction.followup.send(embed=embed)

    # ── /partnership leaderboard ─────────────────────────────────────────────
    @partnership_group.command(
        name="leaderboard",
        description="Top staff members by number of partnerships completed"
    )
    async def partnership_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()

        if not is_authorized(interaction.user, interaction.guild, "partnership"):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="You must be **Admin** or above to view the partnership leaderboard.",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                ),
                ephemeral=True,
            )
            return

        guild = interaction.guild
        rows  = get_partnership_leaderboard(guild.id, 10)
        total = get_total_partnerships(guild.id)

        embed = discord.Embed(
            title="🏆 Partnership Leaderboard",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )

        if not rows:
            embed.description = "No partnerships have been logged yet.\nUse `/partnership log` to get started."
        else:
            medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
            lines  = []
            for i, row in enumerate(rows):
                member = guild.get_member(row["staff_id"])
                name   = member.display_name if member else f"ID:{row['staff_id']}"
                lines.append(f"{medals[i]} **{name}** — {row['total']} partnership(s)")
            embed.description = "\n".join(lines)
            embed.set_footer(text=f"Server total: {total} partnership(s)")

        await interaction.followup.send(embed=embed)

    # ── Internal log helper ───────────────────────────────────────────────────
    async def _send_to_logs(self, guild: discord.Guild, embed: discord.Embed):
        channel_id = CONFIG.get("PARTNERSHIP_LOGS_CHANNEL_ID")
        if not channel_id:
            return
        ch = guild.get_channel(channel_id)
        if ch:
            try:
                await ch.send(embed=embed)
            except discord.Forbidden:
                logger.warning("Cannot send to partnership logs channel %s", channel_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(PartnershipsCog(bot))
