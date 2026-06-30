"""
tickets.py — Channel-based ticket system.

Flow:
  1. /ticket panel [channel]  — post the panel embed with category buttons
  2. User clicks a button     → private channel created in the configured category
                                with the user + staff roles having access
  3. An intro embed is posted with a 🔒 Close Ticket button
  4. Staff and the user converse normally in the channel
  5. Closing (button or /ticket close):
     — logs a summary to the configured log channel
     — deletes the ticket channel after 5 seconds

Config commands (Admin+):
  /ticket setstaffroles roles:    — which roles can see ticket channels
  /ticket setchannel category:    — Discord category for new ticket channels
  /ticket setlogs channel:        — where close summaries are posted
  /ticket setpanel title: desc:   — customise panel embed text
  /ticket addtype label: emoji:   — add a category button to the panel (max 5)
  /ticket removetype label:       — remove a category button
  /ticket types                   — list configured categories

Staff commands (Admin+):
  /ticket panel [channel]         — post the ticket panel
  /ticket close [reason]          — close the current ticket channel
  /ticket add user                — add a user to the current ticket
  /ticket remove user             — remove a user from the current ticket
  /ticket list                    — list all open tickets
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
    open_ticket, get_open_ticket_for_user, get_ticket_by_channel,
    update_ticket, close_ticket, get_all_open_tickets, log_staff_action,
    get_guild_config, set_guild_config,
)
from utils.permissions import is_authorized

logger = logging.getLogger(__name__)

# ── Guild config helpers ───────────────────────────────────────────────────────

DEFAULT_CATEGORIES = [{"label": "Open Ticket", "emoji": "🎫"}]


def _cfg(guild_id: int, key: str) -> Optional[str]:
    return get_guild_config(guild_id, f"ticket_{key}")


def _set_cfg(guild_id: int, key: str, value: str):
    set_guild_config(guild_id, f"ticket_{key}", value)


def _get_staff_role_ids(guild_id: int) -> list[int]:
    raw = _cfg(guild_id, "staff_roles")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _get_categories(guild_id: int) -> list[dict]:
    raw = _cfg(guild_id, "categories")
    if not raw:
        return list(DEFAULT_CATEGORIES)
    try:
        cats = json.loads(raw)
        return cats if cats else list(DEFAULT_CATEGORIES)
    except Exception:
        return list(DEFAULT_CATEGORIES)


def _next_ticket_number(guild_id: int) -> int:
    raw = _cfg(guild_id, "counter") or "0"
    try:
        n = int(raw) + 1
    except ValueError:
        n = 1
    _set_cfg(guild_id, "counter", str(n))
    return n


def _build_panel_embed(guild_id: int) -> discord.Embed:
    title = _cfg(guild_id, "panel_title") or "🎫 Support Tickets"
    desc  = _cfg(guild_id, "panel_description") or (
        "Need help or have a question? Click the button below to open a ticket.\n"
        "Our staff team will assist you as soon as possible."
    )
    embed = discord.Embed(
        title=title,
        description=desc,
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="One open ticket per user • Staff will respond shortly")
    return embed


# ── Panel preview view ────────────────────────────────────────────────────────

class TicketPanelPreviewView(discord.ui.View):
    """
    Ephemeral preview shown to the admin before the panel is posted.
    Row 0 — disabled category buttons (exact preview of what users will see).
    Row 1 — ✅ Send  |  ❌ Cancel.
    """

    def __init__(
        self,
        target: discord.TextChannel,
        categories: list[dict],
        panel_embed: discord.Embed,
    ):
        super().__init__(timeout=120)
        self.target      = target
        self.categories  = categories
        self.panel_embed = panel_embed

        # Row 0: greyed-out preview of the live buttons
        for cat in categories[:5]:
            emoji_str = cat.get("emoji", "").strip() or None
            self.add_item(discord.ui.Button(
                label=cat["label"],
                emoji=emoji_str,
                style=discord.ButtonStyle.primary,
                disabled=True,
                row=0,
            ))

        # Row 1: confirm / cancel
        send_btn = discord.ui.Button(
            label=f"Send to #{target.name}",
            style=discord.ButtonStyle.success,
            emoji="✅",
            row=1,
        )
        cancel_btn = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            emoji="❌",
            row=1,
        )
        send_btn.callback   = self._confirm
        cancel_btn.callback = self._cancel
        self.add_item(send_btn)
        self.add_item(cancel_btn)

    async def _confirm(self, interaction: discord.Interaction):
        """Post the real panel with live (clickable) buttons."""
        live_view = discord.ui.View(timeout=None)
        for cat in self.categories[:5]:
            emoji_str = cat.get("emoji", "").strip() or None
            live_view.add_item(discord.ui.Button(
                label=cat["label"],
                emoji=emoji_str,
                style=discord.ButtonStyle.primary,
                custom_id=f"tkt_open_{cat['label']}",
                row=0,
            ))
        await self.target.send(embed=self.panel_embed, view=live_view)

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            embeds=[discord.Embed(
                title="✅ Panel Sent",
                description=f"Ticket panel posted in {self.target.mention}.",
                color=discord.Color.green(),
            )],
            view=self,
        )
        self.stop()

    async def _cancel(self, interaction: discord.Interaction):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embeds=[discord.Embed(
                title="❌ Cancelled",
                description="Panel was not posted.",
                color=discord.Color.red(),
            )],
            view=self,
        )
        self.stop()

    async def on_timeout(self):
        self.stop()


# ── Persistent close button ────────────────────────────────────────────────────

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="tkt_close_btn",
    )
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass  # Routed through on_interaction in the cog


# ── Cog ───────────────────────────────────────────────────────────────────────

class TicketsCog(commands.Cog, name="Tickets"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(TicketCloseView())

    # ── Interaction router ────────────────────────────────────────────────────
    @commands.Cog.listener("on_interaction")
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if cid.startswith("tkt_open_"):
            await self._handle_open(interaction, cid[len("tkt_open_"):])
        elif cid == "tkt_close_btn":
            await self._handle_close_btn(interaction)

    # ── Open ticket ───────────────────────────────────────────────────────────
    async def _handle_open(self, interaction: discord.Interaction, category_label: str):
        user  = interaction.user
        guild = interaction.guild
        if not guild or user.bot:
            return

        existing = get_open_ticket_for_user(user.id, guild.id)
        if existing:
            ch_id = existing["channel_id"]
            ch = guild.get_channel(ch_id) if ch_id else None
            if ch:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="⚠️ Ticket Already Open",
                        description=(
                            f"You already have an open ticket: {ch.mention}\n"
                            "Please use that channel or ask staff to close it first."
                        ),
                        color=discord.Color.orange(),
                    ),
                    ephemeral=True,
                )
                return
            else:
                close_ticket(existing["ticket_id"])

        await interaction.response.defer(ephemeral=True)

        ticket_id = f"TKT-{uuid.uuid4().hex[:6].upper()}"
        number    = _next_ticket_number(guild.id)

        cat_id_raw = _cfg(guild.id, "category_id")
        category   = guild.get_channel(int(cat_id_raw)) if cat_id_raw else None

        staff_rids = _get_staff_role_ids(guild.id)
        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                manage_channels=True, read_message_history=True,
            ),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
            ),
        }
        for rid in staff_rids:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                    read_message_history=True, manage_messages=True,
                )

        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{number:04d}",
                category=category,
                overwrites=overwrites,
                topic=f"{ticket_id} | {category_label} | {user} ({user.id})",
                reason=f"Ticket opened by {user} ({user.id})",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Error",
                    description="I don't have permission to create channels. Please contact an admin.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        open_ticket(ticket_id, user.id, guild.id, category_label)
        update_ticket(ticket_id, channel_id=channel.id, status="open")
        log_staff_action("ticket_open", user.id, guild.id, details=f"{ticket_id} | {category_label}")

        staff_pings = " ".join(
            guild.get_role(rid).mention for rid in staff_rids if guild.get_role(rid)
        )

        intro = discord.Embed(
            title=f"🎫 {category_label} — {ticket_id}",
            description=(
                f"Welcome {user.mention}! A staff member will be with you shortly.\n\n"
                "**To close this ticket**, click the button below or use `/ticket close`."
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        intro.add_field(name="Opened By", value=user.mention,     inline=True)
        intro.add_field(name="Category",  value=category_label,   inline=True)
        intro.add_field(name="Ticket ID", value=f"`{ticket_id}`", inline=True)
        intro.set_footer(text="Staff will respond as soon as possible.")

        content = user.mention
        if staff_pings:
            content += f" • {staff_pings}"

        await channel.send(content=content, embed=intro, view=TicketCloseView())
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Ticket Opened",
                description=f"Your ticket has been created: {channel.mention}",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    # ── Close via button ──────────────────────────────────────────────────────
    async def _handle_close_btn(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            return
        row = get_ticket_by_channel(interaction.channel.id)
        if not row:
            await interaction.response.send_message(
                "This channel is not an active ticket.", ephemeral=True
            )
            return
        if not is_authorized(interaction.user, interaction.guild, "ticketclose") \
                and interaction.user.id != row["user_id"]:
            await interaction.response.send_message(
                "❌ You don't have permission to close this ticket.", ephemeral=True
            )
            return
        await interaction.response.defer()
        await self._do_close(
            interaction.guild, interaction.channel, row,
            interaction.user, reason="Closed via button"
        )

    # ── Core close logic ──────────────────────────────────────────────────────
    async def _do_close(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        row,
        closer: discord.Member,
        reason: str = "No reason given",
    ):
        ticket_id = row["ticket_id"]
        close_ticket(ticket_id)
        log_staff_action("ticket_close", closer.id, guild.id,
                         details=f"{ticket_id} | {reason}")

        logs_id = _cfg(guild.id, "logs_channel_id")
        if logs_id:
            logs_ch = guild.get_channel(int(logs_id))
            if logs_ch:
                opener = guild.get_member(row["user_id"]) or self.bot.get_user(row["user_id"])
                log_embed = discord.Embed(
                    title=f"🔒 Ticket Closed — {ticket_id}",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                )
                log_embed.add_field(name="Opened By", value=str(opener) if opener else str(row["user_id"]), inline=True)
                log_embed.add_field(name="Category",  value=row["category"],    inline=True)
                log_embed.add_field(name="Closed By", value=closer.mention,     inline=True)
                log_embed.add_field(name="Reason",    value=reason,             inline=False)
                log_embed.add_field(name="Opened At", value=str(row["opened_at"])[:16], inline=True)
                log_embed.set_footer(text=f"Channel: #{channel.name}")
                try:
                    await logs_ch.send(embed=log_embed)
                except discord.Forbidden:
                    pass

        closing = discord.Embed(
            title="🔒 Ticket Closed",
            description=(
                f"This ticket was closed by {closer.mention}.\n"
                f"**Reason:** {reason}\n\n"
                "This channel will be deleted in **5 seconds**."
            ),
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        try:
            await channel.send(embed=closing)
            await asyncio.sleep(5)
            await channel.delete(reason=f"Ticket {ticket_id} closed by {closer}")
        except (discord.Forbidden, discord.NotFound):
            pass

    # ── /ticket group ─────────────────────────────────────────────────────────
    ticket_group = app_commands.Group(name="ticket", description="Ticket system commands")

    # ── /ticket panel ─────────────────────────────────────────────────────────
    @ticket_group.command(name="panel", description="Preview then post the ticket panel")
    @app_commands.describe(channel="Channel to post the panel in (defaults to current channel)")
    async def panel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        await interaction.response.defer(ephemeral=True)
        if not is_authorized(interaction.user, interaction.guild, "ticketpanel"):
            await interaction.followup.send(
                embed=discord.Embed(title="❌ Permission Denied", description="You must be Admin or above.", color=discord.Color.red()),
                ephemeral=True,
            )
            return

        target     = channel or interaction.channel
        guild_id   = interaction.guild.id
        categories = _get_categories(guild_id)
        panel_embed = _build_panel_embed(guild_id)

        preview_header = discord.Embed(
            title="👁️ Panel Preview",
            description=(
                f"**Posting to:** {target.mention}\n"
                f"**Categories:** {len(categories)} button{'s' if len(categories) != 1 else ''}\n\n"
                "The buttons below are greyed out — this is exactly how the panel will look.\n"
                "Click **Send** to post it, or **Cancel** to abort."
            ),
            color=discord.Color.yellow(),
        )
        preview_header.set_footer(text="This preview is only visible to you • expires in 2 minutes")

        view = TicketPanelPreviewView(target, categories, panel_embed)
        await interaction.followup.send(
            embeds=[preview_header, panel_embed],
            view=view,
            ephemeral=True,
        )

    # ── /ticket close ─────────────────────────────────────────────────────────
    @ticket_group.command(name="close", description="Close the current ticket channel")
    @app_commands.describe(reason="Reason for closing")
    async def close(self, interaction: discord.Interaction, reason: str = "No reason given"):
        await interaction.response.defer()
        row = get_ticket_by_channel(interaction.channel.id)
        if not row:
            await interaction.followup.send("❌ This channel is not an active ticket.", ephemeral=True)
            return
        if not is_authorized(interaction.user, interaction.guild, "ticketclose") \
                and interaction.user.id != row["user_id"]:
            await interaction.followup.send("❌ You don't have permission to close this ticket.", ephemeral=True)
            return
        await self._do_close(interaction.guild, interaction.channel, row, interaction.user, reason=reason)

    # ── /ticket add ───────────────────────────────────────────────────────────
    @ticket_group.command(name="add", description="Add a user to the current ticket channel")
    @app_commands.describe(user="The member to add")
    async def add_user(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)
        row = get_ticket_by_channel(interaction.channel.id)
        if not row:
            await interaction.followup.send("❌ This channel is not an active ticket.", ephemeral=True)
            return
        if not is_authorized(interaction.user, interaction.guild, "ticketclose"):
            await interaction.followup.send("❌ You must be Admin or above to add users.", ephemeral=True)
            return
        try:
            await interaction.channel.set_permissions(
                user, view_channel=True, send_messages=True, read_message_history=True,
            )
            await interaction.followup.send(
                embed=discord.Embed(description=f"✅ {user.mention} has been added to this ticket.", color=discord.Color.green()),
                ephemeral=False,
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to manage this channel.", ephemeral=True)

    # ── /ticket remove ────────────────────────────────────────────────────────
    @ticket_group.command(name="remove", description="Remove a user from the current ticket channel")
    @app_commands.describe(user="The member to remove")
    async def remove_user(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)
        row = get_ticket_by_channel(interaction.channel.id)
        if not row:
            await interaction.followup.send("❌ This channel is not an active ticket.", ephemeral=True)
            return
        if not is_authorized(interaction.user, interaction.guild, "ticketclose"):
            await interaction.followup.send("❌ You must be Admin or above to remove users.", ephemeral=True)
            return
        if user.id == row["user_id"]:
            await interaction.followup.send("❌ Cannot remove the ticket owner.", ephemeral=True)
            return
        try:
            await interaction.channel.set_permissions(user, overwrite=None)
            await interaction.followup.send(
                embed=discord.Embed(description=f"✅ {user.mention} has been removed from this ticket.", color=discord.Color.orange()),
                ephemeral=False,
            )
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to manage this channel.", ephemeral=True)

    # ── /ticket list ──────────────────────────────────────────────────────────
    @ticket_group.command(name="list", description="List all open tickets")
    async def list_tickets(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not is_authorized(interaction.user, interaction.guild, "ticketclose"):
            await interaction.followup.send("❌ You must be Admin or above.", ephemeral=True)
            return
        rows = get_all_open_tickets(interaction.guild.id)
        embed = discord.Embed(title="📋 Open Tickets", color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        if not rows:
            embed.description = "No open tickets right now. ✅"
        else:
            lines = []
            for row in rows:
                ch_id = row["channel_id"]
                ch    = interaction.guild.get_channel(ch_id) if ch_id else None
                ch_str = ch.mention if ch else f"(deleted #{ch_id})"
                member = interaction.guild.get_member(row["user_id"])
                uname  = member.mention if member else f"ID:{row['user_id']}"
                lines.append(
                    f"**`{row['ticket_id']}`** {ch_str} — {uname} | "
                    f"{row['category']} | {str(row['opened_at'])[:16]}"
                )
            embed.description = "\n".join(lines)
            embed.set_footer(text=f"{len(rows)} open ticket(s)")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /ticket setstaffroles ─────────────────────────────────────────────────
    @ticket_group.command(name="setstaffroles", description="Set which roles can see and respond to tickets")
    @app_commands.describe(roles="Comma-separated role mentions, names, or IDs")
    async def setstaffroles(self, interaction: discord.Interaction, roles: str):
        await interaction.response.defer(ephemeral=True)
        if not is_authorized(interaction.user, interaction.guild, "ticketpanel"):
            await interaction.followup.send("❌ You must be Admin or above.", ephemeral=True)
            return
        guild = interaction.guild
        ids, names = [], []
        for token in [t.strip() for t in roles.split(",") if t.strip()]:
            role = None
            if token.startswith("<@&") and token.endswith(">"):
                try:
                    role = guild.get_role(int(token[3:-1]))
                except ValueError:
                    pass
            if role is None:
                try:
                    role = guild.get_role(int(token))
                except ValueError:
                    pass
            if role is None:
                role = discord.utils.find(lambda r, t=token: r.name.lower() == t.lower(), guild.roles)
            if role:
                ids.append(role.id)
                names.append(role.name)
        if not ids:
            await interaction.followup.send("❌ No valid roles found.", ephemeral=True)
            return
        _set_cfg(guild.id, "staff_roles", json.dumps(ids))
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Staff Roles Updated",
                description=(
                    f"Ticket staff roles: {', '.join(names)}\n"
                    "New ticket channels will include these roles automatically."
                ),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    # ── /ticket setchannel ────────────────────────────────────────────────────
    @ticket_group.command(name="setchannel", description="Set the Discord category where new ticket channels are created")
    @app_commands.describe(category="The category to use")
    async def setchannel(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        await interaction.response.defer(ephemeral=True)
        if not is_authorized(interaction.user, interaction.guild, "ticketpanel"):
            await interaction.followup.send("❌ You must be Admin or above.", ephemeral=True)
            return
        _set_cfg(interaction.guild.id, "category_id", str(category.id))
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Category Set",
                description=f"Ticket channels will be created under **{category.name}**.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    # ── /ticket setlogs ───────────────────────────────────────────────────────
    @ticket_group.command(name="setlogs", description="Set the channel where closed ticket summaries are posted")
    @app_commands.describe(channel="The log channel")
    async def setlogs(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        if not is_authorized(interaction.user, interaction.guild, "ticketpanel"):
            await interaction.followup.send("❌ You must be Admin or above.", ephemeral=True)
            return
        _set_cfg(interaction.guild.id, "logs_channel_id", str(channel.id))
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Log Channel Set",
                description=f"Closed ticket summaries will be posted in {channel.mention}.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    # ── /ticket setpanel ──────────────────────────────────────────────────────
    @ticket_group.command(name="setpanel", description="Customise the ticket panel embed title and description")
    @app_commands.describe(title="Panel embed title", description="Panel embed description")
    async def setpanel(self, interaction: discord.Interaction, title: str, description: str):
        await interaction.response.defer(ephemeral=True)
        if not is_authorized(interaction.user, interaction.guild, "ticketpanel"):
            await interaction.followup.send("❌ You must be Admin or above.", ephemeral=True)
            return
        _set_cfg(interaction.guild.id, "panel_title", title)
        _set_cfg(interaction.guild.id, "panel_description", description)
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Panel Text Updated",
                description=f"**Title:** {title}\n**Description:** {description}\n\nRe-post with `/ticket panel` to apply.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    # ── /ticket addtype ───────────────────────────────────────────────────────
    @ticket_group.command(name="addtype", description="Add a category button to the ticket panel")
    @app_commands.describe(label="Button label (e.g. General Support)", emoji="Button emoji (e.g. 🎫, optional)")
    async def addtype(self, interaction: discord.Interaction, label: str, emoji: str = ""):
        await interaction.response.defer(ephemeral=True)
        if not is_authorized(interaction.user, interaction.guild, "ticketpanel"):
            await interaction.followup.send("❌ You must be Admin or above.", ephemeral=True)
            return
        cats = _get_categories(interaction.guild.id)
        if len(cats) >= 5:
            await interaction.followup.send("❌ Maximum of **5** categories (Discord button row limit).", ephemeral=True)
            return
        if any(c["label"].lower() == label.lower() for c in cats):
            await interaction.followup.send(f"❌ A category named **{label}** already exists.", ephemeral=True)
            return
        if cats == DEFAULT_CATEGORIES:
            cats = []
        cats.append({"label": label, "emoji": emoji.strip()})
        _set_cfg(interaction.guild.id, "categories", json.dumps(cats))
        display = f"{emoji} **{label}**".strip() if emoji.strip() else f"**{label}**"
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Category Added",
                description=f"{display} added.\nRe-post the panel with `/ticket panel` to update.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    # ── /ticket removetype ────────────────────────────────────────────────────
    @ticket_group.command(name="removetype", description="Remove a category button from the ticket panel")
    @app_commands.describe(label="Exact label of the category to remove")
    async def removetype(self, interaction: discord.Interaction, label: str):
        await interaction.response.defer(ephemeral=True)
        if not is_authorized(interaction.user, interaction.guild, "ticketpanel"):
            await interaction.followup.send("❌ You must be Admin or above.", ephemeral=True)
            return
        cats = _get_categories(interaction.guild.id)
        new_cats = [c for c in cats if c["label"].lower() != label.lower()]
        if len(new_cats) == len(cats):
            await interaction.followup.send(f"❌ No category named **{label}** found.", ephemeral=True)
            return
        if not new_cats:
            new_cats = list(DEFAULT_CATEGORIES)
        _set_cfg(interaction.guild.id, "categories", json.dumps(new_cats))
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Category Removed",
                description=f"**{label}** removed.\nRe-post the panel with `/ticket panel` to update.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    # ── /ticket types ─────────────────────────────────────────────────────────
    @ticket_group.command(name="types", description="List all configured ticket categories")
    async def types(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        cats = _get_categories(interaction.guild.id)
        lines = []
        for i, c in enumerate(cats, 1):
            emoji = c.get("emoji", "").strip()
            line  = f"`{i}.` {emoji} **{c['label']}**" if emoji else f"`{i}.` **{c['label']}**"
            lines.append(line)
        await interaction.followup.send(
            embed=discord.Embed(
                title="🎫 Ticket Categories",
                description="\n".join(lines),
                color=discord.Color.blurple(),
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketsCog(bot))
