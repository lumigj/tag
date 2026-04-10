function EventLogTable({ events }) {
  function formatDateTime(value) {
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
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Label</th>
            <th>Mean</th>
            <th>Std</th>
            <th>P2P</th>
            <th>ZCR</th>
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
              <td>{Number(event.mean).toFixed(3)}</td>
              <td>{Number(event.std).toFixed(3)}</td>
              <td>{Number(event.p2p).toFixed(3)}</td>
              <td>{event.zcr}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default EventLogTable;
