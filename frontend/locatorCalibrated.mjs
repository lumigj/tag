/**
 * Mirrors tag/pc/subscriber_calibrated.py + tag/pc/locator_core.py
 * (offset distance proxy, median RSSI filter).
 * Triangle mode: unconstrained plane solve so the dot can sit outside the B1–B2–B3 hull.
 */

const FILTER_WINDOW = 5;
const FILTER_GATE_DB = 7;
const FILTER_CANDIDATE_DB = 4;
const FILTER_CONFIRM_COUNT = 3;
const FILTER_SMOOTHING = 0.3;
const TRIANGLE_SEARCH_STEPS = 48;

const TRIANGLE_SIDE = 0.7;
const TRIANGLE_BASE_Y = 0.14;
const TRIANGLE_HEIGHT = (TRIANGLE_SIDE * Math.sqrt(3)) / 2;

export const BEACON_ORDER = ["B1", "B2", "B3"];

export function defaultBeaconCoords() {
  return {
    B1: [0.5 - TRIANGLE_SIDE / 2, TRIANGLE_BASE_Y],
    B2: [0.5 + TRIANGLE_SIDE / 2, TRIANGLE_BASE_Y],
    B3: [0.5, TRIANGLE_BASE_Y + TRIANGLE_HEIGHT],
  };
}

export function activeLabelsForMode(mode) {
  return mode === 3 ? [...BEACON_ORDER] : BEACON_ORDER.slice(0, 2);
}

/** Centroid of active beacons for the given layout mode (2 = B1–B2, 3 = full triangle). */
export function layoutCenter(beaconCoords, mode) {
  const labels = activeLabelsForMode(mode);
  const x = labels.reduce((s, label) => s + beaconCoords[label][0], 0) / labels.length;
  const y = labels.reduce((s, label) => s + beaconCoords[label][1], 0) / labels.length;
  return [round4(x), round4(y)];
}

function round4(n) {
  return Math.round(n * 10000) / 10000;
}

export function offsetDistanceProxy(rssi, offset) {
  const proxy = Math.abs(Number(rssi)) - Number(offset);
  if (proxy < 0) return 0;
  return proxy;
}

/**
 * Per-beacon offsets in max(abs(rssi) - offset_B?, 0). A single finite number applies to B1, B2, and B3.
 * @param {number | { B1?: unknown, B2?: unknown, B3?: unknown } | null | undefined} input
 * @returns {{ B1: number, B2: number, B3: number }}
 */
export function normalizeBeaconOffsets(input) {
  if (typeof input === "number" && Number.isFinite(input)) {
    return { B1: input, B2: input, B3: input };
  }
  const d = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  };
  if (input && typeof input === "object") {
    return {
      B1: d(input.B1),
      B2: d(input.B2),
      B3: d(input.B3),
    };
  }
  return { B1: 0, B2: 0, B3: 0 };
}

function median(nums) {
  const s = [...nums].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  if (s.length % 2 === 1) return s[mid];
  return (s[mid - 1] + s[mid]) / 2;
}

function scoreTupleLess(a, b) {
  if (a[0] !== b[0]) return a[0] < b[0];
  return a[1] < b[1];
}

export function triangleCandidateError(pointX, pointY, beaconCoords, proxyDistances) {
  const actualDistances = {};
  for (const label of BEACON_ORDER) {
    const [bx, by] = beaconCoords[label];
    actualDistances[label] = Math.hypot(pointX - bx, pointY - by);
  }

  let orderPenalty = 0;
  for (const label of BEACON_ORDER) {
    for (const otherLabel of BEACON_ORDER) {
      if (proxyDistances[label] + 1e-9 < proxyDistances[otherLabel]) {
        const d = actualDistances[label] - actualDistances[otherLabel];
        orderPenalty += Math.max(d, 0) ** 2;
      }
    }
  }

  let denominator = 0;
  for (const label of BEACON_ORDER) {
    denominator += proxyDistances[label] ** 2;
  }
  if (denominator <= 1e-9) {
    return [orderPenalty, 0];
  }

  let scale = 0;
  for (const label of BEACON_ORDER) {
    scale += actualDistances[label] * proxyDistances[label];
  }
  scale /= denominator;

  let ratioError = 0;
  for (const label of BEACON_ORDER) {
    const diff = actualDistances[label] - scale * proxyDistances[label];
    ratioError += diff ** 2;
  }
  return [orderPenalty, ratioError];
}

