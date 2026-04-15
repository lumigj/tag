import cors from "cors";
import express from "express";
import mqtt from "mqtt";
import path from "path";
import sqlite3 from "sqlite3";
import { fileURLToPath } from "url";
import {
  defaultBeaconCoords,
  estimateOffsetPosition,
  filterSample,
  layoutCenter,
  newFilterStatesByMode,
  normalizeBeaconOffsets,
  offsetDistanceProxy,
  sampleFromPayload,
} from "./locatorCalibrated.mjs";

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
const rawDefLocatorOffset = Number(process.env.LOCATOR_RSSI_OFFSET || 0);
const DEF_LOCATOR_RSSI_OFFSET = Number.isFinite(rawDefLocatorOffset) ? rawDefLocatorOffset : 0;

function envBeaconOffsetVar(name) {
  const v = process.env[name];
  if (v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

const initialLocatorOffsets = normalizeBeaconOffsets({
  B1: envBeaconOffsetVar("LOCATOR_RSSI_OFFSET_B1") ?? DEF_LOCATOR_RSSI_OFFSET,
  B2: envBeaconOffsetVar("LOCATOR_RSSI_OFFSET_B2") ?? DEF_LOCATOR_RSSI_OFFSET,
  B3: envBeaconOffsetVar("LOCATOR_RSSI_OFFSET_B3") ?? DEF_LOCATOR_RSSI_OFFSET,
});

app.use(cors());
app.use(
  express.json({
    verify: (req, _res, buf) => {
      if (buf?.length) {
        req.rawLocatorBody = buf.toString("utf8");
      }
    },
  })
);

const BEACON_COORDS = defaultBeaconCoords();
const locatorFilterStatesByMode = newFilterStatesByMode();
const initialLocatorCenter = layoutCenter(BEACON_COORDS, 3);

const locatorState = {
  connected: false,
  tag_id: LOCATOR_TAG_ID,
  mode: 2,
  gate: "WAIT",
  rssi: { B1: null, B2: null, B3: null },
  current: {
    x: initialLocatorCenter[0],
    y: initialLocatorCenter[1],
    color: "#adb5bd",
    inside_triangle: true,
  },
  trail: [],
  calibration_rows: "-- --",
  timestamp: null,
  updated_at: null,
  offsets: initialLocatorOffsets,
  sampleHistory: [],
};

const locatorSseClients = new Set();

/** @type {import("mqtt").MqttClient | null} */
let locatorMqttClient = null;

let lastRingPublishAt = 0;
const RING_COOLDOWN_MS = 2500;

function sampleTopic() {
  return `${LOCATOR_TOPIC_PREFIX.replace(/\/$/, "")}/${LOCATOR_TAG_ID}/sample`;
}

function calibrationTopic() {
  return `${LOCATOR_TOPIC_PREFIX.replace(/\/$/, "")}/${LOCATOR_TAG_ID}/calibration`;
}

function cmdTopic() {
  return `${LOCATOR_TOPIC_PREFIX.replace(/\/$/, "")}/${LOCATOR_TAG_ID}/cmd`;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function offsetPreviewExamples(offsets) {
  const o = normalizeBeaconOffsets(offsets);
  const row = (off) => ({
    rssi_neg21: Math.trunc(offsetDistanceProxy(-21, off)),
    rssi_neg50: Math.trunc(offsetDistanceProxy(-50, off)),
  });
  return { B1: row(o.B1), B2: row(o.B2), B3: row(o.B3) };
}

function recomputeLocatorFromHistory() {
  const history = locatorState.sampleHistory;
  if (!history.length) return;

  const latest = history[history.length - 1];
  locatorState.mode = latest.mode;
  const currentMode = latest.mode;
  const samplesInMode = history.filter((s) => s.mode === currentMode);
  if (!samplesInMode.length) return;

  const estimates = samplesInMode.map((s) => estimateOffsetPosition(s, BEACON_COORDS, locatorState.offsets));
  const last = estimates[estimates.length - 1];
  const modeSample = samplesInMode[samplesInMode.length - 1];

  locatorState.gate = last.gate;
  locatorState.rssi = {
    B1: modeSample.rssi1,
    B2: modeSample.rssi2,
    B3: modeSample.rssi3 === null || modeSample.rssi3 === undefined ? null : modeSample.rssi3,
  };
  const insideTri = last.insideTriangle !== false;
  locatorState.current = {
    x: last.x,
    y: last.y,
    color: last.color,
    inside_triangle: insideTri,
  };
  locatorState.timestamp = modeSample.timestamp ?? null;
  locatorState.trail = estimates.slice(0, -1).map((e) => ({
    x: e.x,
    y: e.y,
    outside: e.insideTriangle === false,
  }));
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
  const preview = offsetPreviewExamples(locatorState.offsets);
  const off = locatorState.offsets;
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
    offsets: { ...off },
    offset_preview: preview,
    status_text: [
      `tag mode = ${locatorState.mode === 3 ? "TRIANGLE" : "LINE"}`,
      `estimate = ${locatorState.gate}`,
      ...(locatorState.mode === 3
        ? [
            `region   = ${
              locatorState.current?.inside_triangle === false ? "OUTSIDE hull" : "inside hull"
            }`,
          ]
        : []),
      `rssi B1  = ${locatorState.rssi.B1 ?? "--"}`,
      `rssi B2  = ${locatorState.rssi.B2 ?? "--"}`,
      `rssi B3  = ${locatorState.rssi.B3 ?? "--"}`,
      `offsets  = B1=${off.B1} B2=${off.B2} B3=${off.B3}`,
      `proxy ex = B1:-21->${preview.B1.rssi_neg21} -50->${preview.B1.rssi_neg50} | B2:-21->${preview.B2.rssi_neg21} -50->${preview.B2.rssi_neg50} | B3:-21->${preview.B3.rssi_neg21} -50->${preview.B3.rssi_neg50}`,
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
    locatorMqttClient = client;
    locatorState.connected = true;
    client.subscribe(sampleTopic());
    client.subscribe(calibrationTopic());
    pushLocatorEvent();
    console.log(
      `Locator MQTT connected. sample=${sampleTopic()} calibration=${calibrationTopic()} ring publishes -> ${cmdTopic()}`
    );
  });

  client.on("close", () => {
    locatorMqttClient = null;
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
      const rawSample = sampleFromPayload(payload);
      if (!rawSample) return;

      const filterStates = locatorFilterStatesByMode[rawSample.mode];
      if (!filterStates) return;

      const filtered = filterSample(rawSample, filterStates);
      locatorState.sampleHistory.push(filtered);
      if (locatorState.sampleHistory.length > LOCATOR_HISTORY) {
        locatorState.sampleHistory.shift();
      }
      recomputeLocatorFromHistory();
      locatorState.updated_at = Date.now();
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
      "POST /api/ring",
      "POST /api/locator/offset",
    ],
  });
});

