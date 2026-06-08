"""
tickets.py — DM-based ticket system with panel buttons.

Flow:
  1. /ticketpanel   — posts a message with 6 category buttons in a channel.
  2. User clicks a button → bot DMs them intake questions for that category.
  3. User answers (plain DM messages) → bot collects answers, marks ticket 'open',
     posts a staff notification embed in TICKET_STAFF_CHANNEL_ID.
  4. Staff reply via:  !message <content>   in the staff channel (or DM with the bot).
     Bot forwards the message to the user's DM.
  5. User replies via: !message <content>   in their DM with the bot.
     Bot forwards the message to the staff channel.
  6. /ticketclose <ticket_id>  — closes a ticket (staff/owner only).
  7. /ticketlist               — lists all open tickets (staff/owner only).
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.database import (
    open_ticket, get_open_ticket_for_user, get_ticket,
    update_ticket, close_ticket, get_all_open_tickets, log_staff_action,
)
from utils.permissions import is_authorized, is_owner, CONFIG

logger = logging.getLogger(__name__)

# ── Category definitions ──────────────────────────────────────────────────────
# Each entry: (label, emoji, custom_id_suffix, questions[])
TICKET_CATEGORIES = [
    (
        "General Support",
        "🎫",
        "general",
        [
            "What is your issue or question?",
            "Have you already tried anything to resolve it? If yes, what?",
        ],
    ),
    (
        "Partnerships",
        "🤝",
        "partnerships",
        [
            "What is your server/community name?",
            "How many members do you currently have?",
            "What kind of partnership are you looking for?",
        ],
    ),
    (
        "Middleman",
        "⚖️",
        "middleman",
        [
            "What item(s) or service(s) are being traded?",
            "What is the agreed trade value / price?",
            "Who are the two parties involved? (mention both users)",
        ],
    ),
    (
        "Spawners Buy/Sell",
        "🐣",
        "spawners",
        [
            "Are you buying or selling?",
            "Which spawner type(s) and quantity?",
            "What is your asking/offer price?",
        ],
    ),
    (
        "Farm Buy",
        "🌾",
        "farmbuy",
        [
            "What type of farm are you looking to buy?",
            "What is your budget or price range?",
            "Any specific requirements for the farm?",
        ],
    ),
    (
        "Claim Giveaway",
        "🎁",
        "giveaway",
        [
            "Which giveaway are you claiming? (link or name)",
            "What did you win?",
            "Please provide proof you won (screenshot link or description).",
        ],
    ),
]

# Map custom_id suffix → full category tuple
_CAT_MAP = {c[2]: c for c in TICKET_CATEGORIES}

# In-memory intake state: user_id → {ticket_id, category, questions, answers, q_index, guild_id}
_intake: dict[int, dict] = {}


def _make_ticket_id() -> str:
    return f"TKT-{uuid.uuid4().hex[:6].upper()}"


def _staff_channel(bot: commands.Bot, guild_id: int) -> Optional[discord.TextChannel]:
    ch_id = CONFIG.get("TICKET_STAFF_CHANNEL_ID")
    if not ch_id:
        return None
    guild = bot.get_guild(guild_id)
    if guild:
        return guild.get_channel(ch_id)
    return None


def _ticket_staff_role(guild: discord.Guild) -> Optional[discord.Role]:
    rid = CONFIG.get("TICKET_STAFF_ROLE_ID")
    if rid:
        return guild.get_role(rid)
    return None


# ── Panel View (persistent) ───────────────────────────────────────────────────

class TicketPanelView(discord.ui.View):
    """
    A persistent view (timeout=None) so buttons still work after bot restarts.
    Each button has a unique custom_id of the form 'ticket_open_<suffix>'.
    """

    def __init__(self):
        super().__init__(timeout=None)
        for label, emoji, suffix, _ in TICKET_CATEGORIES:
            self.add_item(TicketButton(label=label, emoji=emoji, suffix=suffix))


class TicketButton(discord.ui.Button):
    def __init__(self, label: str, emoji: str, suffix: str):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label=label,
            emoji=emoji,
            custom_id=f"ticket_open_{suffix}",
        )
        self.suffix = suffix

    async def callback(self, interaction: discord.Interaction):
        user  = interaction.user
        guild = interaction.guild

        # Reject bots
        if user.bot:
            return

        # Check for existing open ticket
        existing = get_open_ticket_for_user(user.id, guild.id)
        if existing:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="⚠️ Existing Ticket Open",
                    description=(
                        f"You already have an open ticket (`{existing['ticket_id']}`).\n"
                        "Please wait for it to be resolved or ask staff to close it first."
                    ),
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )
            return

        cat = _CAT_MAP.get(self.suffix)
        if not cat:
            await interaction.response.send_message("Unknown ticket category.", ephemeral=True)
            return

        label, emoji, suffix, questions = cat
        ticket_id = _make_ticket_id()

        # Attempt to DM the user
        try:
            dm = await user.create_dm()
            intro = discord.Embed(
                title=f"{emoji} {label} — Ticket {ticket_id}",
                description=(
                    "Thanks for reaching out! I'll ask you a few quick questions.\n"
                    "**Please reply with `!message <your answer>` for each question.**\n\n"
                    f"**Question 1/{len(questions)}:** {questions[0]}"
                ),
                color=discord.Color.blurple(),
                timestamp=datetime.now(timezone.utc),
            )
            await dm.send(embed=intro)
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="❌ DMs Closed",
                    description=(
                        "I couldn't DM you. Please **open your DMs** for this server "
                        "and try again.\n*(User Settings → Privacy & Safety → Allow DMs from server members)*"
                    ),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        # Register ticket in DB + memory
        open_ticket(ticket_id, user.id, guild.id, label)
        _intake[user.id] = {
            "ticket_id": ticket_id,
            "category":  label,
            "questions": questions,
            "answers":   [],
            "q_index":   0,
            "guild_id":  guild.id,
        }

        await interaction.response.send_message(
            embed=discord.Embed(
                title="📬 Ticket Created",
                description="I've sent you a DM to collect a few details. Check your messages!",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


# ── Tickets Cog ───────────────────────────────────────────────────────────────

class TicketsCog(commands.Cog, name="Tickets"):
    """DM-based ticket system."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Register the persistent view so buttons survive restarts
        bot.add_view(TicketPanelView())

    # ── /ticketpanel ──────────────────────────────────────────────────────────
    @app_commands.command(
        name="ticketpanel",
        description="Post the ticket panel with category buttons (Owner/Admin only)",
    )
    @app_commands.describe(channel="Channel to post the panel in (defaults to current channel)")
    async def ticketpanel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if not is_authorized(interaction.user, interaction.guild, "ticketpanel"):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="You must be **Admin** or above to post the ticket panel.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        target = channel or interaction.channel

        embed = discord.Embed(
            title="🎫 Support Tickets",
            description=(
                "Need help? Click the button that matches your request below.\n"
                "The bot will DM you and collect your details before notifying staff.\n\n"
                "**Categories:**\n"
                + "\n".join(f"{e} **{lbl}**" for lbl, e, _, __ in TICKET_CATEGORIES)
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="One open ticket per user at a time.")

        await target.send(embed=embed, view=TicketPanelView())
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Panel Posted",
                description=f"Ticket panel sent to {target.mention}.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    # ── /ticketclose ──────────────────────────────────────────────────────────
    @app_commands.command(
        name="ticketclose",
        description="Close an open ticket by its ID (Staff only)",
    )
    @app_commands.describe(ticket_id="The ticket ID to close, e.g. TKT-AB12CD")
    async def ticketclose(
        self,
        interaction: discord.Interaction,
        ticket_id: str,
    ):
        await interaction.response.defer(ephemeral=True)

        if not is_authorized(interaction.user, interaction.guild, "ticketclose"):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="You must be **Admin** or above to close tickets.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        ticket_id = ticket_id.upper().strip()
        row = get_ticket(ticket_id)

        if not row:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Not Found",
                    description=f"No ticket with ID `{ticket_id}` exists.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        if row["status"] == "closed":
            await interaction.followup.send(
                embed=discord.Embed(
                    title="ℹ️ Already Closed",
                    description=f"Ticket `{ticket_id}` is already closed.",
                    color=discord.Color.blue(),
                ),
                ephemeral=True,
            )
            return

        close_ticket(ticket_id)
        # Remove from intake memory if still there
        _intake.pop(row["user_id"], None)

        # Notify the user
        user = self.bot.get_user(row["user_id"])
        if user:
            try:
                dm = await user.create_dm()
                await dm.send(embed=discord.Embed(
                    title="🔒 Ticket Closed",
                    description=(
                        f"Your ticket **{ticket_id}** ({row['category']}) has been closed by staff.\n"
                        "If you need further help, feel free to open a new ticket."
                    ),
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                ))
            except discord.Forbidden:
                pass

        log_staff_action("ticket_close", interaction.user.id, interaction.guild.id,
                         details=f"Closed {ticket_id}")

        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Ticket Closed",
                description=f"Ticket `{ticket_id}` has been closed.",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            ),
            ephemeral=True,
        )

    # ── /ticketlist ───────────────────────────────────────────────────────────
    @app_commands.command(
        name="ticketlist",
        description="List all currently open tickets (Staff only)",
    )
    async def ticketlist(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not is_authorized(interaction.user, interaction.guild, "ticketlist"):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="You must be **Admin** or above to view tickets.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        rows = get_all_open_tickets(interaction.guild.id)
        embed = discord.Embed(
            title="📋 Open Tickets",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )

        if not rows:
            embed.description = "No open tickets right now. ✅"
        else:
            lines = []
            for row in rows:
                user = self.bot.get_user(row["user_id"])
                uname = user.mention if user else f"ID:{row['user_id']}"
                status = row["status"]
                opened = str(row["opened_at"])[:16]
                lines.append(
                    f"**`{row['ticket_id']}`** — {uname}\n"
                    f"  Category: {row['category']} | Status: `{status}` | Opened: {opened}"
                )
            embed.description = "\n\n".join(lines)
            embed.set_footer(text=f"{len(rows)} open ticket(s)")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── Message listener (DM relay + intake) ──────────────────────────────────
    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # ── DM from a user ───────────────────────────────────────────────────
        if isinstance(message.channel, discord.DMChannel):
            await self._handle_user_dm(message)
            return

        # ── Staff channel message relay ───────────────────────────────────────
        staff_ch_id = CONFIG.get("TICKET_STAFF_CHANNEL_ID")
        if staff_ch_id and message.channel.id == staff_ch_id:
            await self._handle_staff_message(message)

    # ── Intake + user-side relay ──────────────────────────────────────────────

    async def _handle_user_dm(self, message: discord.Message):
        user_id = message.author.id

        # ── Intake flow ───────────────────────────────────────────────────────
        if user_id in _intake:
            state = _intake[user_id]

            # Must use !message prefix
            if not message.content.startswith("!message "):
                await message.channel.send(
                    embed=discord.Embed(
                        description=(
                            "Please reply using:\n`!message <your answer>`\n\n"
                            f"**Question {state['q_index']+1}/{len(state['questions'])}:** "
                            f"{state['questions'][state['q_index']]}"
                        ),
                        color=discord.Color.yellow(),
                    )
                )
                return

            answer = message.content[len("!message "):].strip()
            if not answer:
                await message.channel.send("Answer cannot be empty — please try again.")
                return

            state["answers"].append(answer)
            state["q_index"] += 1

            if state["q_index"] < len(state["questions"]):
                # Ask next question
                next_q = state["questions"][state["q_index"]]
                await message.channel.send(
                    embed=discord.Embed(
                        description=(
                            f"✅ Got it!\n\n"
                            f"**Question {state['q_index']+1}/{len(state['questions'])}:** {next_q}"
                        ),
                        color=discord.Color.blurple(),
                    )
                )
            else:
                # All questions answered — finalise
                await self._finalise_intake(message.author, state)
                _intake.pop(user_id, None)
            return

        # ── Relay: open ticket exists, user sends !message ────────────────────
        # Find the user's open ticket across all guilds the bot shares with them
        guild_id = None
        for guild in self.bot.guilds:
            if guild.get_member(user_id):
                row = get_open_ticket_for_user(user_id, guild.id)
                if row and row["status"] == "open":
                    guild_id = guild.id
                    ticket_row = row
                    break

        if guild_id is None:
            return  # no open ticket; ignore

        if not message.content.startswith("!message "):
            await message.channel.send(
                embed=discord.Embed(
                    description=(
                        "To send a message to staff, use:\n`!message <your message>`"
                    ),
                    color=discord.Color.yellow(),
                )
            )
            return

        content = message.content[len("!message "):].strip()
        if not content:
            await message.channel.send("Message cannot be empty.")
            return

        staff_ch = _staff_channel(self.bot, guild_id)
        if not staff_ch:
            await message.channel.send(
                embed=discord.Embed(
                    description="⚠️ Staff channel not configured. Please contact an admin.",
                    color=discord.Color.red(),
                )
            )
            return

        relay_embed = discord.Embed(
            title=f"📨 User Message — {ticket_row['ticket_id']}",
            description=content,
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        relay_embed.set_author(
            name=f"{message.author} (ID: {user_id})",
            icon_url=message.author.display_avatar.url,
        )
        relay_embed.set_footer(text=f"Category: {ticket_row['category']}")
        await staff_ch.send(embed=relay_embed)

        await message.channel.send(
            embed=discord.Embed(
                description="✅ Your message has been sent to staff.",
                color=discord.Color.green(),
            )
        )

    async def _finalise_intake(self, user: discord.User, state: dict):
        """Store answers, mark ticket open, DM user confirmation, notify staff."""
        ticket_id = state["ticket_id"]
        category  = state["category"]
        questions = state["questions"]
        answers   = state["answers"]
        guild_id  = state["guild_id"]

        answers_json = json.dumps(list(zip(questions, answers)))
        update_ticket(ticket_id, status="open", answers=answers_json)

        # ── Confirm to user ───────────────────────────────────────────────────
        confirm = discord.Embed(
            title="✅ Ticket Submitted",
            description=(
                f"**Ticket ID:** `{ticket_id}`\n"
                "Your ticket has been sent to staff. They'll reach out to you via this DM soon.\n\n"
                "To send additional messages to staff, use:\n`!message <your message>`"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        for q, a in zip(questions, answers):
            confirm.add_field(name=q, value=a[:512], inline=False)
        try:
            await user.send(embed=confirm)
        except discord.Forbidden:
            pass

        # ── Notify staff channel ──────────────────────────────────────────────
        staff_ch = _staff_channel(self.bot, guild_id)
        if not staff_ch:
            logger.warning("Ticket %s: TICKET_STAFF_CHANNEL_ID not set in config.json", ticket_id)
            return

        guild       = self.bot.get_guild(guild_id)
        staff_role  = _ticket_staff_role(guild) if guild else None
        ping        = staff_role.mention if staff_role else ""

        notify = discord.Embed(
            title=f"🎫 New Ticket — {ticket_id}",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        notify.set_author(
            name=f"{user} (ID: {user.id})",
            icon_url=user.display_avatar.url,
        )
        notify.add_field(name="Category", value=category, inline=True)
        notify.add_field(name="Ticket ID", value=f"`{ticket_id}`", inline=True)
        for q, a in zip(questions, answers):
            notify.add_field(name=q, value=a[:512], inline=False)
        notify.set_footer(text="Reply with  !message <content>  to send a message to the user.")

        sent = await staff_ch.send(content=ping if ping else None, embed=notify)
        update_ticket(ticket_id, staff_msg_id=sent.id)
        log_staff_action("ticket_open", user.id, guild_id, details=f"{ticket_id} | {category}")

    # ── Staff-side relay ──────────────────────────────────────────────────────

    async def _handle_staff_message(self, message: discord.Message):
        """
        Staff type  !message <content>  in the ticket staff channel.
        The bot identifies which ticket to reply to via the replied-to message's
        embed footer (ticket ID), or falls back to the most recently opened ticket.
        """
        if not message.content.startswith("!message "):
            return

        content = message.content[len("!message "):].strip()
        if not content:
            return

        guild_id  = message.guild.id
        ticket_id = None

        # Try to extract ticket ID from a replied-to staff notification
        if message.reference and message.reference.message_id:
            try:
                ref = await message.channel.fetch_message(message.reference.message_id)
                if ref.embeds:
                    footer = ref.embeds[0].footer.text or ""
                    # Footer format: "Reply with  !message <content>  ..."  but title has the ID
                    title = ref.embeds[0].title or ""
                    if "TKT-" in title:
                        ticket_id = title.split("TKT-")[1][:6]
                        ticket_id = f"TKT-{ticket_id}"
                    # Also check description / author
                    for field in ref.embeds[0].fields:
                        if field.name == "Ticket ID":
                            ticket_id = field.value.strip("`")
                            break
            except Exception:
                pass

        # Fallback: look for a ticket ID anywhere in the message itself (#TKT-XXXXXX)
        if not ticket_id:
            import re
            m = re.search(r"TKT-[A-F0-9]{6}", message.content.upper())
            if m:
                ticket_id = m.group(0)

        # Fallback: most recent open ticket in this guild
        if not ticket_id:
            rows = get_all_open_tickets(guild_id)
            if not rows:
                await message.reply(
                    embed=discord.Embed(
                        description="❌ No open tickets found in this server.",
                        color=discord.Color.red(),
                    ),
                    mention_author=False,
                )
                return
            ticket_id = rows[0]["ticket_id"]

        row = get_ticket(ticket_id)
        if not row or row["status"] == "closed":
            await message.reply(
                embed=discord.Embed(
                    description=f"❌ Ticket `{ticket_id}` not found or already closed.",
                    color=discord.Color.red(),
                ),
                mention_author=False,
            )
            return

        # DM the user
        user = self.bot.get_user(row["user_id"])
        if not user:
            try:
                user = await self.bot.fetch_user(row["user_id"])
            except Exception:
                pass

        if not user:
            await message.reply(
                embed=discord.Embed(
                    description="❌ Could not find the ticket's user.",
                    color=discord.Color.red(),
                ),
                mention_author=False,
            )
            return

        relay = discord.Embed(
            title=f"💬 Staff Reply — {ticket_id}",
            description=content,
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        relay.set_author(
            name=f"Staff: {message.author.display_name}",
            icon_url=message.author.display_avatar.url,
        )
        relay.set_footer(text=f"Category: {row['category']} | To reply: !message <content>")

        try:
            dm = await user.create_dm()
            await dm.send(embed=relay)
            # Confirm in staff channel
            await message.add_reaction("✅")
        except discord.Forbidden:
            await message.reply(
                embed=discord.Embed(
                    description="⚠️ Could not DM the user — their DMs may be closed.",
                    color=discord.Color.orange(),
                ),
                mention_author=False,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketsCog(bot))
