function PredictionBreakdownChart({ rows }) {
  const colorByLabel = {
    drop: "#ef4444",
    pickup: "#f59e0b",
    bump: "#8b5cf6",
    still: "#10b981",
    unknown: "#64748b",
  };

  const counts = rows.reduce((acc, row) => {
    const label = row.label || "unknown";
    acc[label] = (acc[label] || 0) + 1;
    return acc;
  }, {});

  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const maxCount = entries.length ? entries[0][1] : 1;

  return (
    <div className="card">
      <h2>Prediction Label Breakdown</h2>
      {!entries.length ? (
        <p>No prediction data yet.</p>
      ) : (
        <div className="bar-list">
          {entries.map(([label, count]) => (
            <div key={label} className="bar-row">
              <div className="bar-meta">
                <span>{label}</span>
                <span>{count}</span>
              </div>
              <div className="bar-track">
                <div
                  className="bar-fill"
                  style={{
                    width: `${(count / maxCount) * 100}%`,
                    background: colorByLabel[label] || colorByLabel.unknown,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default PredictionBreakdownChart;
