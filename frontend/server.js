import cors from "cors";
import express from "express";
import mqtt from "mqtt";
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
const LOCATOR_BROKER = process.env.LOCATOR_BROKER || "broker.emqx.io";
const LOCATOR_BROKER_PORT = Number(process.env.LOCATOR_BROKER_PORT || 1883);
const LOCATOR_TOPIC_PREFIX = process.env.LOCATOR_TOPIC_PREFIX || "/is4151-is5451/tag-locator/v1";
const LOCATOR_TAG_ID = process.env.LOCATOR_TAG_ID || "tag-1";
const LOCATOR_USERNAME = process.env.LOCATOR_USERNAME || "emqx";
const LOCATOR_PASSWORD = process.env.LOCATOR_PASSWORD || "public";
const LOCATOR_HISTORY = Number(process.env.LOCATOR_HISTORY || 20);

app.use(cors());
app.use(express.json());

const BEACON_COORDS = {
  B1: [0.15, 0.14],
  B2: [0.85, 0.14],
  B3: [0.5, 0.7462],
};

const locatorState = {
  connected: false,
  tag_id: LOCATOR_TAG_ID,
  mode: 2,
  gate: "WAIT",
  rssi: { B1: null, B2: null, B3: null },
  current: { x: 0.5, y: 0.34, color: "#adb5bd" },
  trail: [],
  calibration_rows: "-- --",
  timestamp: null,
  updated_at: null,
};

const locatorSseClients = new Set();

function sampleTopic() {
  return `${LOCATOR_TOPIC_PREFIX.replace(/\/$/, "")}/${LOCATOR_TAG_ID}/sample`;
}

