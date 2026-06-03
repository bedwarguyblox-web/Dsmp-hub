"""
vouches.py — /vouch, /scamvouch, /checkvouches, /leaderboard_vouches, /leaderboard_scamvouches
Anyone can vouch; duplicates are blocked per (voucher, target, guild) triplet.
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
from datetime import datetime, timezone

from utils.permissions import is_authorized, CONFIG
from utils.database import (
    add_vouch, add_scam_vouch,
    get_vouch_counts, get_recent_vouches, get_recent_scam_vouches,
    get_vouch_leaderboard, get_scam_vouch_leaderboard,
    log_staff_action
)

logger = logging.getLogger(__name__)

# Per-user cooldown: 1 vouch every 30 seconds
VOUCH_COOLDOWN = app_commands.checks.cooldown(1, 30.0, key=lambda i: (i.guild_id, i.user.id))


class VouchesCog(commands.Cog, name="Vouches"):
    """Vouch and scam-vouch commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /vouch ───────────────────────────────────────────────────────────────
    @app_commands.command(name="vouch", description="Vouch for a user with proof")
    @app_commands.describe(
        user="The member you are vouching for",
        proof="Link or description of your proof"
    )
    @VOUCH_COOLDOWN
    async def vouch(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        proof: str
    ):
        await interaction.response.defer()

        if not is_authorized(interaction.user, interaction.guild, "vouch"):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="You must be **Admin** or above to submit vouches.",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                ),
                ephemeral=True,
            )
            return

        # Cannot vouch for yourself
        if user.id == interaction.user.id:
            await interaction.followup.send(
                embed=discord.Embed(title="❌ Invalid", description="You cannot vouch for yourself.", color=discord.Color.red()),
                ephemeral=True
            )
            return

        if user.bot:
            await interaction.followup.send(
                embed=discord.Embed(title="❌ Invalid", description="You cannot vouch for a bot.", color=discord.Color.red()),
                ephemeral=True
            )
            return

        guild = interaction.guild
        success = add_vouch(interaction.user.id, user.id, guild.id, proof)

        if not success:
            embed = discord.Embed(
                title="⚠️ Duplicate Vouch",
                description=f"You have already vouched for {user.mention}.",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        total_v, total_sv = get_vouch_counts(user.id, guild.id)

        embed = discord.Embed(
            title="✅ Vouch Submitted",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Vouched For",  value=user.mention,            inline=True)
        embed.add_field(name="Vouched By",   value=interaction.user.mention, inline=True)
        embed.add_field(name="Total Vouches",value=str(total_v),             inline=True)
        embed.add_field(name="Proof",        value=proof,                   inline=False)
        embed.set_footer(text=f"User ID: {user.id}")
        await interaction.followup.send(embed=embed)

        log_staff_action("vouch", interaction.user.id, guild.id, target_id=user.id, details=proof)
        await self._send_to_logs(guild, CONFIG.get("VOUCH_LOGS_CHANNEL_ID"), embed)

    # ── /scamvouch ────────────────────────────────────────────────────────────
    @app_commands.command(name="scamvouch", description="Submit a scam report against a user with proof")
    @app_commands.describe(
        user="The member you are reporting",
        proof="Link or description of your proof"
    )
    @VOUCH_COOLDOWN
    async def scamvouch(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        proof: str
    ):
        await interaction.response.defer()

        if not is_authorized(interaction.user, interaction.guild, "scamvouch"):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="You must be **Admin** or above to submit scam vouches.",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                ),
                ephemeral=True,
            )
            return

        if user.id == interaction.user.id:
            await interaction.followup.send(
                embed=discord.Embed(title="❌ Invalid", description="You cannot scam-vouch yourself.", color=discord.Color.red()),
                ephemeral=True
            )
            return

        if user.bot:
            await interaction.followup.send(
                embed=discord.Embed(title="❌ Invalid", description="You cannot scam-vouch a bot.", color=discord.Color.red()),
                ephemeral=True
            )
            return

        guild = interaction.guild
        success = add_scam_vouch(interaction.user.id, user.id, guild.id, proof)

        if not success:
            embed = discord.Embed(
                title="⚠️ Duplicate Scam Vouch",
                description=f"You have already submitted a scam report for {user.mention}.",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        _, total_sv = get_vouch_counts(user.id, guild.id)

        embed = discord.Embed(
            title="🚨 Scam Vouch Submitted",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Reported User",      value=user.mention,            inline=True)
        embed.add_field(name="Reported By",        value=interaction.user.mention, inline=True)
        embed.add_field(name="Total Scam Vouches", value=str(total_sv),            inline=True)
        embed.add_field(name="Proof",              value=proof,                    inline=False)
        embed.set_footer(text=f"User ID: {user.id}")
        await interaction.followup.send(embed=embed)

        log_staff_action("scam_vouch", interaction.user.id, guild.id, target_id=user.id, details=proof)
        await self._send_to_logs(guild, CONFIG.get("SCAM_VOUCH_LOGS_CHANNEL_ID"), embed)

    # ── /checkvouches ─────────────────────────────────────────────────────────
    @app_commands.command(name="checkvouches", description="Check vouch record for a user")
    @app_commands.describe(user="The member to check")
    async def checkvouches(
        self,
        interaction: discord.Interaction,
        user: discord.Member
    ):
        await interaction.response.defer()

        if not is_authorized(interaction.user, interaction.guild, "checkvouches"):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="You must be **Admin** or above to check vouch records.",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                ),
                ephemeral=True,
            )
            return

        guild = interaction.guild
        total_v, total_sv = get_vouch_counts(user.id, guild.id)
        recent_v  = get_recent_vouches(user.id, guild.id, 5)
        recent_sv = get_recent_scam_vouches(user.id, guild.id, 5)

        # Vouch ratio string
        if total_v + total_sv == 0:
            ratio_str = "N/A"
        elif total_sv == 0:
            ratio_str = "✅ 100% positive"
        else:
            pct = total_v / (total_v + total_sv) * 100
            ratio_str = f"{pct:.1f}% positive"

        # Choose color based on ratio
        if total_sv == 0:
            color = discord.Color.green()
        elif total_v >= total_sv * 2:
            color = discord.Color.yellow()
        else:
            color = discord.Color.red()

        embed = discord.Embed(
            title=f"📊 Vouch Record — {user.display_name}",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="User",             value=user.mention,  inline=True)
        embed.add_field(name="✅ Total Vouches", value=str(total_v),   inline=True)
        embed.add_field(name="🚨 Scam Vouches",  value=str(total_sv),  inline=True)
        embed.add_field(name="📈 Vouch Ratio",   value=ratio_str,      inline=False)

        # Recent vouches section
        if recent_v:
            lines = []
            for v in recent_v:
                voucher = guild.get_member(v["voucher_id"])
                vname   = voucher.display_name if voucher else f"ID:{v['voucher_id']}"
                lines.append(f"• By **{vname}** on {v['timestamp'][:10]}\n  Proof: {v['proof'][:80]}")
            embed.add_field(name=f"Recent Vouches (last {len(recent_v)})", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Recent Vouches", value="None yet.", inline=False)

        # Recent scam vouches section
        if recent_sv:
            lines = []
            for sv in recent_sv:
                reporter = guild.get_member(sv["voucher_id"])
                rname    = reporter.display_name if reporter else f"ID:{sv['voucher_id']}"
                lines.append(f"• By **{rname}** on {sv['timestamp'][:10]}\n  Proof: {sv['proof'][:80]}")
            embed.add_field(name=f"Recent Scam Vouches (last {len(recent_sv)})", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Recent Scam Vouches", value="None reported.", inline=False)

        embed.set_footer(text=f"User ID: {user.id}")
        await interaction.followup.send(embed=embed)

    # ── /leaderboard_vouches ──────────────────────────────────────────────────
    @app_commands.command(name="leaderboard_vouches", description="Top-10 most vouched users")
    async def leaderboard_vouches(self, interaction: discord.Interaction):
        await interaction.response.defer()

        guild = interaction.guild
        rows  = get_vouch_leaderboard(guild.id, 10)

        embed = discord.Embed(
            title="🏆 Vouch Leaderboard",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        if not rows:
            embed.description = "No vouches recorded yet."
        else:
            medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
            lines  = []
            for i, row in enumerate(rows):
                member = guild.get_member(row["target_id"])
                name   = member.display_name if member else f"ID:{row['target_id']}"
                lines.append(f"{medals[i]} **{name}** — {row['total']} vouch(es)")
            embed.description = "\n".join(lines)
        embed.set_footer(text=f"Guild: {guild.name}")
        await interaction.followup.send(embed=embed)

    # ── /leaderboard_scamvouches ──────────────────────────────────────────────
    @app_commands.command(name="leaderboard_scamvouches", description="Top-10 most scam-vouched users")
    async def leaderboard_scamvouches(self, interaction: discord.Interaction):
        await interaction.response.defer()

        guild = interaction.guild
        rows  = get_scam_vouch_leaderboard(guild.id, 10)

        embed = discord.Embed(
            title="🚨 Scam Vouch Leaderboard",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        if not rows:
            embed.description = "No scam vouches recorded yet."
        else:
            lines = []
            for i, row in enumerate(rows, 1):
                member = guild.get_member(row["target_id"])
                name   = member.display_name if member else f"ID:{row['target_id']}"
                lines.append(f"`#{i}` **{name}** — {row['total']} report(s)")
            embed.description = "\n".join(lines)
        embed.set_footer(text=f"Guild: {guild.name}")
        await interaction.followup.send(embed=embed)

    # ── Cooldown error handler ────────────────────────────────────────────────
    @vouch.error
    @scamvouch.error
    async def on_cooldown(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            embed = discord.Embed(
                title="⏳ Cooldown",
                description=f"Please wait **{error.retry_after:.1f}s** before submitting again.",
                color=discord.Color.yellow(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            raise error

    # ── Internal log helper ───────────────────────────────────────────────────
    async def _send_to_logs(self, guild: discord.Guild, channel_id: int | None, embed: discord.Embed):
        if not channel_id:
            return
        ch = guild.get_channel(channel_id)
        if ch:
            try:
                await ch.send(embed=embed)
            except discord.Forbidden:
                logger.warning("Cannot send to logs channel %s", channel_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(VouchesCog(bot))
