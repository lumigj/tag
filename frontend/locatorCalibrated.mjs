/**
 * Mirrors tag/pc/subscriber_calibrated.py + tag/pc/locator_core.py
 * (offset distance proxy, median RSSI filter, proportional triangle solve).
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

export function estimateOffsetPosition(sample, beaconCoords, offset) {
  if (sample.mode === 3 && sample.rssi3 !== null && sample.rssi3 !== undefined) {
    const distances = {
      B1: offsetDistanceProxy(sample.rssi1, offset),
      B2: offsetDistanceProxy(sample.rssi2, offset),
      B3: offsetDistanceProxy(sample.rssi3, offset),
    };
    const position = proportionalTrianglePosition(beaconCoords, distances);
    return {
      x: position[0],
      y: position[1],
      gate: "OFF-B123",
      color: "#2a9d8f",
    };
  }

  const distance1 = offsetDistanceProxy(sample.rssi1, offset);
  const distance2 = offsetDistanceProxy(sample.rssi2, offset);
  const total = distance1 + distance2;
  if (total <= 0) {
    const center = layoutCenter(beaconCoords, 2);
    return { x: center[0], y: center[1], gate: "OFF-B12", color: "#457b9d" };
  }

  const ratioFromB1 = distance1 / total;
  const x = round4(
    beaconCoords.B1[0] + (beaconCoords.B2[0] - beaconCoords.B1[0]) * ratioFromB1
  );
  const y = round4(
    beaconCoords.B1[1] + (beaconCoords.B2[1] - beaconCoords.B1[1]) * ratioFromB1
  );
  return { x, y, gate: "OFF-B12", color: "#457b9d" };
}
