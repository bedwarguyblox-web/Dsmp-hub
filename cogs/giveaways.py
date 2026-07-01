"""
giveaways.py — /giveaway, /quickdrop, /rerollgiveaway, /giveawayset commands.
Button-based entry with live entry count displayed on the button.

'ends' parameter accepts:
  • A duration     — 30s, 5m, 2h, 1d   (draws after that time)
  • A member goal  — "500 members"      (draws when the SERVER reaches 500 members)
"""

import asyncio
import random
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.permissions import is_authorized
from utils.database import get_guild_config, set_guild_config

logger = logging.getLogger(__name__)

# How often (seconds) to check server member count for member-goal giveaways
MEMBER_GOAL_POLL_INTERVAL = 10
# Hard cap: member-goal giveaways expire after 30 days even if goal isn't reached
MEMBER_GOAL_MAX_SECONDS = 30 * 24 * 3600


def parse_ends(s: str):
    """
    Parse the 'ends' field.  Returns one of:
      ('time',    int)  — end after this many seconds
      ('members', int)  — end when the SERVER has this many total members
      None              — invalid input
    """
    s = s.strip().lower()

    # Member-goal: "500 members" or "500 member"
    for suffix in (" members", " member"):
        if s.endswith(suffix):
            num = s[: -len(suffix)].strip()
            if num.isdigit() and int(num) >= 1:
                return ("members", int(num))
            return None

    # Time-based
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if len(s) >= 2 and s[-1] in multipliers and s[:-1].isdigit():
        return ("time", int(s[:-1]) * multipliers[s[-1]])
    if s.isdigit():
        return ("time", int(s))

    return None


class GiveawayEntryView(discord.ui.View):
    """Live-updating entry button for an active giveaway."""

    def __init__(self, prize: str, is_quickdrop: bool):
        super().__init__(timeout=None)
        self.prize         = prize
        self.is_quickdrop  = is_quickdrop
        self.entrants: set = set()
        self._lock         = asyncio.Lock()

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
            self._entry_button.label = (
                f"🎉 Enter — {count} {'entry' if count == 1 else 'entries'}"
            )

        await interaction.response.edit_message(view=self)
        logger.info(
            "Giveaway entry: %s (%d) — total %d",
            interaction.user, interaction.user.id, count,
        )

    def disable(self):
        self._entry_button.disabled = True
        self._entry_button.style   = discord.ButtonStyle.secondary


