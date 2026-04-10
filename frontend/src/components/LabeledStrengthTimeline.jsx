/** Fixed strength y-axis (matches RawStrengthChart). */
const STRENGTH_AXIS_MIN = 0;
const STRENGTH_AXIS_MAX = 3000;

function parseTimestamp(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    // Raw sensor timestamps are usually epoch seconds.
    return value > 1e12 ? value : value * 1000;
  }
  const asNumber = Number(value);
  if (!Number.isNaN(asNumber)) {
    return asNumber > 1e12 ? asNumber : asNumber * 1000;
  }
  const parsed = Date.parse(String(value));
  return Number.isNaN(parsed) ? null : parsed;
}

function formatDateTime(value) {
  return new Date(value).toLocaleString([], {
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function interpolateStrengthAtTime(rawPoints, targetMs) {
  if (!rawPoints.length || targetMs === null) return null;
  if (targetMs < rawPoints[0].timeMs || targetMs > rawPoints[rawPoints.length - 1].timeMs) {
    return null;
  }

  if (targetMs === rawPoints[0].timeMs) return rawPoints[0].strength;
  if (targetMs === rawPoints[rawPoints.length - 1].timeMs) return rawPoints[rawPoints.length - 1].strength;

  for (let i = 1; i < rawPoints.length; i += 1) {
    const left = rawPoints[i - 1];
    const right = rawPoints[i];
    if (targetMs >= left.timeMs && targetMs <= right.timeMs) {
      const span = right.timeMs - left.timeMs || 1;
      const ratio = (targetMs - left.timeMs) / span;
      return left.strength + ratio * (right.strength - left.strength);
    }
  }

  return null;
}

function strengthAtOrNearest(rawPoints, targetMs) {
  if (!rawPoints.length || targetMs === null) return null;
  if (targetMs <= rawPoints[0].timeMs) {
    return { strength: rawPoints[0].strength, projected: true };
  }
  if (targetMs >= rawPoints[rawPoints.length - 1].timeMs) {
    return { strength: rawPoints[rawPoints.length - 1].strength, projected: true };
  }
  const interpolated = interpolateStrengthAtTime(rawPoints, targetMs);
  if (interpolated === null) return null;
  return { strength: interpolated, projected: false };
}

/**
 * Processed rows often use server wall time (datetime.now) while raw MQTT rows use device epoch.
 * That mismatch blows up the X-axis (e.g. 0s vs 9000s) and flattens the line.
 * Map event time onto the raw capture window: in-range uses real time; otherwise proportional placement.
 */
function mapEventTimeToRawWindow(eventMs, rawMin, rawMax, eventMin, eventMax) {
  const rawSpan = rawMax - rawMin || 1;
  if (eventMs >= rawMin && eventMs <= rawMax) {
    return { displayMs: eventMs, aligned: true };
  }
  const eSpan = eventMax - eventMin || 1;
  const ratio = (eventMs - eventMin) / eSpan;
  return {
    displayMs: rawMin + Math.max(0, Math.min(1, ratio)) * rawSpan,
    aligned: false,
  };
}

function LabeledStrengthTimeline({ rawRows, processedRows }) {
  if (!rawRows?.length) {
    return (
      <div className="card">
        <h2>Strength Timeline (timestamp vs strength)</h2>
        <p>No raw data yet.</p>
      </div>
    );
  }

  const minInnerPlotW = 820;
  const maxInnerPlotW = 800000;
  const timePxPerSecond = 2.5;
  /** Space after the last data x so markers + datetime ticks are not clipped by the SVG viewBox */
  const rightGutter = 200;
  const height = 300;
  const chartLeft = 58;
  const chartTop = 18;
  const chartBottom = 56;

  const rawPoints = rawRows
    .map((row) => ({
      xSource: row.timestamp,
      timeMs: parseTimestamp(row.timestamp),
      strength: Number(row.strength),
    }))
    .filter((p) => p.timeMs !== null && !Number.isNaN(p.strength))
    .sort((a, b) => a.timeMs - b.timeMs);

  if (!rawPoints.length) {
    return (
      <div className="card">
        <h2>Strength Timeline (timestamp vs strength)</h2>
        <p>Unable to parse timestamps from raw data.</p>
      </div>
    );
  }

  const markerColors = {
    drop: "#ef4444",
    pickup: "#f59e0b",
    bump: "#8b5cf6",
    still: "#10b981",
  };

  const labelEvents = (processedRows || [])
    .filter((row) => markerColors[row.label])
    .map((row) => ({
      id: row.id,
      label: row.label,
      timeMs: parseTimestamp(row.timestamp),
    }))
    .filter((row) => row.timeMs !== null)
    .sort((a, b) => a.timeMs - b.timeMs);

  const rawMin = rawPoints[0].timeMs;
  const rawMax = rawPoints[rawPoints.length - 1].timeMs;
  const minX = rawMin;
  const maxX = rawMax;
  const minY = STRENGTH_AXIS_MIN;
  const maxY = STRENGTH_AXIS_MAX;
  const xRange = maxX - minX || 1;
  const yRange = maxY - minY;
  const byCountInner = rawPoints.length * 18;
  const byTimeInner = (xRange / 1000) * timePxPerSecond;
  const innerPlotW = Math.max(
    minInnerPlotW,
    Math.min(maxInnerPlotW, Math.max(byCountInner, byTimeInner))
  );
  const svgWidth = chartLeft + innerPlotW + rightGutter;

  const toSvgX = (timeMs) => chartLeft + ((timeMs - minX) / xRange) * innerPlotW;
  const plotRightX = chartLeft + innerPlotW;
  const toSvgY = (strength) => {
    const clamped = Math.min(maxY, Math.max(minY, strength));
    return height - chartBottom - ((clamped - minY) / yRange) * (height - chartTop - chartBottom);
  };

  const polyline = rawPoints
    .map((p) => `${toSvgX(p.timeMs)},${toSvgY(p.strength)}`)
    .join(" ");

  const eventTimeVals = labelEvents.map((e) => e.timeMs);
  const eventMin = eventTimeVals.length ? Math.min(...eventTimeVals) : rawMin;
  const eventMax = eventTimeVals.length ? Math.max(...eventTimeVals) : rawMax;

  const markers = labelEvents
    .map((row) => {
      const { displayMs, aligned } = mapEventTimeToRawWindow(
        row.timeMs,
        rawMin,
        rawMax,
        eventMin,
        eventMax
      );
      const strengthPoint = strengthAtOrNearest(rawPoints, displayMs);
      if (!strengthPoint) return null;
      return {
        id: row.id,
        label: row.label,
        x: toSvgX(displayMs),
        y: toSvgY(strengthPoint.strength),
        aligned,
      };
    })
    .filter(Boolean);

  const remappedCount = markers.filter((m) => !m.aligned).length;

  const yTicks = 5;
  const xTicks = 3;
  const yTickValues = Array.from({ length: yTicks }, (_, i) => {
    const ratio = i / (yTicks - 1);
    return maxY - ratio * yRange;
  });
  const xTickValues = Array.from({ length: xTicks }, (_, i) => {
    const ratio = i / (xTicks - 1);
    return minX + ratio * xRange;
  });

  return (
    <div className="card">
      <h2>Strength Timeline (timestamp vs strength)</h2>
      <div className="timeline-legend">
        <span><i className="legend-dot dot-drop" />drop</span>
        <span><i className="legend-dot dot-pickup" />pickup</span>
        <span><i className="legend-dot dot-bump" />bump</span>
        <span><i className="legend-dot dot-still" />still</span>
      </div>
      {remappedCount > 0 ? (
        <p className="timeline-note">
          {remappedCount} event label(s) use server time; X position is scaled into this sensor window (device vs laptop clock). For exact alignment, store the same timestamp (or running_time) on both raw and processed rows.
        </p>
      ) : null}
      <div className="timeline-scroll">
        <svg width={svgWidth} height={height} viewBox={`0 0 ${svgWidth} ${height}`} className="chart timeline-chart-svg">
          {/* Axes */}
          <line x1={chartLeft} y1={chartTop} x2={chartLeft} y2={height - chartBottom} stroke="#475569" strokeWidth="1.2" />
          <line
            x1={chartLeft}
            y1={height - chartBottom}
            x2={plotRightX}
            y2={height - chartBottom}
            stroke="#475569"
            strokeWidth="1.2"
          />

          {/* Y ticks and horizontal grid */}
          {yTickValues.map((v) => {
            const y = toSvgY(v);
            return (
              <g key={`y-${v}`}>
                <line x1={chartLeft} y1={y} x2={chartLeft - 6} y2={y} stroke="#475569" />
                <line x1={chartLeft} y1={y} x2={plotRightX} y2={y} stroke="#e2e8f0" strokeWidth="1" />
                <text x={chartLeft - 10} y={y + 4} textAnchor="end" fontSize="10.5" fill="#334155">
                  {v.toFixed(0)}
                </text>
              </g>
            );
          })}

          {/* X ticks */}
          {xTickValues.map((v) => {
            const x = toSvgX(v);
            return (
              <g key={`x-${v}`}>
                <line x1={x} y1={height - chartBottom} x2={x} y2={height - chartBottom + 6} stroke="#475569" />
                <text x={x} y={height - chartBottom + 18} textAnchor="middle" fontSize="10.5" fill="#334155">
                  {formatDateTime(v)}
                </text>
              </g>
            );
          })}

          {/* Axis titles */}
          <text
            x={16}
            y={(chartTop + (height - chartBottom)) / 2}
            transform={`rotate(-90 16 ${(chartTop + (height - chartBottom)) / 2})`}
            textAnchor="middle"
            fontSize="11.5"
            fill="#0f172a"
          >
            Strength
          </text>
          <text x={(chartLeft + plotRightX) / 2} y={height - 8} textAnchor="middle" fontSize="11.5" fill="#0f172a">
            Timestamp
          </text>

          <polyline fill="none" stroke="#2563eb" strokeWidth="2.2" points={polyline} />
          {markers.map((m) => (
            <g key={`${m.id}-${m.label}`}>
              <circle
                cx={m.x}
                cy={m.y}
                r="5.5"
                fill={markerColors[m.label]}
                fillOpacity={m.aligned ? "1" : "0.75"}
                stroke="#fff"
                strokeWidth="1.5"
              />
              <line
                x1={m.x}
                y1={m.y - 6}
                x2={m.x}
                y2={m.y - 24}
                stroke={markerColors[m.label]}
                strokeWidth="1.4"
                strokeOpacity={m.aligned ? "0.9" : "0.65"}
              />
              <rect
                x={m.x - 22}
                y={m.y - 40}
                width={44}
                height={16}
                rx={4}
                fill={markerColors[m.label]}
                fillOpacity={m.aligned ? "0.95" : "0.75"}
                stroke="#ffffff"
                strokeWidth="1"
              />
              <text
                x={m.x}
                y={m.y - 28}
                fill="#ffffff"
                textAnchor="middle"
                fontSize="10"
                fontWeight="700"
              >
                {m.label}
              </text>
            </g>
          ))}
        </svg>
      </div>
    </div>
  );
}

export default LabeledStrengthTimeline;