app.post("/api/ring", (req, res) => {
  if (!locatorMqttClient || !locatorMqttClient.connected) {
    res.status(503).json({ ok: false, error: "MQTT client not connected yet" });
    return;
  }
  const now = Date.now();
  if (now - lastRingPublishAt < RING_COOLDOWN_MS) {
    res.status(429).json({ ok: false, error: "Ring cooldown active; try again shortly" });
    return;
  }
  const payload = JSON.stringify({
    type: "ring",
    tag_id: LOCATOR_TAG_ID,
  });
  locatorMqttClient.publish(cmdTopic(), payload, { qos: 0 }, (err) => {
    if (err) {
      res.status(500).json({ ok: false, error: String(err?.message || err) });
      return;
    }
    lastRingPublishAt = Date.now();
    console.log(`Published ring command to ${cmdTopic()}`);
    res.json({ ok: true });
  });
});

app.get("/api/locator/latest", (_req, res) => {
  res.json(locatorSnapshot());
});

function flattenLocatorOffsetPostBody(raw) {
  const body = raw && typeof raw === "object" && !Array.isArray(raw) ? { ...raw } : {};
  const mergeBeacon = (src) => {
    if (!src || typeof src !== "object" || Array.isArray(src)) return;
    for (const k of ["B1", "B2", "B3"]) {
      if (!Object.prototype.hasOwnProperty.call(src, k)) continue;
      body[k] = src[k];
    }
  };
  mergeBeacon(body.offsets);
  mergeBeacon(body.offset);
  return body;
}

app.post("/api/locator/offset", (req, res) => {
  let raw = req.body;
  const bodyLooksEmpty =
    raw === undefined ||
    raw === null ||
    (typeof raw === "object" && !Array.isArray(raw) && Object.keys(raw).length === 0);
  if (bodyLooksEmpty && typeof req.rawLocatorBody === "string" && req.rawLocatorBody.trim()) {
    try {
      raw = JSON.parse(req.rawLocatorBody);
    } catch {
      res.status(400).json({ error: "Invalid JSON body" });
      return;
    }
  }
  const body = flattenLocatorOffsetPostBody(raw);
  let next = { ...locatorState.offsets };

  const nested = body.offsets && typeof body.offsets === "object" && !Array.isArray(body.offsets) ? body.offsets : null;
  const hasBeaconKeyInBody = ["B1", "B2", "B3"].some((k) => Object.prototype.hasOwnProperty.call(body, k));
  const usePerBeacon = nested !== null || hasBeaconKeyInBody;

  if (usePerBeacon) {
    const src = nested || body;
    for (const k of ["B1", "B2", "B3"]) {
      if (!Object.prototype.hasOwnProperty.call(src, k)) continue;
      if (src[k] === undefined || src[k] === null) continue;
      const trimmed = String(src[k]).trim();
      if (trimmed === "") continue;
      const n = Number(trimmed);
      if (!Number.isFinite(n)) {
        res.status(400).json({ error: `offset ${k} must be a number` });
        return;
      }
      next[k] = n;
    }
  } else if (
    body.offset !== undefined &&
    body.offset !== null &&
    (typeof body.offset === "number" || typeof body.offset === "string") &&
    String(body.offset).trim() !== ""
  ) {
    const v = Number(String(body.offset).trim());
    if (!Number.isFinite(v)) {
      res.status(400).json({ error: "offset must be a number" });
      return;
    }
    next = { B1: v, B2: v, B3: v };
  }

  locatorState.offsets = normalizeBeaconOffsets(next);
  recomputeLocatorFromHistory();
  locatorState.updated_at = Date.now();
  pushLocatorEvent();
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
  console.log(`Ring command publishes to: ${cmdTopic()}`);
});
