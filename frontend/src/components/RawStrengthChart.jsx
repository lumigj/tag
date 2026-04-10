function parseRawTimestamp(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return value > 1e12 ? value : value * 1000;
  const n = Number(value);
  if (!Number.isNaN(n)) return n > 1e12 ? n : n * 1000;
  const p = Date.parse(String(value));
  return Number.isNaN(p) ? null : p;
}

function RawStrengthChart({ data }) {
  if (!data?.length) {
    return (
      <div className="card">
        <h2>Raw Strength</h2>
        <p>No raw data yet.</p>
      </div>
    );
  }

  const rows = data
    .map((row, idx) => ({
      idx,
      strength: Number(row.strength),
      timeMs: parseRawTimestamp(row.timestamp),
      session_id: row.session_id,
      running_time: Number(row.running_time),
    }))
    .filter((r) => !Number.isNaN(r.strength))
    .sort((a, b) => (a.running_time - b.running_time) || (a.idx - b.idx));

  const width = 900;
  const height = 220;
  const left = 40;
  const right = 20;
  const top = 10;
  const bottom = 30;
  const minStrength = Math.min(...rows.map((r) => r.strength));
  const maxStrength = Math.max(...rows.map((r) => r.strength));
  const sRange = maxStrength - minStrength || 1;
  const xDen = Math.max(rows.length - 1, 1);
  const toX = (i) => left + (i / xDen) * (width - left - right);
  const toY = (s) => height - bottom - ((s - minStrength) / sRange) * (height - top - bottom);
  const points = rows.map((r, i) => `${toX(i)},${toY(r.strength)}`).join(" ");
  const latest = rows[rows.length - 1];

  return (
    <div className="card">
      <h2>Raw Strength</h2>
      <p>
        Latest strength: <strong>{Math.round(latest.strength)}</strong> | session:{" "}
        <strong>{String(latest.session_id)}</strong>
      </p>
      <div className="timeline-scroll">
        <svg width={width} height={height} className="chart">
          <line x1={left} y1={top} x2={left} y2={height - bottom} stroke="#475569" />
          <line x1={left} y1={height - bottom} x2={width - right} y2={height - bottom} stroke="#475569" />
          <polyline fill="none" stroke="#2563eb" strokeWidth="2" points={points} />
        </svg>
      </div>
    </div>
  );
}

export default RawStrengthChart;
