"""
database.py — SQLite database initialization and helper functions.
All tables are created here; other modules import and call these helpers.
"""

import sqlite3
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

# Path to the SQLite database file
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "database.db")


def get_connection() -> sqlite3.Connection:
    """Return a thread-safe SQLite connection with row_factory set."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # rows accessible by column name
    conn.execute("PRAGMA journal_mode=WAL") # better concurrency
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create all tables if they do not already exist."""
    with get_connection() as conn:
        c = conn.cursor()

        # ── Staff action audit log ──────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS staff_actions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT    NOT NULL,
                actor_id    INTEGER NOT NULL,
                target_id   INTEGER,
                details     TEXT,
                guild_id    INTEGER NOT NULL,
                timestamp   TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # ── Active strike count (one row per user per guild) ────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS strikes (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER NOT NULL,
                guild_id  INTEGER NOT NULL,
                count     INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, guild_id)
            )
        """)

        # ── Individual strike history records ───────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS strike_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                guild_id    INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                reason      TEXT    NOT NULL,
                action      TEXT    NOT NULL DEFAULT 'add',
                timestamp   TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # ── Vouch records ───────────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS vouches (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                voucher_id  INTEGER NOT NULL,
                target_id   INTEGER NOT NULL,
                guild_id    INTEGER NOT NULL,
                proof       TEXT    NOT NULL,
                timestamp   TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(voucher_id, target_id, guild_id)
            )
        """)

        # ── Scam-vouch records ──────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS scam_vouches (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                voucher_id  INTEGER NOT NULL,
                target_id   INTEGER NOT NULL,
                guild_id    INTEGER NOT NULL,
                proof       TEXT    NOT NULL,
                timestamp   TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(voucher_id, target_id, guild_id)
            )
        """)

        # ── Builder payment records ─────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS builder_payments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id TEXT    NOT NULL UNIQUE,
                staff_id   INTEGER NOT NULL,
                guild_id   INTEGER NOT NULL,
                ign        TEXT    NOT NULL,
                amount     TEXT    NOT NULL,
                timestamp  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # ── Builder protection timer cases ──────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS builder_cases (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id     TEXT    NOT NULL UNIQUE,
                builder_id  INTEGER NOT NULL,
                customer_id INTEGER NOT NULL,
                guild_id    INTEGER NOT NULL,
                ign         TEXT    NOT NULL,
                amount      TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'pending_confirmation',
                start_time  TEXT,
                end_time    TEXT,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # ── Builder timer logs (per-case event log) ─────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS builder_timers (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id   TEXT    NOT NULL,
                event     TEXT    NOT NULL,
                actor_id  INTEGER,
                note      TEXT,
                timestamp TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # ── Serverify audit log ─────────────────────────────────────────────
        c.execute("""
            CREATE TABLE IF NOT EXISTS serverify_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id        INTEGER NOT NULL,
                guild_id        INTEGER NOT NULL,
                roles_scanned   INTEGER NOT NULL DEFAULT 0,
                roles_modified  INTEGER NOT NULL DEFAULT 0,
                perms_added     INTEGER NOT NULL DEFAULT 0,
                perms_removed   INTEGER NOT NULL DEFAULT 0,
                details         TEXT,
                timestamp       TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        conn.commit()
    logger.info("Database initialised at %s", DB_PATH)


# ── Strike helpers ──────────────────────────────────────────────────────────

def get_strike_count(user_id: int, guild_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT count FROM strikes WHERE user_id=? AND guild_id=?",
            (user_id, guild_id)
        ).fetchone()
    return row["count"] if row else 0


def add_strike(user_id: int, guild_id: int, moderator_id: int, reason: str) -> int:
    """Add one strike and return the new total count."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO strikes (user_id, guild_id, count)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, guild_id) DO UPDATE SET count = count + 1
        """, (user_id, guild_id))
        conn.execute("""
            INSERT INTO strike_history (user_id, guild_id, moderator_id, reason, action)
            VALUES (?, ?, ?, ?, 'add')
        """, (user_id, guild_id, moderator_id, reason))
        conn.commit()
        row = conn.execute(
            "SELECT count FROM strikes WHERE user_id=? AND guild_id=?",
            (user_id, guild_id)
        ).fetchone()
    return row["count"] if row else 1


def remove_strike(user_id: int, guild_id: int, moderator_id: int) -> int:
    """Remove one strike (min 0) and return the new total count."""
    current = get_strike_count(user_id, guild_id)
    if current <= 0:
        return 0
    with get_connection() as conn:
        conn.execute(
            "UPDATE strikes SET count = MAX(0, count - 1) WHERE user_id=? AND guild_id=?",
            (user_id, guild_id)
        )
        conn.execute("""
            INSERT INTO strike_history (user_id, guild_id, moderator_id, reason, action)
            VALUES (?, ?, ?, 'Strike removed by moderator', 'remove')
        """, (user_id, guild_id, moderator_id))
        conn.commit()
    return max(0, current - 1)


def get_strike_history(user_id: int, guild_id: int, limit: int = 10):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT * FROM strike_history
            WHERE user_id=? AND guild_id=? AND action='add'
            ORDER BY timestamp DESC
            LIMIT ?
        """, (user_id, guild_id, limit)).fetchall()
    return rows


def reset_all_strikes(guild_id: int) -> int:
    """Reset every user's strike count to 0 for a guild. Returns number of rows reset."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE strikes SET count=0 WHERE guild_id=? AND count>0", (guild_id,)
        )
        conn.commit()
    return cur.rowcount


# ── Vouch helpers ───────────────────────────────────────────────────────────

