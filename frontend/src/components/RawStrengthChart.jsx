function parseRawTimestamp(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    return value > 1e12 ? value : value * 1000;
  }
  const n = Number(value);
  if (!Number.isNaN(n)) {
    return n > 1e12 ? n : n * 1000;
  }
  const p = Date.parse(String(value));
  return Number.isNaN(p) ? null : p;
}

function formatClock(ms) {
  if (ms === null) return "—";
  return new Date(ms).toLocaleString([], {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

const SESSION_PALETTE = ["#2563eb", "#0d9488", "#7c3aed", "#ea580c", "#db2777"];

function sessionColor(sessionId, indexInPalette) {
  const key = Number(sessionId);
  const i = Number.isNaN(key) ? indexInPalette % SESSION_PALETTE.length : Math.abs(key) % SESSION_PALETTE.length;
  return SESSION_PALETTE[i];
}

function RawStrengthChart({ data }) {
  if (!data?.length) {
    return (
      <div className="card">
        <h2>Raw Strength (latest window)</h2>
        <p>No raw data yet.</p>
      </div>
    );
  }

  const rows = data
    .map((row, idx) => ({
      idx,
      running_time: Number(row.running_time),
      strength: Number(row.strength),
      session_id: row.session_id,
      timeMs: parseRawTimestamp(row.timestamp),
    }))
    .filter((r) => !Number.isNaN(r.strength))
    .sort((a, b) => {
      if (a.running_time !== b.running_time) {
        return a.running_time - b.running_time;
      }
      if (a.timeMs !== null && b.timeMs !== null) return a.timeMs - b.timeMs;
      return a.idx - b.idx;
    });

  if (!rows.length) {
    return (
      <div className="card">
        <h2>Raw Strength (latest window)</h2>
        <p>No valid strength samples.</p>
      </div>
    );
  }

  const sessionIds = [...new Set(rows.map((r) => r.session_id))];
  const sessionRank = new Map(sessionIds.map((id, i) => [id, i]));

  const strengths = rows.map((r) => r.strength);
  const sMin = Math.min(...strengths);
  const sMax = Math.max(...strengths);
  const sRange = sMax - sMin || 1;

  const timeMsList = rows.map((r) => r.timeMs).filter((t) => t !== null);
  const hasTimeAxis = timeMsList.length === rows.length;
  const firstClock = hasTimeAxis ? Math.min(...timeMsList) : null;
  const lastClock = hasTimeAxis ? Math.max(...timeMsList) : null;
  const rtVals = rows.map((r) => r.running_time).filter((v) => !Number.isNaN(v));
  const rtMin = rtVals.length ? Math.min(...rtVals) : 0;
  const rtMax = rtVals.length ? Math.max(...rtVals) : 0;

  const minInnerPlotW = 640;
  const maxInnerPlotW = 800000;
  const timePxPerSecond = 2.5;
  const rightGutter = 180;
  const height = 260;
  const chartLeft = 52;
  const chartTop = 12;
  const chartBottom = 50;

  const tMin = hasTimeAxis ? firstClock : 0;
  const tMax = hasTimeAxis ? lastClock : 1;
  const tSpan = (tMax - tMin) || 1;
  const byCountInner = rows.length * 12;
  const byTimeInner = hasTimeAxis ? (tSpan / 1000) * timePxPerSecond : 0;
  const rtSpan = (rtMax - rtMin) || 1;
  const innerPlotW = Math.max(
    minInnerPlotW,
    Math.min(
      maxInnerPlotW,
      hasTimeAxis ? Math.max(byCountInner, byTimeInner) : Math.max(byCountInner, 400)
    )
  );
  const svgWidth = chartLeft + innerPlotW + rightGutter;
  const plotRightX = chartLeft + innerPlotW;
  const toX = (timeMs, fallbackRunningTime) => {
    if (hasTimeAxis) {
      return chartLeft + ((timeMs - tMin) / tSpan) * innerPlotW;
    }
    return chartLeft + ((fallbackRunningTime - rtMin) / rtSpan) * innerPlotW;
  };
  const toY = (s) => height - chartBottom - ((s - sMin) / sRange) * (height - chartTop - chartBottom);

  const yTicks = 5;
  const yTickVals = Array.from({ length: yTicks }, (_, i) => sMax - (i / (yTicks - 1)) * sRange);
  const xTicks = Math.min(hasTimeAxis ? 3 : 6, rows.length);
  const xTickVals = Array.from({ length: xTicks }, (_, i) => {
    if (hasTimeAxis) return tMin + (i / Math.max(xTicks - 1, 1)) * tSpan;
    return rtMin + (i / Math.max(xTicks - 1, 1)) * ((rtMax - rtMin) || 1);
  });

  const lineSegments = [];
  for (let i = 0; i < rows.length - 1; i += 1) {
    const a = rows[i];
    const b = rows[i + 1];
    const color = sessionColor(a.session_id, sessionRank.get(a.session_id) ?? 0);
    lineSegments.push({
      key: `${a.idx}-${b.idx}`,
      x1: toX(a.timeMs, a.running_time),
      y1: toY(a.strength),
      x2: toX(b.timeMs, b.running_time),
      y2: toY(b.strength),
      stroke: color,
    });
  }

  return (
    <div className="card raw-strength-card">
      <h2>Raw Strength (latest window)</h2>

      <div className="raw-meta">
        <span>
          <strong>Session{sessionIds.length > 1 ? "s" : ""}:</strong>{" "}
          {sessionIds.map((id) => (
            <span key={String(id)} className="raw-session-chip" style={{ borderColor: sessionColor(id, sessionRank.get(id) ?? 0) }}>
              {id}
            </span>
          ))}
        </span>
        <span>
          <strong>Clock:</strong> {formatClock(firstClock)} → {formatClock(lastClock)}
        </span>
        <span>
          <strong>Running time:</strong> {rtMin} → {rtMax}
          {!hasTimeAxis ? <> <span className="raw-meta-muted">(x-axis fallback)</span></> : null}
        </span>
        <span>
          <strong>Strength:</strong> min {Math.round(sMin)} · max {Math.round(sMax)} · latest{" "}
          <strong>{Math.round(strengths[strengths.length - 1])}</strong>
        </span>
      </div>

      {sessionIds.length > 1 ? (
        <p className="raw-legend-hint">Line segments are colored by session_id.</p>
      ) : null}

      <div className="raw-chart-scroll">
        <svg width={svgWidth} height={height} viewBox={`0 0 ${svgWidth} ${height}`} className="chart raw-strength-svg">
          <line
            x1={chartLeft}
            y1={chartTop}
            x2={chartLeft}
            y2={height - chartBottom}
            stroke="#475569"
            strokeWidth="1.2"
          />
          <line
            x1={chartLeft}
            y1={height - chartBottom}
            x2={plotRightX}
            y2={height - chartBottom}
            stroke="#475569"
            strokeWidth="1.2"
          />

          {yTickVals.map((v) => {
            const y = toY(v);
            return (
              <g key={`y-${v}`}>
                <line x1={chartLeft} y1={y} x2={chartLeft - 5} y2={y} stroke="#475569" />
                <line x1={chartLeft} y1={y} x2={plotRightX} y2={y} stroke="#e2e8f0" strokeWidth="1" />
                <text x={chartLeft - 8} y={y + 4} textAnchor="end" fontSize="10" fill="#334155">
                  {Math.round(v)}
                </text>
              </g>
            );
          })}

          {xTickVals.map((v) => {
            const x = hasTimeAxis
              ? chartLeft + ((v - tMin) / tSpan) * innerPlotW
              : chartLeft + ((v - rtMin) / rtSpan) * innerPlotW;
            return (
              <g key={`x-${v}`}>
                <line x1={x} y1={height - chartBottom} x2={x} y2={height - chartBottom + 5} stroke="#475569" />
                <text x={x} y={height - chartBottom + 18} textAnchor="middle" fontSize="10" fill="#334155">
                  {hasTimeAxis ? formatClock(v) : Math.round(v)}
                </text>
              </g>
            );
          })}

          <text
            x={14}
            y={(chartTop + (height - chartBottom)) / 2}
            transform={`rotate(-90 14 ${(chartTop + (height - chartBottom)) / 2})`}
            textAnchor="middle"
            fontSize="11"
            fill="#0f172a"
          >
            Strength
          </text>
          <text
            x={(chartLeft + plotRightX) / 2}
            y={height - 6}
            textAnchor="middle"
            fontSize="11"
            fill="#0f172a"
          >
            {hasTimeAxis ? "Timestamp" : "Running time"}
          </text>

          {lineSegments.map((seg) => (
            <line
              key={seg.key}
              x1={seg.x1}
              y1={seg.y1}
              x2={seg.x2}
              y2={seg.y2}
              stroke={seg.stroke}
              strokeWidth="2.4"
              strokeLinecap="round"
            />
          ))}

          {rows.map((r) => (
            <circle
              key={r.idx}
              cx={toX(r.timeMs, r.running_time)}
              cy={toY(r.strength)}
              r="3.5"
              fill={sessionColor(r.session_id, sessionRank.get(r.session_id) ?? 0)}
              stroke="#fff"
              strokeWidth="1"
            />
          ))}
        </svg>
      </div>

      <div className="raw-table-mini">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>running_time</th>
              <th>strength</th>
              <th>session_id</th>
              <th>timestamp</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(-12).map((r) => (
              <tr key={r.idx}>
                <td>{r.idx}</td>
                <td>{r.running_time}</td>
                <td>{Math.round(r.strength)}</td>
                <td>{r.session_id}</td>
                <td className="raw-ts-cell">{r.timeMs !== null ? formatClock(r.timeMs) : String(data[r.idx]?.timestamp ?? "—")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length > 12 ? (
          <p className="raw-table-note">Showing last 12 of {rows.length} samples in this window.</p>
        ) : null}
      </div>
    </div>
  );
}

export default RawStrengthChart;
