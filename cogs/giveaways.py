"""
giveaways.py — /giveaway, /quickdrop, /rerollgiveaway commands.
Button-based entry with live entry count displayed on the button.
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


def parse_duration(s: str):
    """Parse '30s', '5m', '2h', '1d' → total seconds. Returns None if invalid."""
    s = s.strip().lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if len(s) >= 2 and s[-1] in multipliers and s[:-1].isdigit():
        return int(s[:-1]) * multipliers[s[-1]]
    if s.isdigit():
        return int(s)
    return None


class GiveawayEntryView(discord.ui.View):
    """Live-updating entry button for an active giveaway."""

    def __init__(self, prize: str, is_quickdrop: bool):
        super().__init__(timeout=None)   # we manage lifetime manually
        self.prize       = prize
        self.is_quickdrop = is_quickdrop
        self.entrants: set = set()       # set of user IDs
        self._lock = asyncio.Lock()

        # Build the initial button
        self._entry_button = discord.ui.Button(
            label="🎉 Enter — 0 entries",
            style=discord.ButtonStyle.primary,
            custom_id=f"giveaway:enter:{id(self)}",
        )
        self._entry_button.callback = self._on_enter
        self.add_item(self._entry_button)

    async def _on_enter(self, interaction: discord.Interaction):
        async with self._lock:
            if interaction.user.id in self.entrants:
                await interaction.response.send_message(
                    "✅ You're already entered!", ephemeral=True
                )
                return
            self.entrants.add(interaction.user.id)
            count = len(self.entrants)
            self._entry_button.label = f"🎉 Enter — {count} {'entry' if count == 1 else 'entries'}"

        # Edit the message to reflect the new count
        await interaction.response.edit_message(view=self)
        logger.info(
            "Giveaway entry: %s (%d) — total %d",
            interaction.user, interaction.user.id, count
        )

    def disable(self):
        """Disable the button when the giveaway ends."""
        self._entry_button.disabled = True
        self._entry_button.style   = discord.ButtonStyle.secondary


class GiveawaysCog(commands.Cog, name="Giveaways"):
    """Giveaway and quickdrop commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Store final entrant sets keyed by message ID for /rerollgiveaway
        self._finished: dict = {}   # message_id → list[int] of user IDs

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

        end_ts   = int(datetime.now(timezone.utc).timestamp()) + seconds
        kind_tag = "⚡ QUICKDROP" if is_quickdrop else "🎉 GIVEAWAY"
        color    = discord.Color.orange() if is_quickdrop else discord.Color.blue()

        embed = discord.Embed(
            title=f"{kind_tag} — {prize}",
            description=(
                f"Click **🎉 Enter** below to join!\n\n"
                f"**Prize:** {prize}\n"
                f"**Winners:** {num_winners}\n"
                f"**Ends:** <t:{end_ts}:R> (<t:{end_ts}:t>)"
            ),
            color=color,
            timestamp=datetime.fromtimestamp(end_ts, tz=timezone.utc),
        )
        embed.set_footer(text=f"Hosted by {interaction.user.display_name} • Ends at")

        view = GiveawayEntryView(prize, is_quickdrop)
        msg  = await interaction.channel.send(embed=embed, view=view)

        await interaction.followup.send(
            embed=discord.Embed(
                description=f"✅ {'Quickdrop' if is_quickdrop else 'Giveaway'} started in {interaction.channel.mention}!",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

        logger.info(
            "%s started by %s in %s — prize: %s, duration: %ds, winners: %d",
            kind_tag, interaction.user, interaction.guild.name, prize, seconds, num_winners,
        )

        # ── Wait for the giveaway to end ──────────────────────────────────────
        await asyncio.sleep(seconds)

        # ── Collect entrants from the view ────────────────────────────────────
        entrant_ids = list(view.entrants)
        self._finished[msg.id] = entrant_ids   # save for possible reroll

        # Resolve to Member objects (best effort)
        entrants = []
        for uid in entrant_ids:
            m = interaction.guild.get_member(uid)
            if m and not m.bot:
                entrants.append(m)

        # ── Pick winners ──────────────────────────────────────────────────────
        actual_winners = min(num_winners, len(entrants))
        if entrants:
            winners        = random.sample(entrants, actual_winners)
            winner_mentions = ", ".join(w.mention for w in winners)
            win_text       = f"🏆 **Winner{'s' if actual_winners > 1 else ''}:** {winner_mentions}"
        else:
            winners        = []
            winner_mentions = ""
            win_text       = "😢 Nobody entered — no winners this time!"

        # ── Edit original embed to ended state ────────────────────────────────
        view.disable()
        ended_embed = discord.Embed(
            title=f"{kind_tag} ENDED — {prize}",
            description=(
                f"**Prize:** {prize}\n"
                f"**Total Entries:** {len(entrant_ids)}\n\n"
                f"{win_text}"
            ),
            color=discord.Color.dark_grey(),
            timestamp=datetime.now(timezone.utc),
        )
        ended_embed.set_footer(text=f"Hosted by {interaction.user.display_name} • Ended")

        try:
            await msg.edit(embed=ended_embed, view=view)
        except discord.NotFound:
            logger.warning("Giveaway message was deleted before it could be updated.")
            return

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
                allowed_mentions=discord.AllowedMentions(users=True),
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

    # ── /rerollgiveaway ───────────────────────────────────────────────────────
    @app_commands.command(
        name="rerollgiveaway",
        description="Reroll new winner(s) for an ended giveaway or quickdrop",
    )
    @app_commands.describe(
        message_id="ID of the ended giveaway message to reroll",
        winners="How many new winners to pick (default 1)",
    )
    async def rerollgiveaway(
        self,
        interaction: discord.Interaction,
        message_id: str,
        winners: app_commands.Range[int, 1, 10] = 1,
    ):
        await interaction.response.defer()

        if not is_authorized(interaction.user, interaction.guild, "giveaway"):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="You must be **Admin** or above (or granted `giveaway` access) to reroll.",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                ),
                ephemeral=True,
            )
            return

        try:
            mid = int(message_id)
        except ValueError:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Invalid Message ID",
                    description="Please provide a valid message ID.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        entrant_ids = self._finished.get(mid)
        if not entrant_ids:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Not Found",
                    description=(
                        f"No entry data found for message `{mid}`.\n"
                        "Reroll only works for giveaways run in this bot session."
                    ),
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )
            return

        # Resolve members
        entrants = []
        for uid in entrant_ids:
            m = interaction.guild.get_member(uid)
            if m and not m.bot:
                entrants.append(m)

        if not entrants:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="😢 No Entrants",
                    description="No valid entrants found to reroll from.",
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )
            return

        actual_winners = min(winners, len(entrants))
        picked         = random.sample(entrants, actual_winners)
        winner_mentions = ", ".join(w.mention for w in picked)

        try:
            msg = await interaction.channel.fetch_message(mid)
            jump = msg.jump_url
        except (discord.NotFound, discord.Forbidden):
            jump = None

        ref_text = f"*(Rerolled from [this message]({jump}))*" if jump else ""

        await interaction.followup.send(
            content=winner_mentions,
            embed=discord.Embed(
                title=f"🔁 Reroll — New Winner{'s' if actual_winners > 1 else ''}!",
                description=(
                    f"🏆 {winner_mentions}\n\n"
                    f"Congratulations! Please contact {interaction.user.mention} to claim your prize.\n"
                    f"{ref_text}"
                ),
                color=discord.Color.gold(),
                timestamp=datetime.now(timezone.utc),
            ),
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        logger.info(
            "Reroll by %s in %s: msg=%s, winners=%s",
            interaction.user, interaction.guild.name, mid, [w.id for w in picked],
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawaysCog(bot))
