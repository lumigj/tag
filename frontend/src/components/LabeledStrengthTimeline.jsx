function parseTimestamp(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return value > 1e12 ? value : value * 1000;
  const asNumber = Number(value);
  if (!Number.isNaN(asNumber)) return asNumber > 1e12 ? asNumber : asNumber * 1000;
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

function LabeledStrengthTimeline({ rawRows, processedRows }) {
  if (!rawRows?.length) {
    return (
      <div className="card">
        <h2>Timeline</h2>
        <p>No raw data yet.</p>
      </div>
    );
  }

  const latestRaw = rawRows[rawRows.length - 1];
  const latestProcessed = processedRows?.length ? processedRows[0] : null;

  return (
    <div className="card">
      <h2>Latest Sensor vs Prediction</h2>
      <p>
        Raw sample time: {formatDateTime(parseTimestamp(latestRaw.timestamp))} | strength:{" "}
        <strong>{Number(latestRaw.strength).toFixed(0)}</strong> | session: {String(latestRaw.session_id)}
      </p>
      <p>
        Latest prediction:{" "}
        {latestProcessed
          ? `${latestProcessed.label} @ ${formatDateTime(parseTimestamp(latestProcessed.timestamp))}`
          : "No prediction yet"}
      </p>
    </div>
  );
}

export default LabeledStrengthTimeline;
