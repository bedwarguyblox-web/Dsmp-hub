"""
permissions.py — Role hierarchy, permission templates, and check helpers.
All permission logic is centralised here so every cog imports from one place.
"""

import discord
import json
import os
import logging

logger = logging.getLogger(__name__)

# ── Load config once at import time ────────────────────────────────────────
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
with open(_CONFIG_PATH) as _f:
    CONFIG = json.load(_f)

# ── Staff role hierarchy (lower index = lower rank) ─────────────────────────
ROLE_HIERARCHY: list[str] = [
    "Jr Helper",
    "Helper",
    "Sr Helper",
    "Jr Moderator",
    "Moderator",
    "Sr Moderator",
    "Head Moderator",
    "Trial Partnership Manager",
    "Jr Partnership Manager",
    "Partnership Manager",
    "Sr Partnership Manager",
    "Head Partnership Manager",
    "Jr Ticket Helper",
    "Ticket Helper",
    "Sr Ticket Helper",
    "Jr Ticket Admin",
    "Ticket Admin",
    "Sr Ticket Admin",
    "Staff Manager",
    "Jr Admin",
    "Admin",
    "Head Admin",
]

# Numeric rank — higher is more powerful
ROLE_RANK: dict[str, int] = {name: i for i, name in enumerate(ROLE_HIERARCHY)}


# ── Discord permission templates per staff role ─────────────────────────────
# Only permissions that differ from the default @everyone baseline are listed.
ROLE_PERMISSION_TEMPLATES: dict[str, discord.Permissions] = {
    "Jr Helper": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
    ),
    "Helper": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
    ),
    "Sr Helper": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        mute_members=True,
    ),
    "Jr Moderator": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        mute_members=True,
        deafen_members=True,
        kick_members=True,
    ),
    "Moderator": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        mute_members=True,
        deafen_members=True,
        kick_members=True,
        ban_members=True,
    ),
    "Sr Moderator": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        mute_members=True,
        deafen_members=True,
        kick_members=True,
        ban_members=True,
        manage_nicknames=True,
    ),
    "Head Moderator": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        mute_members=True,
        deafen_members=True,
        kick_members=True,
        ban_members=True,
        manage_nicknames=True,
        manage_channels=True,
    ),
    "Trial Partnership Manager": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
    ),
    "Jr Partnership Manager": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        manage_webhooks=True,
    ),
    "Partnership Manager": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        manage_webhooks=True,
        mention_everyone=True,
    ),
    "Sr Partnership Manager": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        manage_webhooks=True,
        mention_everyone=True,
    ),
    "Head Partnership Manager": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        manage_webhooks=True,
        mention_everyone=True,
        manage_channels=True,
    ),
    "Jr Ticket Helper": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
    ),
    "Ticket Helper": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        manage_threads=True,
    ),
    "Sr Ticket Helper": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        manage_threads=True,
        kick_members=True,
    ),
    "Jr Ticket Admin": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        manage_threads=True,
        kick_members=True,
        ban_members=True,
    ),
    "Ticket Admin": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        manage_threads=True,
        kick_members=True,
        ban_members=True,
        manage_channels=True,
    ),
    "Sr Ticket Admin": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        manage_threads=True,
        kick_members=True,
        ban_members=True,
        manage_channels=True,
        manage_nicknames=True,
    ),
    "Staff Manager": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        manage_threads=True,
        kick_members=True,
        ban_members=True,
        manage_channels=True,
        manage_nicknames=True,
        manage_roles=True,
    ),
    "Jr Admin": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        manage_threads=True,
        kick_members=True,
        ban_members=True,
        manage_channels=True,
        manage_nicknames=True,
        manage_roles=True,
        manage_guild=True,
    ),
    "Admin": discord.Permissions(
        read_messages=True,
        send_messages=True,
        embed_links=True,
        attach_files=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        manage_messages=True,
        manage_threads=True,
        kick_members=True,
        ban_members=True,
        manage_channels=True,
        manage_nicknames=True,
        manage_roles=True,
        manage_guild=True,
        view_audit_log=True,
    ),
    "Head Admin": discord.Permissions(administrator=True),
}


# ── Helper: get the highest staff rank name for a member ───────────────────

def get_staff_rank(member: discord.Member) -> str | None:
    """Return the name of the highest staff role the member holds, or None."""
    member_role_ids = {r.id for r in member.roles}
    best_rank = -1
    best_name = None
    for role_name, rank in ROLE_RANK.items():
        rid = CONFIG.get("STAFF_ROLES", {}).get(role_name)
        if rid and rid in member_role_ids and rank > best_rank:
            best_rank = rank
            best_name = role_name
    return best_name


def get_rank_value(role_name: str) -> int:
    """Return numeric rank; -1 if unknown."""
    return ROLE_RANK.get(role_name, -1)


# ── Permission guard helpers ────────────────────────────────────────────────

def is_at_least(member: discord.Member, minimum_role: str) -> bool:
    """Return True if member's highest staff rank >= minimum_role."""
    rank = get_staff_rank(member)
    if rank is None:
        return False
    return ROLE_RANK.get(rank, -1) >= ROLE_RANK.get(minimum_role, 999)


def is_owner(member: discord.Member) -> bool:
    return member.id == CONFIG.get("OWNER_ID")


def can_manage_roles(actor: discord.Member, target: discord.Member) -> bool:
    """
    Return True if actor's top role is higher than target's top role
    (enforces Discord hierarchy — prevents acting on peers / superiors).
    """
    return actor.top_role > target.top_role


def can_manage_specific_role(actor: discord.Member, role: discord.Role) -> bool:
    """Return True if actor's top role is strictly above the role being modified."""
    return actor.top_role > role