export function proportionalTrianglePosition(beaconCoords, distances) {
  let bestPoint = layoutCenter(beaconCoords, 3);
  let bestScore = triangleCandidateError(bestPoint[0], bestPoint[1], beaconCoords, distances);

  for (let i = 0; i <= TRIANGLE_SEARCH_STEPS; i += 1) {
    const weightB1 = i / TRIANGLE_SEARCH_STEPS;
    for (let j = 0; j <= TRIANGLE_SEARCH_STEPS - i; j += 1) {
      const weightB2 = j / TRIANGLE_SEARCH_STEPS;
      const weightB3 = 1 - weightB1 - weightB2;

      const pointX =
        beaconCoords.B1[0] * weightB1 + beaconCoords.B2[0] * weightB2 + beaconCoords.B3[0] * weightB3;
      const pointY =
        beaconCoords.B1[1] * weightB1 + beaconCoords.B2[1] * weightB2 + beaconCoords.B3[1] * weightB3;

      const score = triangleCandidateError(pointX, pointY, beaconCoords, distances);
      if (scoreTupleLess(score, bestScore)) {
        bestScore = score;
        bestPoint = [pointX, pointY];
      }
    }
  }

  return [round4(bestPoint[0]), round4(bestPoint[1])];
}

const BARY_EPS = 1e-6;

/** True if (px, py) lies inside or on the edge of triangle B1–B2–B3 (same beacon order as layout). */
export function pointInClosedTriangle(px, py, beaconCoords) {
  const [ax, ay] = beaconCoords.B1;
  const [bx, by] = beaconCoords.B2;
  const [cx, cy] = beaconCoords.B3;
  const v0x = cx - ax;
  const v0y = cy - ay;
  const v1x = bx - ax;
  const v1y = by - ay;
  const v2x = px - ax;
  const v2y = py - ay;
  const dot00 = v0x * v0x + v0y * v0y;
  const dot01 = v0x * v1x + v0y * v1y;
  const dot11 = v1x * v1x + v1y * v1y;
  const dot20 = v2x * v0x + v2y * v0y;
  const dot21 = v2x * v1x + v2y * v1y;
  const denom = dot00 * dot11 - dot01 * dot01;
  if (Math.abs(denom) < 1e-14) return false;
  const inv = 1 / denom;
  const u = (dot11 * dot20 - dot01 * dot21) * inv;
  const v = (dot00 * dot21 - dot01 * dot20) * inv;
  return u >= -BARY_EPS && v >= -BARY_EPS && u + v <= 1 + BARY_EPS;
}

function refineUnconstrainedPlane(x0, y0, beaconCoords, proxyDistances) {
  let x = x0;
  let y = y0;
  let bestScore = triangleCandidateError(x, y, beaconCoords, proxyDistances);
  const dirs = [
    [1, 0],
    [-1, 0],
    [0, 1],
    [0, -1],
    [1, 1],
    [1, -1],
    [-1, 1],
    [-1, -1],
  ];
  let step = 0.045;
  for (let iter = 0; iter < 48; iter += 1) {
    let improved = false;
    for (const [dx, dy] of dirs) {
      const nx = x + dx * step;
      const ny = y + dy * step;
      const s = triangleCandidateError(nx, ny, beaconCoords, proxyDistances);
      if (scoreTupleLess(s, bestScore)) {
        bestScore = s;
        x = nx;
        y = ny;
        improved = true;
      }
    }
    if (!improved) step *= 0.55;
    if (step < 1e-5) break;
  }
  return [round4(x), round4(y)];
}

/**
 * Minimize the same (order_penalty, ratio_error) score as the constrained solver, but over an
 * extended region of the plane so the best point may lie outside the beacon triangle.
 */
export function unconstrainedTrianglePosition(beaconCoords, proxyDistances) {
  const sumProxy = BEACON_ORDER.reduce((s, label) => s + proxyDistances[label], 0);
  if (sumProxy <= 1e-9) {
    const c = layoutCenter(beaconCoords, 3);
    return { x: c[0], y: c[1], insideTriangle: true };
  }

  const xs = BEACON_ORDER.map((label) => beaconCoords[label][0]);
  const ys = BEACON_ORDER.map((label) => beaconCoords[label][1]);
  const pad = 0.38;
  const minX = Math.min(...xs) - pad;
  const maxX = Math.max(...xs) + pad;
  const minY = Math.min(...ys) - pad;
  const maxY = Math.max(...ys) + pad;
  const step = 0.028;

  let bestX = (minX + maxX) / 2;
  let bestY = (minY + maxY) / 2;
  let bestScore = triangleCandidateError(bestX, bestY, beaconCoords, proxyDistances);

  for (let gx = minX; gx <= maxX; gx += step) {
    for (let gy = minY; gy <= maxY; gy += step) {
      const s = triangleCandidateError(gx, gy, beaconCoords, proxyDistances);
      if (scoreTupleLess(s, bestScore)) {
        bestScore = s;
        bestX = gx;
        bestY = gy;
      }
    }
  }

  const [rx, ry] = refineUnconstrainedPlane(bestX, bestY, beaconCoords, proxyDistances);
  const insideTriangle = pointInClosedTriangle(rx, ry, beaconCoords);
  return { x: rx, y: ry, insideTriangle };
}

export function newFilterState() {
  return {
    window: [],
    stableValue: null,
    candidateValue: null,
    candidateCount: 0,
  };
}

