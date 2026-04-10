import cors from "cors";
import express from "express";
import path from "path";
import sqlite3 from "sqlite3";
import { fileURLToPath } from "url";

const app = express();
const PORT = Number(process.env.API_PORT || 4000);
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const RAW_DB_PATH = process.env.RAW_DB_PATH || path.join(__dirname, "..", "pc", "cloud_u1raw.db");
const PROCESSED_DB_PATH = process.env.PROCESSED_DB_PATH || path.join(__dirname, "..", "pc", "cloud_u1fea.db");
const RAW_SOURCE_VIEW = safeIdentifier(process.env.RAW_SOURCE_VIEW, "raw_data");
const PROCESSED_SOURCE_VIEW = safeIdentifier(process.env.PROCESSED_SOURCE_VIEW, "processed_data");

app.use(cors());
app.use(express.json());

function withDb(dbPath, queryFn) {
  return new Promise((resolve, reject) => {
    const db = new sqlite3.Database(dbPath, sqlite3.OPEN_READONLY, (openErr) => {
      if (openErr) {
        reject(openErr);
        return;
      }
      db.run("PRAGMA busy_timeout = 1000");
      queryFn(
        db,
        (result) => {
          db.close();
          resolve(result);
        },
        (queryErr) => {
          db.close();
          reject(queryErr);
        }
      );
    });
  });
}

function clampLimit(value, defaultLimit = 100, maxLimit = 1000) {
  const parsed = Number.parseInt(String(value || defaultLimit), 10);
  if (Number.isNaN(parsed) || parsed <= 0) {
    return defaultLimit;
  }
  return Math.min(parsed, maxLimit);
}

function safeIdentifier(name, fallback) {
  const input = String(name || fallback);
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(input)) {
    return fallback;
  }
  return input;
}

app.get("/api/health", (_req, res) => {
  res.json({ ok: true });
});

app.get("/api/raw/latest", async (req, res) => {
  const limit = clampLimit(req.query.limit, 120, 1500);
  try {
    const rows = await withDb(RAW_DB_PATH, (db, done, fail) => {
      db.all(
        `
          SELECT id, timestamp, running_time, strength, session_id
          FROM ${RAW_SOURCE_VIEW}
          ORDER BY id DESC
          LIMIT ?
        `,
        [limit],
        (err, data) => {
          if (err) return fail(err);
          done(data.reverse());
        }
      );
    });
    res.json({ data: rows });
  } catch (error) {
    res.status(500).json({ error: String(error?.message || error) });
  }
});

app.get("/api/processed/latest", async (req, res) => {
  const limit = clampLimit(req.query.limit, 80, 1000);
  try {
    const rows = await withDb(PROCESSED_DB_PATH, (db, done, fail) => {
      db.all(
        `
          SELECT id, timestamp, mean, std, max, min, p2p, zcr, max_abs_diff, initial_delta, label
          FROM ${PROCESSED_SOURCE_VIEW}
          ORDER BY id DESC
          LIMIT ?
        `,
        [limit],
        (err, data) => {
          if (err) return fail(err);
          done(data.reverse());
        }
      );
    });
    res.json({ data: rows });
  } catch (error) {
    res.status(500).json({ error: String(error?.message || error) });
  }
});

app.get("/api/summary", async (_req, res) => {
  try {
    const [latestRaw, latestPrediction] = await Promise.all([
      withDb(RAW_DB_PATH, (db, done, fail) => {
        db.get(
          `
            SELECT id, timestamp, running_time, strength, session_id
            FROM ${RAW_SOURCE_VIEW}
            ORDER BY id DESC
            LIMIT 1
          `,
          [],
          (err, row) => (err ? fail(err) : done(row || null))
        );
      }),
      withDb(PROCESSED_DB_PATH, (db, done, fail) => {
        db.get(
          `
            SELECT id, timestamp, label
            FROM ${PROCESSED_SOURCE_VIEW}
            ORDER BY id DESC
            LIMIT 1
          `,
          [],
          (err, row) => (err ? fail(err) : done(row || null))
        );
      }),
    ]);

    res.json({
      mode: "LIVE_MONITOR",
      connection: latestRaw ? "Connected" : "Waiting for MQTT data",
      ml_state: latestPrediction?.label || "No prediction yet",
      last_seen: latestRaw?.timestamp || null,
      latest_raw: latestRaw,
      latest_prediction: latestPrediction,
    });
  } catch (error) {
    res.status(500).json({ error: String(error?.message || error) });
  }
});

app.listen(PORT, () => {
  console.log(`SQLite API server running on http://localhost:${PORT}`);
  console.log(`Reading raw DB: ${RAW_DB_PATH}`);
  console.log(`Reading processed DB: ${PROCESSED_DB_PATH}`);
  console.log(`Raw source: ${RAW_SOURCE_VIEW}`);
  console.log(`Processed source: ${PROCESSED_SOURCE_VIEW}`);
});
