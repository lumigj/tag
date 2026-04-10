function parseTimestamp(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return value > 1e12 ? value : value * 1000;
  const numeric = Number(value);
  if (!Number.isNaN(numeric)) return numeric > 1e12 ? numeric : numeric * 1000;
  const parsed = Date.parse(String(value));
  return Number.isNaN(parsed) ? null : parsed;
}

function formatDateTime(value) {
  const ms = parseTimestamp(value);
  if (ms === null) return String(value || "-");
  return new Date(ms).toLocaleString([], {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function DeviceStatusCard({ deviceState }) {
  if (!deviceState) {
    return <div className="card">Loading device state...</div>;
  }

  return (
    <div className="card">
      <h2>Device Status</h2>
      <p><strong>Mode:</strong> {deviceState.mode}</p>
      <p><strong>Connection:</strong> {deviceState.connection}</p>
      <p><strong>ML State:</strong> {deviceState.ml_state}</p>
      <p><strong>Last Seen:</strong> {formatDateTime(deviceState.last_seen)}</p>
    </div>
  );
}

export default DeviceStatusCard;