class GiveawaysCog(commands.Cog, name="Giveaways"):
    """Giveaway and quickdrop commands."""

    def __init__(self, bot: commands.Bot):
        self.bot      = bot
        self._finished: dict = {}   # message_id → list[int] of user IDs

    # ── Config helpers ────────────────────────────────────────────────────────
    def _ping_role(self, guild: discord.Guild) -> discord.Role | None:
        """Return the configured giveaway ping role, or None."""
        raw = get_guild_config(guild.id, "giveaway_ping_role_id")
        if raw:
            return guild.get_role(int(raw))
        return None

    # ── Core runner ───────────────────────────────────────────────────────────
    async def _run(
        self,
        interaction: discord.Interaction,
        prize: str,
        ends_str: str,
        num_winners: int,
        is_quickdrop: bool,
    ):
        await interaction.response.defer(ephemeral=True)

        cmd_name = "quickdrop" if is_quickdrop else "giveaway"

        if not is_authorized(interaction.user, interaction.guild, cmd_name):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description=(
                        f"You must be **Admin** or above (or granted `{cmd_name}` access) "
                        "to use this command."
                    ),
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                ),
                ephemeral=True,
            )
            return

        parsed = parse_ends(ends_str)
        if parsed is None:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Invalid 'ends' Value",
                    description=(
                        "Use a **duration** (`30s`, `5m`, `2h`, `1d`) "
                        "or a **server member goal** (`500 members`).\n"
                        "Minimum duration: 5 seconds — maximum: 7 days."
                    ),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        end_mode, end_value = parsed

        if end_mode == "time" and (end_value < 5 or end_value > 604800):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Invalid Duration",
                    description="Minimum **5 seconds**, maximum **7 days**.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        kind_tag = "⚡ QUICKDROP" if is_quickdrop else "🎉 GIVEAWAY"
        color    = discord.Color.orange() if is_quickdrop else discord.Color.blue()
        guild    = interaction.guild

        # ── Build embed ───────────────────────────────────────────────────────
        if end_mode == "time":
            end_ts     = int(datetime.now(timezone.utc).timestamp()) + end_value
            end_line   = f"**Ends:** <t:{end_ts}:R> (<t:{end_ts}:t>)"
            footer_sfx = "Ends at"
            embed_ts   = datetime.fromtimestamp(end_ts, tz=timezone.utc)
        else:
            current    = guild.member_count or 0
            end_line   = (
                f"**Member Goal:** {end_value:,} server members\n"
                f"**Current Members:** {current:,}"
            )
            footer_sfx = "Draws at member goal"
            embed_ts   = datetime.now(timezone.utc)

        embed = discord.Embed(
            title=f"{kind_tag} — {prize}",
            description=(
                f"Click **🎉 Enter** below to join!\n\n"
                f"**Prize:** {prize}\n"
                f"**Winners:** {num_winners}\n"
                f"{end_line}"
            ),
            color=color,
            timestamp=embed_ts,
        )
        embed.set_footer(
            text=f"Hosted by {interaction.user.display_name} • {footer_sfx}"
        )

        # ── Post giveaway — ping the role if configured ───────────────────────
        ping_role = self._ping_role(guild)
        view      = GiveawayEntryView(prize, is_quickdrop)

        if ping_role:
            msg = await interaction.channel.send(
                content=ping_role.mention,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        else:
            msg = await interaction.channel.send(embed=embed, view=view)

        await interaction.followup.send(
            embed=discord.Embed(
                description=(
                    f"✅ {'Quickdrop' if is_quickdrop else 'Giveaway'} started in "
                    f"{interaction.channel.mention}!"
                ),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

        logger.info(
            "%s started by %s in %s — prize: %s, mode: %s=%s, winners: %d",
            kind_tag, interaction.user, guild.name,
            prize, end_mode, end_value, num_winners,
        )

        # ── Wait for the end condition ─────────────────────────────────────────
        if end_mode == "time":
            await asyncio.sleep(end_value)
        else:
            # Poll server member count until goal is reached (hard cap: 30 days)
            elapsed = 0
            while elapsed < MEMBER_GOAL_MAX_SECONDS:
                current = guild.member_count or 0
                if current >= end_value:
                    break
                await asyncio.sleep(MEMBER_GOAL_POLL_INTERVAL)
                elapsed += MEMBER_GOAL_POLL_INTERVAL

        # ── Collect entrants ──────────────────────────────────────────────────
        entrant_ids = list(view.entrants)
        self._finished[msg.id] = entrant_ids

        entrants = []
        for uid in entrant_ids:
            m = guild.get_member(uid)
            if m and not m.bot:
                entrants.append(m)

        # ── Pick winners ──────────────────────────────────────────────────────
        actual_winners = min(num_winners, len(entrants))
        if entrants:
            winners         = random.sample(entrants, actual_winners)
            winner_mentions = ", ".join(w.mention for w in winners)
            win_text        = f"🏆 **Winner{'s' if actual_winners > 1 else ''}:** {winner_mentions}"
        else:
            winners         = []
            winner_mentions = ""
            win_text        = "😢 Nobody entered — no winners this time!"

        # If member-goal mode, show final count in ended embed
        extra = ""
        if end_mode == "members":
            extra = f"**Server Members at Draw:** {guild.member_count:,}\n"

        # ── Edit original embed to ended state ────────────────────────────────
        view.disable()
        ended_embed = discord.Embed(
            title=f"{kind_tag} ENDED — {prize}",
            description=(
                f"**Prize:** {prize}\n"
                f"**Total Entries:** {len(entrant_ids)}\n"
                f"{extra}\n"
                f"{win_text}"
            ),
            color=discord.Color.dark_grey(),
            timestamp=datetime.now(timezone.utc),
        )
        ended_embed.set_footer(
            text=f"Hosted by {interaction.user.display_name} • Ended"
        )

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
                    description=(
                        f"Nobody entered the "
                        f"{'quickdrop' if is_quickdrop else 'giveaway'} for **{prize}**."
                    ),
                    color=discord.Color.dark_grey(),
                    timestamp=datetime.now(timezone.utc),
                )
            )

    # ── /giveaway ─────────────────────────────────────────────────────────────
    @app_commands.command(name="giveaway", description="Start a giveaway in this channel")
    @app_commands.describe(
        prize="What you're giving away",
        ends="Duration (30s, 5m, 2h, 1d) OR server member goal (500 members)",
        winners="Number of winners (1–10, default 1)",
    )
    async def giveaway(
        self,
        interaction: discord.Interaction,
        prize: str,
        ends: str,
        winners: app_commands.Range[int, 1, 10] = 1,
    ):
        await self._run(interaction, prize, ends, winners, is_quickdrop=False)

    # ── /quickdrop ────────────────────────────────────────────────────────────
    @app_commands.command(name="quickdrop", description="Start a flash quickdrop in this channel")
    @app_commands.describe(
        prize="What you're dropping",
        ends="Duration (30s, 5m, 2h) OR server member goal (100 members)",
        winners="Number of winners (1–10, default 1)",
    )
    async def quickdrop(
        self,
        interaction: discord.Interaction,
        prize: str,
        ends: str,
        winners: app_commands.Range[int, 1, 10] = 1,
    ):
        await self._run(interaction, prize, ends, winners, is_quickdrop=True)

    # ── /giveawayset ──────────────────────────────────────────────────────────
    giveawayset = app_commands.Group(
        name="giveawayset",
        description="Configure giveaway settings (Admin only)",
    )

    @giveawayset.command(name="ping", description="Set the role to ping when a giveaway starts")
    @app_commands.describe(role="Role to mention — leave empty to clear the ping")
    async def giveawayset_ping(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
    ):
        if not is_authorized(interaction.user, interaction.guild, "giveaway"):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="You must be **Admin** or above to configure giveaway settings.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        if role:
            set_guild_config(interaction.guild.id, "giveaway_ping_role_id", str(role.id))
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="✅ Giveaway Ping Set",
                    description=(
                        f"{role.mention} will now be pinged whenever a giveaway or quickdrop starts.\n\n"
                        "**Tip:** Make sure this role has **Allow anyone to @mention this role** "
                        "enabled, or the bot will need a role above it."
                    ),
                    color=discord.Color.green(),
                    timestamp=datetime.now(timezone.utc),
                ),
                ephemeral=True,
            )
        else:
            set_guild_config(interaction.guild.id, "giveaway_ping_role_id", "")
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="✅ Giveaway Ping Cleared",
                    description="No role will be pinged when giveaways start.",
                    color=discord.Color.green(),
                    timestamp=datetime.now(timezone.utc),
                ),
                ephemeral=True,
            )

    @giveawayset.command(name="status", description="Show current giveaway configuration")
    async def giveawayset_status(self, interaction: discord.Interaction):
        if not is_authorized(interaction.user, interaction.guild, "giveaway"):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        ping_role = self._ping_role(interaction.guild)
        embed = discord.Embed(
            title="⚙️ Giveaway Settings",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Ping Role",
            value=ping_role.mention if ping_role else "*Not set — use `/giveawayset ping`*",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

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

        actual_winners  = min(winners, len(entrants))
        picked          = random.sample(entrants, actual_winners)
        winner_mentions = ", ".join(w.mention for w in picked)

        try:
            orig = await interaction.channel.fetch_message(mid)
            jump = orig.jump_url
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