function calibrationTopic() {
  return `${LOCATOR_TOPIC_PREFIX.replace(/\/$/, "")}/${LOCATOR_TAG_ID}/calibration`;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function rssiToScore(rssi) {
  return clamp((Number(rssi) || -95) - (-95) + 1, 1, 56);
}

function estimateRawPosition(sample) {
  const mode = Number(sample.mode) === 3 ? 3 : 2;
  const s1 = rssiToScore(sample.rssi1);
  const s2 = rssiToScore(sample.rssi2);
  if (mode === 2) {
    const total = s1 + s2 || 1;
    const ratio = s2 / total;
    return {
      x: Number((BEACON_COORDS.B1[0] + (BEACON_COORDS.B2[0] - BEACON_COORDS.B1[0]) * ratio).toFixed(4)),
      y: Number((BEACON_COORDS.B1[1] + (BEACON_COORDS.B2[1] - BEACON_COORDS.B1[1]) * ratio).toFixed(4)),
      gate: "RAW-B12",
      color: "#457b9d",
    };
  }
  const s3 = rssiToScore(sample.rssi3);
  const total = s1 + s2 + s3 || 1;
  const x = (
    BEACON_COORDS.B1[0] * s1
    + BEACON_COORDS.B2[0] * s2
    + BEACON_COORDS.B3[0] * s3
  ) / total;
  const y = (
    BEACON_COORDS.B1[1] * s1
    + BEACON_COORDS.B2[1] * s2
    + BEACON_COORDS.B3[1] * s3
  ) / total;
  return { x: Number(x.toFixed(4)), y: Number(y.toFixed(4)), gate: "RAW-B123", color: "#2a9d8f" };
}

function formatCalibrationRows(payload, mode) {
  const modeRows = payload?.modes?.[String(mode)];
  if (!modeRows || typeof modeRows !== "object") return mode === 3 ? "-- -- --" : "-- --";
  if (mode === 3) {
    return ["B1", "B2", "B3"].map((b) => (modeRows[b] ? b : "--")).join(" ");
  }
  return ["B1", "B2"].map((b) => (modeRows[b] ? b : "--")).join(" ");
}

function locatorSnapshot() {
  return {
    connected: locatorState.connected,
    tag_id: locatorState.tag_id,
    mode: locatorState.mode,
    gate: locatorState.gate,
    beacons: BEACON_COORDS,
    rssi: locatorState.rssi,
    current: locatorState.current,
    trail: locatorState.trail,
    calibration_rows: locatorState.calibration_rows,
    timestamp: locatorState.timestamp,
    updated_at: locatorState.updated_at,
    status_text: [
      `tag mode = ${locatorState.mode === 3 ? "TRIANGLE" : "LINE"}`,
      `estimate = ${locatorState.gate}`,
      `rssi B1  = ${locatorState.rssi.B1 ?? "--"}`,
      `rssi B2  = ${locatorState.rssi.B2 ?? "--"}`,
      `rssi B3  = ${locatorState.rssi.B3 ?? "--"}`,
      `cal rows = ${locatorState.calibration_rows}`,
    ].join("\n"),
  };
}

function pushLocatorEvent() {
  const payload = `event: locator\ndata: ${JSON.stringify(locatorSnapshot())}\n\n`;
  for (const res of locatorSseClients) {
    res.write(payload);
  }
}

function startLocatorMqtt() {
  const client = mqtt.connect(`mqtt://${LOCATOR_BROKER}:${LOCATOR_BROKER_PORT}`, {
    username: LOCATOR_USERNAME,
    password: LOCATOR_PASSWORD,
    clientId: `tag-frontend-locator-${Math.floor(Math.random() * 10000)}`,
    reconnectPeriod: 1500,
  });

  client.on("connect", () => {
    locatorState.connected = true;
    client.subscribe(sampleTopic());
    client.subscribe(calibrationTopic());
    pushLocatorEvent();
    console.log(`Locator MQTT connected. sample=${sampleTopic()} calibration=${calibrationTopic()}`);
  });

  client.on("close", () => {
    locatorState.connected = false;
    pushLocatorEvent();
  });

  client.on("message", (topic, raw) => {
    let payload;
    try {
      payload = JSON.parse(raw.toString("utf-8"));
    } catch {
      return;
    }
    if (payload?.tag_id !== LOCATOR_TAG_ID) return;
    if (topic === calibrationTopic() && payload?.type === "calibration_state") {
      locatorState.calibration_rows = formatCalibrationRows(payload, locatorState.mode);
      locatorState.updated_at = Date.now();
      pushLocatorEvent();
      return;
    }
    if (topic === sampleTopic() && payload?.type === "tag_sample") {
      const mode = Number(payload.mode) === 3 ? 3 : 2;
      const estimate = estimateRawPosition(payload);
      locatorState.mode = mode;
      locatorState.gate = estimate.gate;
      locatorState.rssi = {
        B1: Number.isFinite(Number(payload.rssi1)) ? Number(payload.rssi1) : null,
        B2: Number.isFinite(Number(payload.rssi2)) ? Number(payload.rssi2) : null,
        B3: payload.rssi3 === null || payload.rssi3 === undefined ? null : Number(payload.rssi3),
      };
      locatorState.current = { x: estimate.x, y: estimate.y, color: estimate.color };
      locatorState.timestamp = payload.timestamp || null;
      locatorState.updated_at = Date.now();
      locatorState.trail.push({ x: estimate.x, y: estimate.y });
      if (locatorState.trail.length > LOCATOR_HISTORY) {
        locatorState.trail = locatorState.trail.slice(-LOCATOR_HISTORY);
      }
      pushLocatorEvent();
    }
  });

  client.on("error", (error) => {
    console.error("Locator MQTT error:", error?.message || error);
  });
}

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

function decodeSqliteIntBuffer(value) {
  if (!Buffer.isBuffer(value)) return value;
  if (value.length === 8) {
    return Number(value.readBigInt64LE(0));
  }
  return value;
}

function normalizeProcessedRow(row) {
  if (!row) return row;
  return {
    ...row,
    zcr: decodeSqliteIntBuffer(row.zcr),
  };
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

app.get("/api", (_req, res) => {
  res.json({
    ok: true,
    message: "Tag frontend API is running.",
    endpoints: [
      "/api/health",
      "/api/summary",
      "/api/raw/latest?limit=120",
      "/api/processed/latest?limit=80",
      "/api/locator/latest",
      "/api/locator/stream",
    ],
  });
});

app.get("/api/locator/latest", (_req, res) => {
  res.json(locatorSnapshot());
});

app.get("/api/locator/stream", (req, res) => {
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();

  locatorSseClients.add(res);
  res.write(`event: locator\ndata: ${JSON.stringify(locatorSnapshot())}\n\n`);

  const keepAlive = setInterval(() => {
    res.write(": ping\n\n");
  }, 15000);

  req.on("close", () => {
    clearInterval(keepAlive);
    locatorSseClients.delete(res);
  });
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
          done(data.reverse().map(normalizeProcessedRow));
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
  startLocatorMqtt();
  console.log(`SQLite API server running on http://localhost:${PORT}`);
  console.log(`Reading raw DB: ${RAW_DB_PATH}`);
  console.log(`Reading processed DB: ${PROCESSED_DB_PATH}`);
  console.log(`Raw source: ${RAW_SOURCE_VIEW}`);
  console.log(`Processed source: ${PROCESSED_SOURCE_VIEW}`);
  console.log(`Locator source: mqtt://${LOCATOR_BROKER}:${LOCATOR_BROKER_PORT} (${LOCATOR_TAG_ID})`);
});
