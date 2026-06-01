import path from "path";
import { Router, type IRouter } from "express";
import Database from "better-sqlite3";
import {
  GetStatsResponse,
  GetVouchLeaderboardResponse,
  GetScamVouchLeaderboardResponse,
  GetRecentStrikesResponse,
  GetBuilderCasesResponse,
  GetBuilderPaymentsResponse,
  GetRecentActivityResponse,
} from "@workspace/api-zod";

const router: IRouter = Router();

const DB_PATH = path.resolve(process.cwd(), "../../data/database.db");

function openDb(): Database.Database {
  return new Database(DB_PATH, { readonly: true, fileMustExist: false });
}

router.get("/stats", (req, res) => {
  try {
    const db = openDb();
    const totalVouches = (
      db.prepare("SELECT COUNT(*) as c FROM vouches").get() as { c: number }
    ).c;
    const totalScamVouches = (
      db.prepare("SELECT COUNT(*) as c FROM scam_vouches").get() as {
        c: number;
      }
    ).c;
    const totalStrikes = (
      db
        .prepare("SELECT COUNT(*) as c FROM strike_history WHERE action='add'")
        .get() as { c: number }
    ).c;
    const totalBuilderCases = (
      db.prepare("SELECT COUNT(*) as c FROM builder_cases").get() as {
        c: number;
      }
    ).c;
    const totalPayments = (
      db.prepare("SELECT COUNT(*) as c FROM builder_payments").get() as {
        c: number;
      }
    ).c;
    const activeTimers = (
      db
        .prepare("SELECT COUNT(*) as c FROM builder_cases WHERE status='active'")
        .get() as { c: number }
    ).c;
    const since = new Date(Date.now() - 24 * 60 * 60 * 1000)
      .toISOString()
      .replace("T", " ")
      .slice(0, 19);
    const recentActions = (
      db
        .prepare(
          "SELECT COUNT(*) as c FROM staff_actions WHERE timestamp >= ?"
        )
        .get(since) as { c: number }
    ).c;
    db.close();

    const data = GetStatsResponse.parse({
      totalVouches,
      totalScamVouches,
      totalStrikes,
      totalBuilderCases,
      totalPayments,
      activeTimers,
      recentActions,
    });
    res.json(data);
  } catch (err) {
    req.log.warn({ err }, "stats query failed");
    res.json({
      totalVouches: 0,
      totalScamVouches: 0,
      totalStrikes: 0,
      totalBuilderCases: 0,
      totalPayments: 0,
      activeTimers: 0,
      recentActions: 0,
    });
  }
});

router.get("/vouches/leaderboard", (req, res) => {
  const limit = Math.min(Number(req.query["limit"] ?? 10), 100);
  try {
    const db = openDb();
    const rows = db
      .prepare(
        `SELECT target_id as userId, COUNT(*) as total
         FROM vouches
         GROUP BY target_id
         ORDER BY total DESC
         LIMIT ?`
      )
      .all(limit) as { userId: string; total: number }[];
    db.close();
    const data = GetVouchLeaderboardResponse.parse(
      rows.map((r, i) => ({ ...r, userId: String(r.userId), rank: i + 1 }))
    );
    res.json(data);
  } catch (err) {
    req.log.warn({ err }, "vouch leaderboard query failed");
    res.json([]);
  }
});

router.get("/scamvouches/leaderboard", (req, res) => {
  const limit = Math.min(Number(req.query["limit"] ?? 10), 100);
  try {
    const db = openDb();
    const rows = db
      .prepare(
        `SELECT target_id as userId, COUNT(*) as total
         FROM scam_vouches
         GROUP BY target_id
         ORDER BY total DESC
         LIMIT ?`
      )
      .all(limit) as { userId: string; total: number }[];
    db.close();
    const data = GetScamVouchLeaderboardResponse.parse(
      rows.map((r, i) => ({ ...r, userId: String(r.userId), rank: i + 1 }))
    );
    res.json(data);
  } catch (err) {
    req.log.warn({ err }, "scam vouch leaderboard query failed");
    res.json([]);
  }
});

router.get("/strikes/recent", (req, res) => {
  const limit = Math.min(Number(req.query["limit"] ?? 20), 100);
  try {
    const db = openDb();
    const rows = db
      .prepare(
        `SELECT id, user_id as userId, moderator_id as moderatorId,
                reason, action, timestamp
         FROM strike_history
         ORDER BY timestamp DESC
         LIMIT ?`
      )
      .all(limit) as {
      id: number;
      userId: string;
      moderatorId: string;
      reason: string;
      action: string;
      timestamp: string;
    }[];
    db.close();
    const data = GetRecentStrikesResponse.parse(
      rows.map((r) => ({
        ...r,
        userId: String(r.userId),
        moderatorId: String(r.moderatorId),
      }))
    );
    res.json(data);
  } catch (err) {
    req.log.warn({ err }, "strikes query failed");
    res.json([]);
  }
});

router.get("/builder/cases", (req, res) => {
  try {
    const db = openDb();
    const rows = db
      .prepare(
        `SELECT case_id as caseId, builder_id as builderId,
                customer_id as customerId, ign, amount,
                status, start_time as startTime, end_time as endTime,
                created_at as createdAt
         FROM builder_cases
         ORDER BY created_at DESC`
      )
      .all() as {
      caseId: string;
      builderId: string;
      customerId: string;
      ign: string;
      amount: string;
      status: string;
      startTime: string | null;
      endTime: string | null;
      createdAt: string;
    }[];
    db.close();
    const data = GetBuilderCasesResponse.parse(
      rows.map((r) => ({
        ...r,
        builderId: String(r.builderId),
        customerId: String(r.customerId),
      }))
    );
    res.json(data);
  } catch (err) {
    req.log.warn({ err }, "builder cases query failed");
    res.json([]);
  }
});

router.get("/builder/payments", (req, res) => {
  const limit = Math.min(Number(req.query["limit"] ?? 20), 100);
  try {
    const db = openDb();
    const rows = db
      .prepare(
        `SELECT id, payment_id as paymentId, staff_id as staffId,
                ign, amount, timestamp
         FROM builder_payments
         ORDER BY timestamp DESC
         LIMIT ?`
      )
      .all(limit) as {
      id: number;
      paymentId: string;
      staffId: string;
      ign: string;
      amount: string;
      timestamp: string;
    }[];
    db.close();
    const data = GetBuilderPaymentsResponse.parse(
      rows.map((r) => ({ ...r, staffId: String(r.staffId) }))
    );
    res.json(data);
  } catch (err) {
    req.log.warn({ err }, "builder payments query failed");
    res.json([]);
  }
});

router.get("/activity", (req, res) => {
  const limit = Math.min(Number(req.query["limit"] ?? 30), 200);
  try {
    const db = openDb();
    const rows = db
      .prepare(
        `SELECT id, action_type as actionType, actor_id as actorId,
                target_id as targetId, details, timestamp
         FROM staff_actions
         ORDER BY timestamp DESC
         LIMIT ?`
      )
      .all(limit) as {
      id: number;
      actionType: string;
      actorId: string;
      targetId: string | null;
      details: string | null;
      timestamp: string;
    }[];
    db.close();
    const data = GetRecentActivityResponse.parse(
      rows.map((r) => ({
        ...r,
        actorId: String(r.actorId),
        targetId: r.targetId != null ? String(r.targetId) : null,
      }))
    );
    res.json(data);
  } catch (err) {
    req.log.warn({ err }, "activity query failed");
    res.json([]);
  }
});

export default router;
