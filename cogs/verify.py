"""
verify.py — Member verification system.

Setup: /verify setup channel: role:
  - Stores the verification channel and the role to assign on verify.

Flow:
  1. New member joins → bot sends a welcome+verify prompt in the verify channel.
  2. Member replies with ANY message → bot assigns the verified role and deletes their reply.

The prompt asks them to share their favourite food — any reply works.
"""

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.database import get_guild_config, set_guild_config
from utils.permissions import is_authorized

logger = logging.getLogger(__name__)


class VerifyCog(commands.Cog, name="Verify"):
    """Member gate / verification system."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # pending[guild_id][user_id] = prompt message id (in-memory only)
        self._pending: dict[int, dict[int, int]] = {}

    # ── /verify group ─────────────────────────────────────────────────────────
    verify_group = app_commands.Group(
        name="verify",
        description="Verification system — setup and view",
    )

    @verify_group.command(
        name="setup",
        description="Set the verification channel and role granted on verify",
    )
    @app_commands.describe(
        channel="Channel where new members must verify",
        role="Role assigned automatically once they verify",
    )
    async def verify_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role,
    ):
        await interaction.response.defer(ephemeral=True)

        if not is_authorized(interaction.user, interaction.guild, "verify"):
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Permission Denied",
                    description="You must be **Admin** or above to configure verification.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        gid = interaction.guild.id
        set_guild_config(gid, "verify_channel_id", str(channel.id))
        set_guild_config(gid, "verify_role_id",    str(role.id))

        embed = discord.Embed(
            title="✅ Verification Configured",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Verify Channel", value=channel.mention, inline=True)
        embed.add_field(name="Verified Role",  value=role.mention,    inline=True)
        embed.add_field(
            name="How it works",
            value=(
                f"When someone joins, I'll ping them in {channel.mention} and ask "
                f"them to share their favourite food.\n"
                f"Once they reply with **anything**, they'll receive the {role.mention} role automatically."
            ),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info("Verification set up in guild %s: channel=%s role=%s", gid, channel.id, role.id)

    @verify_group.command(
        name="view",
        description="Show the current verification settings",
    )
    async def verify_view(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        gid     = interaction.guild.id
        ch_id   = get_guild_config(gid, "verify_channel_id")
        role_id = get_guild_config(gid, "verify_role_id")

        if not ch_id and not role_id:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="⚙️ Verification Not Configured",
                    description="Use `/verify setup channel: role:` to set it up.",
                    color=discord.Color.yellow(),
                ),
                ephemeral=True,
            )
            return

        ch   = interaction.guild.get_channel(int(ch_id))   if ch_id   else None
        role = interaction.guild.get_role(int(role_id))     if role_id else None

        embed = discord.Embed(
            title="⚙️ Verification Settings",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Verify Channel", value=ch.mention   if ch   else f"Unknown (ID {ch_id})",   inline=True)
        embed.add_field(name="Verified Role",  value=role.mention if role else f"Unknown (ID {role_id})", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── on_member_join ─────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        gid     = member.guild.id
        ch_id   = get_guild_config(gid, "verify_channel_id")
        role_id = get_guild_config(gid, "verify_role_id")
        if not ch_id or not role_id:
            return

        channel = member.guild.get_channel(int(ch_id))
        if not channel:
            return

        embed = discord.Embed(
            title="👋 Welcome! One quick step to unlock the server.",
            description=(
                f"Hey {member.mention}! 👀\n\n"
                f"To gain access to all the channels, **reply to this message** "
                f"with your **favourite food** 🍕🍜🍔\n\n"
                f"That's all — any reply works!"
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"User ID: {member.id}")

        try:
            msg = await channel.send(content=member.mention, embed=embed)
            self._pending.setdefault(gid, {})[member.id] = msg.id
            logger.info("Verification prompt sent to %s (%s) in guild %s", member, member.id, gid)
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("Could not send verify prompt in guild %s: %s", gid, exc)

    # ── on_message — detect verify reply ──────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        gid     = message.guild.id
        uid     = message.author.id
        ch_id   = get_guild_config(gid, "verify_channel_id")
        role_id = get_guild_config(gid, "verify_role_id")

        if not ch_id or not role_id:
            return
        if message.channel.id != int(ch_id):
            return

        role = message.guild.get_role(int(role_id))
        if not role:
            return

        # Skip if already verified
        if role in message.author.roles:
            self._pending.get(gid, {}).pop(uid, None)
            return

        # Anyone writing in the verify channel who doesn't have the role → verify them
        try:
            await message.author.add_roles(role, reason="Verification reply")
            self._pending.get(gid, {}).pop(uid, None)

            confirm = discord.Embed(
                title="✅ You're verified!",
                description=(
                    f"Welcome to the server, {message.author.mention}! 🎉\n"
                    f"You now have the **{role.name}** role and full access to all channels."
                ),
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc),
            )
            confirm.set_thumbnail(url=message.author.display_avatar.url)

            await message.channel.send(embed=confirm, delete_after=15)

            # Clean up their reply message
            try:
                await message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass

            logger.info("Verified %s (%s) in guild %s", message.author, uid, gid)

        except discord.Forbidden:
            logger.warning("Missing permission to assign role %s to %s in guild %s", role_id, uid, gid)
        except discord.HTTPException as exc:
            logger.warning("HTTPException verifying %s: %s", uid, exc)


async def setup(bot: commands.Bot):
    await bot.add_cog(VerifyCog(bot))
