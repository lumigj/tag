function formatZcr(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "string") return value;
  if (
    typeof value === "object"
    && value.type === "Buffer"
    && Array.isArray(value.data)
    && value.data.length === 8
  ) {
    let result = 0n;
    for (let i = 0; i < value.data.length; i += 1) {
      result += BigInt(value.data[i]) << (8n * BigInt(i));
    }
    return String(Number(result));
  }
  if (typeof value === "object") return JSON.stringify(value);
  const n = Number(value);
  return Number.isFinite(n) ? String(n) : String(value);
}

function EventLogTable({ events }) {
  function formatDateTime(value) {
    if (value !== null && typeof value === "object") {
      return JSON.stringify(value);
    }
    const parsed = Date.parse(String(value));
    if (!Number.isNaN(parsed)) {
      return new Date(parsed).toLocaleString([], {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
    }
    return String(value);
  }

  const classByLabel = {
    drop: "label-pill label-drop",
    pickup: "label-pill label-pickup",
    bump: "label-pill label-bump",
    still: "label-pill label-still",
  };

  return (
    <div className="card">
      <h2>Latest Predictions</h2>
      <div className="event-log-scroll">
      <table className="event-log-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Label</th>
            <th>Mean</th>
            <th>Std</th>
            <th>P2P</th>
            <th>ZCR</th>
            <th>Max Abs Diff</th>
            <th>Initial Delta</th>
            <th>Min</th>
            <th>Max</th>  
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr key={event.id} className={event.label === "drop" || event.label === "pickup" ? "event-highlight-row" : ""}>
              <td>{formatDateTime(event.timestamp)}</td>
              <td>
                <span className={classByLabel[event.label] || "label-pill"}>
                  {event.label}
                </span>
              </td>
              <td>{Number(event.mean).toFixed(4)}</td>
              <td>{Number(event.std).toFixed(4)}</td>
              <td>{Number(event.p2p).toFixed(4)}</td>
              <td>{formatZcr(event.zcr)}</td>
              <td>{Number(event.max_abs_diff).toFixed(4)}</td>
              <td>{Number(event.initial_delta).toFixed(4)}</td>
              <td>{Number(event.min).toFixed(4)}</td>
              <td>{Number(event.max).toFixed(4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}

export default EventLogTable;