export function newFilterStatesByMode() {
  const mk = () => ({ B1: newFilterState(), B2: newFilterState(), B3: newFilterState() });
  return { 2: mk(), 3: mk() };
}

function pushWindow(state, rawRssi) {
  state.window.push(rawRssi);
  while (state.window.length > FILTER_WINDOW) {
    state.window.shift();
  }
}

export function filterRssi(filterState, rawRssi) {
  pushWindow(filterState, rawRssi);
  const medianValue = median(filterState.window);

  if (filterState.stableValue === null) {
    filterState.stableValue = medianValue;
    return Math.round(filterState.stableValue);
  }

  if (Math.abs(medianValue - filterState.stableValue) <= FILTER_GATE_DB) {
    filterState.stableValue = filterState.stableValue * (1 - FILTER_SMOOTHING) + medianValue * FILTER_SMOOTHING;
    filterState.candidateValue = null;
    filterState.candidateCount = 0;
    return Math.round(filterState.stableValue);
  }

  if (
    filterState.candidateValue === null ||
    Math.abs(medianValue - filterState.candidateValue) > FILTER_CANDIDATE_DB
  ) {
    filterState.candidateValue = medianValue;
    filterState.candidateCount = 1;
    return Math.round(filterState.stableValue);
  }

  filterState.candidateValue =
    (filterState.candidateValue * filterState.candidateCount + medianValue) / (filterState.candidateCount + 1);
  filterState.candidateCount += 1;

  if (filterState.candidateCount >= FILTER_CONFIRM_COUNT) {
    filterState.stableValue = filterState.candidateValue;
    filterState.candidateValue = null;
    filterState.candidateCount = 0;
  }

  return Math.round(filterState.stableValue);
}

export function filterSample(rawSample, filterStates) {
  const filteredRssi1 = filterRssi(filterStates.B1, rawSample.rssi1);
  const filteredRssi2 = filterRssi(filterStates.B2, rawSample.rssi2);
  let filteredRssi3 = null;
  if (rawSample.mode === 3 && rawSample.rssi3 !== null && rawSample.rssi3 !== undefined) {
    filteredRssi3 = filterRssi(filterStates.B3, rawSample.rssi3);
  }
  return {
    timestamp: rawSample.timestamp,
    mode: rawSample.mode,
    rssi1: filteredRssi1,
    rssi2: filteredRssi2,
    rssi3: filteredRssi3,
  };
}

export function sampleFromPayload(payload) {
  const mode = Number(payload.mode);
  if (!Number.isFinite(mode)) return null;
  const rssi1 = Number(payload.rssi1);
  const rssi2 = Number(payload.rssi2);
  if (!Number.isFinite(rssi1) || !Number.isFinite(rssi2)) return null;
  const rssi3Raw = payload.rssi3;
  let rssi3 = null;
  if (rssi3Raw !== null && rssi3Raw !== undefined) {
    const n = Number(rssi3Raw);
    if (!Number.isFinite(n)) return null;
    rssi3 = n;
  }
  const ts = Number(payload.timestamp);
  return {
    timestamp: Number.isFinite(ts) ? ts : Date.now() / 1000,
    mode,
    rssi1,
    rssi2,
    rssi3,
  };
}

export function estimateOffsetPosition(sample, beaconCoords, offsetsInput) {
  const o = normalizeBeaconOffsets(offsetsInput);
  if (sample.mode === 3 && sample.rssi3 !== null && sample.rssi3 !== undefined) {
    const distances = {
      B1: offsetDistanceProxy(sample.rssi1, o.B1),
      B2: offsetDistanceProxy(sample.rssi2, o.B2),
      B3: offsetDistanceProxy(sample.rssi3, o.B3),
    };
    const position = unconstrainedTrianglePosition(beaconCoords, distances);
    const insideTriangle = position.insideTriangle;
    return {
      x: position.x,
      y: position.y,
      gate: insideTriangle ? "OFF-B123" : "OFF-B123-OUT",
      color: insideTriangle ? "#2a9d8f" : "#ea580c",
      insideTriangle,
    };
  }

  const distance1 = offsetDistanceProxy(sample.rssi1, o.B1);
  const distance2 = offsetDistanceProxy(sample.rssi2, o.B2);
  const total = distance1 + distance2;
  if (total <= 0) {
    const center = layoutCenter(beaconCoords, 2);
    return { x: center[0], y: center[1], gate: "OFF-B12", color: "#457b9d", insideTriangle: true };
  }

  const ratioFromB1 = distance1 / total;
  const x = round4(
    beaconCoords.B1[0] + (beaconCoords.B2[0] - beaconCoords.B1[0]) * ratioFromB1
  );
  const y = round4(
    beaconCoords.B1[1] + (beaconCoords.B2[1] - beaconCoords.B1[1]) * ratioFromB1
  );
  return { x, y, gate: "OFF-B12", color: "#457b9d", insideTriangle: true };
}