def add_vouch(voucher_id: int, target_id: int, guild_id: int, proof: str) -> bool:
    """Returns True if inserted, False if duplicate."""
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO vouches (voucher_id, target_id, guild_id, proof)
                VALUES (?, ?, ?, ?)
            """, (voucher_id, target_id, guild_id, proof))
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def add_scam_vouch(voucher_id: int, target_id: int, guild_id: int, proof: str) -> bool:
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO scam_vouches (voucher_id, target_id, guild_id, proof)
                VALUES (?, ?, ?, ?)
            """, (voucher_id, target_id, guild_id, proof))
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def get_vouch_counts(target_id: int, guild_id: int):
    """Returns (vouch_count, scam_vouch_count)."""
    with get_connection() as conn:
        v  = conn.execute(
            "SELECT COUNT(*) as c FROM vouches WHERE target_id=? AND guild_id=?",
            (target_id, guild_id)
        ).fetchone()["c"]
        sv = conn.execute(
            "SELECT COUNT(*) as c FROM scam_vouches WHERE target_id=? AND guild_id=?",
            (target_id, guild_id)
        ).fetchone()["c"]
    return v, sv


def get_recent_vouches(target_id: int, guild_id: int, limit: int = 5):
    with get_connection() as conn:
        return conn.execute("""
            SELECT * FROM vouches WHERE target_id=? AND guild_id=?
            ORDER BY timestamp DESC LIMIT ?
        """, (target_id, guild_id, limit)).fetchall()


def get_recent_scam_vouches(target_id: int, guild_id: int, limit: int = 5):
    with get_connection() as conn:
        return conn.execute("""
            SELECT * FROM scam_vouches WHERE target_id=? AND guild_id=?
            ORDER BY timestamp DESC LIMIT ?
        """, (target_id, guild_id, limit)).fetchall()


def get_vouch_leaderboard(guild_id: int, limit: int = 10):
    with get_connection() as conn:
        return conn.execute("""
            SELECT target_id, COUNT(*) as total
            FROM vouches WHERE guild_id=?
            GROUP BY target_id ORDER BY total DESC LIMIT ?
        """, (guild_id, limit)).fetchall()


def get_scam_vouch_leaderboard(guild_id: int, limit: int = 10):
    with get_connection() as conn:
        return conn.execute("""
            SELECT target_id, COUNT(*) as total
            FROM scam_vouches WHERE guild_id=?
            GROUP BY target_id ORDER BY total DESC LIMIT ?
        """, (guild_id, limit)).fetchall()


# ── Builder payment helpers ─────────────────────────────────────────────────

def add_builder_payment(payment_id: str, staff_id: int, guild_id: int, ign: str, amount: str):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO builder_payments (payment_id, staff_id, guild_id, ign, amount)
            VALUES (?, ?, ?, ?, ?)
        """, (payment_id, staff_id, guild_id, ign, amount))
        conn.commit()


def get_builder_payments(staff_id: int, guild_id: int):
    with get_connection() as conn:
        return conn.execute("""
            SELECT * FROM builder_payments
            WHERE staff_id=? AND guild_id=?
            ORDER BY timestamp DESC
        """, (staff_id, guild_id)).fetchall()


# ── Builder case helpers ────────────────────────────────────────────────────

def create_builder_case(case_id: str, builder_id: int, customer_id: int,
                        guild_id: int, ign: str, amount: str):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO builder_cases (case_id, builder_id, customer_id, guild_id, ign, amount)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (case_id, builder_id, customer_id, guild_id, ign, amount))
        conn.execute("""
            INSERT INTO builder_timers (case_id, event, actor_id, note)
            VALUES (?, 'created', ?, 'Case created, awaiting customer confirmation')
        """, (case_id, builder_id))
        conn.commit()


def get_builder_case(case_id: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM builder_cases WHERE case_id=?", (case_id,)
        ).fetchone()


def get_all_builder_cases(guild_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM builder_cases WHERE guild_id=? ORDER BY created_at DESC",
            (guild_id,)
        ).fetchall()


def update_builder_case_status(case_id: str, status: str,
                                start_time: str = None, end_time: str = None):
    with get_connection() as conn:
        if start_time and end_time:
            conn.execute("""
                UPDATE builder_cases
                SET status=?, start_time=?, end_time=?
                WHERE case_id=?
            """, (status, start_time, end_time, case_id))
        else:
            conn.execute(
                "UPDATE builder_cases SET status=? WHERE case_id=?",
                (status, case_id)
            )
        conn.commit()


def log_builder_timer_event(case_id: str, event: str, actor_id: int, note: str = None):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO builder_timers (case_id, event, actor_id, note)
            VALUES (?, ?, ?, ?)
        """, (case_id, event, actor_id, note))
        conn.commit()


def get_builder_case_logs(case_id: str):
    with get_connection() as conn:
        return conn.execute("""
            SELECT * FROM builder_timers WHERE case_id=?
            ORDER BY timestamp ASC
        """, (case_id,)).fetchall()


def get_pending_builder_cases():
    """Return all active (timer running) cases — used by scheduler on restart."""
    with get_connection() as conn:
        return conn.execute("""
            SELECT * FROM builder_cases
            WHERE status='active' AND end_time IS NOT NULL
        """).fetchall()


# ── Staff action log helper ─────────────────────────────────────────────────

def log_staff_action(action_type: str, actor_id: int, guild_id: int,
                     target_id: int = None, details: str = None):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO staff_actions (action_type, actor_id, target_id, details, guild_id)
            VALUES (?, ?, ?, ?, ?)
        """, (action_type, actor_id, target_id, details, guild_id))
        conn.commit()
