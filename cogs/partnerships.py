"""
partnerships.py — /partnership log, /partnership stats, /partnership leaderboard
Also auto-tracks partnerships by watching the configured partnership channel
for Discord invite links — each unique invite in a message = 1 partnership.
"""

import re
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

# Matches discord.gg/CODE, discord.com/invite/CODE, discordapp.com/invite/CODE
INVITE_RE = re.compile(
    r'discord(?:\.gg|(?:app)?\.com/invite)/([a-zA-Z0-9-]+)',
    re.IGNORECASE
)


class PartnershipsCog(commands.Cog, name="Partnerships"):
    """Partnership tracking commands + auto-detection from channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    partnership_group = app_commands.Group(
        name="partnership",
        description="Partnership tracking commands"
    )

    # ── Auto-track: watch partnership channel for invite links ───────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Skip bots and DMs
        if message.author.bot or not message.guild:
            return

        channel_id = CONFIG.get("PARTNERSHIP_CHANNEL_ID")
        if not channel_id or message.channel.id != channel_id:
            return

        # Find all unique invite codes in the message
        codes = list(dict.fromkeys(INVITE_RE.findall(message.content)))
        if not codes:
            return

        guild    = message.guild
        staff_id = message.author.id
        logged   = 0

        for code in codes:
            partner_name = f"discord.gg/{code}"
            log_partnership(
                staff_id, guild.id,
                partner_name=partner_name,
                notes=f"Auto-detected in #{message.channel.name}",
                invite_code=code,
            )
            log_staff_action(
                "partnership_auto", staff_id, guild.id,
                details=f"Invite: {code} | Channel: {message.channel.id}"
            )
            logged += 1

        if logged:
            try:
                await message.add_reaction("🤝")
            except (discord.Forbidden, discord.HTTPException):
                pass

            total = get_partnership_count(staff_id, guild.id)
            logger.info(
                "Auto-logged %d partnership(s) for %s (%s) — total now %d",
                logged, message.author, staff_id, total
            )

            await self._send_to_logs(guild, discord.Embed(
                title="🤝 Partnership Auto-Logged",
                description=(
                    f"**Staff:** {message.author.mention}\n"
                    f"**Invite(s):** {', '.join(f'discord.gg/{c}' for c in codes)}\n"
                    f"**Their Total:** {total}"
                ),
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            ))

    # ── /partnership log ─────────────────────────────────────────────────────
    @partnership_group.command(
        name="log",
        description="Manually log a completed partnership"
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
        embed.add_field(name="Logged By",  value=interaction.user.mention, inline=True)
        embed.add_field(name="Partner",    value=partner_name,             inline=True)
        embed.add_field(name="Your Total", value=str(total),               inline=True)
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

        total  = get_partnership_count(target.id, guild.id)
        recent = get_recent_partnerships(target.id, guild.id, 5)

        embed = discord.Embed(
            title=f"🤝 Partnership Stats — {target.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Staff Member",       value=target.mention, inline=True)
        embed.add_field(name="Total Partnerships", value=str(total),     inline=True)

        if recent:
            lines = []
            for p in recent:
                ts    = str(p["timestamp"])[:10]
                notes = f" — {p['notes'][:50]}" if p["notes"] else ""
                # Show invite code badge if auto-tracked
                badge = " 🔗" if p["invite_code"] else ""
                lines.append(f"• **{p['partner_name']}**{badge} on {ts}{notes}")
            embed.add_field(
                name=f"Recent (last {len(recent)})",
                value="\n".join(lines),
                inline=False,
            )
        else:
            embed.add_field(name="Recent", value="No partnerships logged yet.", inline=False)

        embed.set_footer(text=f"User ID: {target.id} • 🔗 = auto-tracked from channel")
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
            embed.description = "No partnerships have been logged yet.\nPost an invite link in the partnership channel to get started."
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
