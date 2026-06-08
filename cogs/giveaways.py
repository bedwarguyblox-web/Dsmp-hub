"""
giveaways.py — /giveaway and /quickdrop commands.
Reaction-based prize drops with countdown, random winner selection, and winner ping.
"""

import asyncio
import random
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.permissions import is_authorized

logger = logging.getLogger(__name__)

ENTRY_EMOJI = "🎉"


def parse_duration(s: str) -> int | None:
    """Parse '30s', '5m', '2h', '1d' → total seconds. Returns None if invalid."""
    s = s.strip().lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if len(s) >= 2 and s[-1] in multipliers and s[:-1].isdigit():
        return int(s[:-1]) * multipliers[s[-1]]
    if s.isdigit():
        return int(s)
    return None


class GiveawaysCog(commands.Cog, name="Giveaways"):
    """Giveaway and quickdrop commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _run(
        self,
        interaction: discord.Interaction,
        prize: str,
        duration_str: str,
        num_winners: int,
        is_quickdrop: bool,
    ):
        await interaction.response.defer(ephemeral=True)

        cmd_name = "quickdrop" if is_quickdrop else "giveaway"

        if not is_authorized(interaction.user, interaction.guild, cmd_name):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description=f"You must be **Admin** or above (or granted `{cmd_name}` access) to use this command.",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                ),
                ephemeral=True,
            )
            return

        seconds = parse_duration(duration_str)
        if seconds is None or seconds < 5 or seconds > 604800:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Invalid Duration",
                    description="Use a format like `30s`, `5m`, `2h`, `1d`. Minimum 5 seconds, maximum 7 days.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        end_ts    = int(datetime.now(timezone.utc).timestamp()) + seconds
        kind_tag  = "⚡ QUICKDROP" if is_quickdrop else "🎉 GIVEAWAY"
        color     = discord.Color.orange() if is_quickdrop else discord.Color.blue()

        # ── Send the live giveaway embed ──────────────────────────────────────
        embed = discord.Embed(
            title=f"{kind_tag} — {prize}",
            description=(
                f"React with {ENTRY_EMOJI} to enter!\n\n"
                f"**Prize:** {prize}\n"
                f"**Winners:** {num_winners}\n"
                f"**Ends:** <t:{end_ts}:R> (<t:{end_ts}:t>)"
            ),
            color=color,
            timestamp=datetime.fromtimestamp(end_ts, tz=timezone.utc),
        )
        embed.set_footer(text=f"Hosted by {interaction.user.display_name} • Ends at")

        msg = await interaction.channel.send(embed=embed)
        await msg.add_reaction(ENTRY_EMOJI)

        await interaction.followup.send(
            embed=discord.Embed(
                description=f"✅ {'Quickdrop' if is_quickdrop else 'Giveaway'} started in {interaction.channel.mention}!",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

        logger.info(
            "%s started by %s in guild %s — prize: %s, duration: %ds, winners: %d",
            kind_tag, interaction.user, interaction.guild.name, prize, seconds, num_winners,
        )

        # ── Wait ──────────────────────────────────────────────────────────────
        await asyncio.sleep(seconds)

        # ── Fetch updated reactions ───────────────────────────────────────────
        try:
            msg = await interaction.channel.fetch_message(msg.id)
        except discord.NotFound:
            logger.warning("Giveaway message deleted before it ended.")
            return

        entrants: list[discord.User] = []
        for reaction in msg.reactions:
            if str(reaction.emoji) == ENTRY_EMOJI:
                async for user in reaction.users():
                    if not user.bot:
                        entrants.append(user)
                break

        # Deduplicate (shouldn't be needed, but safe)
        seen = set()
        unique_entrants = []
        for u in entrants:
            if u.id not in seen:
                seen.add(u.id)
                unique_entrants.append(u)
        entrants = unique_entrants

        # ── Pick winners ──────────────────────────────────────────────────────
        actual_winners = min(num_winners, len(entrants))
        if entrants:
            winners        = random.sample(entrants, actual_winners)
            winner_mentions = ", ".join(w.mention for w in winners)
            win_text        = f"🏆 **Winner{'s' if actual_winners > 1 else ''}:** {winner_mentions}"
        else:
            winners         = []
            winner_mentions = ""
            win_text        = "😢 Nobody entered — no winners this time!"

        # ── Edit original embed to ended state ────────────────────────────────
        ended_embed = discord.Embed(
            title=f"{kind_tag} ENDED — {prize}",
            description=(
                f"**Prize:** {prize}\n"
                f"**Entries:** {len(entrants)}\n\n"
                f"{win_text}"
            ),
            color=discord.Color.dark_grey(),
            timestamp=datetime.now(timezone.utc),
        )
        ended_embed.set_footer(text=f"Hosted by {interaction.user.display_name} • Ended")
        await msg.edit(embed=ended_embed)

        # ── Announce winners ──────────────────────────────────────────────────
        if winners:
            await interaction.channel.send(
                content=winner_mentions,
                embed=discord.Embed(
                    title=f"🏆 {'Quickdrop' if is_quickdrop else 'Giveaway'} Winner{'s' if actual_winners > 1 else ''}!",
                    description=(
                        f"Congratulations {winner_mentions}!\n"
                        f"You won **{prize}**!\n\n"
                        f"Contact {interaction.user.mention} to claim your prize."
                    ),
                    color=discord.Color.gold(),
                    timestamp=datetime.now(timezone.utc),
                ),
            )
        else:
            await interaction.channel.send(
                embed=discord.Embed(
                    title="😢 No Winners",
                    description=f"Nobody entered the {'quickdrop' if is_quickdrop else 'giveaway'} for **{prize}**.",
                    color=discord.Color.dark_grey(),
                    timestamp=datetime.now(timezone.utc),
                )
            )

    # ── /giveaway ─────────────────────────────────────────────────────────────
    @app_commands.command(name="giveaway", description="Start a giveaway in this channel")
    @app_commands.describe(
        prize="What you're giving away",
        duration="How long to run: 30s, 5m, 2h, 1d, etc.",
        winners="Number of winners (1–10, default 1)",
    )
    async def giveaway(
        self,
        interaction: discord.Interaction,
        prize: str,
        duration: str,
        winners: app_commands.Range[int, 1, 10] = 1,
    ):
        await self._run(interaction, prize, duration, winners, is_quickdrop=False)

    # ── /quickdrop ────────────────────────────────────────────────────────────
    @app_commands.command(name="quickdrop", description="Start a flash quickdrop in this channel")
    @app_commands.describe(
        prize="What you're dropping",
        duration="How long it lasts: 30s, 5m, 2h, etc.",
        winners="Number of winners (1–10, default 1)",
    )
    async def quickdrop(
        self,
        interaction: discord.Interaction,
        prize: str,
        duration: str,
        winners: app_commands.Range[int, 1, 10] = 1,
    ):
        await self._run(interaction, prize, duration, winners, is_quickdrop=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawaysCog(bot))
