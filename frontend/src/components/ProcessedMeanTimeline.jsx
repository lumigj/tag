function parseProcessedTimestamp(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "object") return null;
  if (typeof value === "number") {
    return value > 1e12 ? value : value * 1000;
  }
  const asNumber = Number(value);
  if (!Number.isNaN(asNumber)) {
    return asNumber > 1e12 ? asNumber : asNumber * 1000;
  }
  const parsed = Date.parse(String(value));
  return Number.isNaN(parsed) ? null : parsed;
}

function formatDateTime(ms) {
  return new Date(ms).toLocaleString([], {
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

const labelColors = {
  drop: "#ef4444",
  pickup: "#f59e0b",
  bump: "#8b5cf6",
  still: "#10b981",
  unknown: "#64748b",
};

function ProcessedMeanTimeline({ processedRows }) {
  if (!processedRows?.length) {
    return (
      <div className="card">
        <h2>Average strength vs time</h2>
        <p>No processed predictions yet.</p>
      </div>
    );
  }

  const minInnerPlotW = 820;
  const maxInnerPlotW = 800000;
  const timePxPerSecond = 2.5;
  const rightGutter = 200;
  const height = 300;
  const chartLeft = 58;
  const chartTop = 18;
  const chartBottom = 56;

  const points = processedRows
    .map((row) => ({
      id: row.id,
      timeMs: parseProcessedTimestamp(row.timestamp),
      mean: Number(row.mean),
      label: row.label || "unknown",
    }))
    .filter((p) => p.timeMs !== null && Number.isFinite(p.mean))
    .sort((a, b) => a.timeMs - b.timeMs);

  if (!points.length) {
    return (
      <div className="card">
        <h2>Average strength vs time</h2>
        <p>Unable to parse timestamps or mean values from processed_data.</p>
      </div>
    );
  }

  const minX = points[0].timeMs;
  const maxX = points[points.length - 1].timeMs;
  const rawMinMean = Math.min(...points.map((p) => p.mean));
  const rawMaxMean = Math.max(...points.map((p) => p.mean));
  const pad = (rawMaxMean - rawMinMean) * 0.12 || 0.08;
  const minY = rawMinMean - pad;
  const maxY = rawMaxMean + pad;
  const xRange = maxX - minX || 1;
  const yRange = maxY - minY || 1;

  const byCountInner = points.length * 18;
  const byTimeInner = (xRange / 1000) * timePxPerSecond;
  const innerPlotW = Math.max(
    minInnerPlotW,
    Math.min(maxInnerPlotW, Math.max(byCountInner, byTimeInner))
  );
  const svgWidth = chartLeft + innerPlotW + rightGutter;
  const plotRightX = chartLeft + innerPlotW;

  const toSvgX = (timeMs) => chartLeft + ((timeMs - minX) / xRange) * innerPlotW;
  const toSvgY = (mean) =>
    height - chartBottom - ((mean - minY) / yRange) * (height - chartTop - chartBottom);

  const polyline = points.map((p) => `${toSvgX(p.timeMs)},${toSvgY(p.mean)}`).join(" ");

  const yTicks = 5;
  const xTicks = Math.min(5, Math.max(2, points.length));
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
      <h2>Average strength vs time</h2>
      <p className="timeline-note">
        X: <code>processed_data.timestamp</code> · Y: <code>mean</code> · Labels show ML <code>label</code> per row.
      </p>
      <div className="timeline-legend">
        <span><i className="legend-dot dot-drop" />drop</span>
        <span><i className="legend-dot dot-pickup" />pickup</span>
        <span><i className="legend-dot dot-bump" />bump</span>
        <span><i className="legend-dot dot-still" />still</span>
      </div>
      <div className="timeline-scroll">
        <svg width={svgWidth} height={height} viewBox={`0 0 ${svgWidth} ${height}`} className="chart timeline-chart-svg">
          <line x1={chartLeft} y1={chartTop} x2={chartLeft} y2={height - chartBottom} stroke="#475569" strokeWidth="1.2" />
          <line
            x1={chartLeft}
            y1={height - chartBottom}
            x2={plotRightX}
            y2={height - chartBottom}
            stroke="#475569"
            strokeWidth="1.2"
          />

          {yTickValues.map((v) => {
            const y = toSvgY(v);
            return (
              <g key={`y-${v}`}>
                <line x1={chartLeft} y1={y} x2={chartLeft - 6} y2={y} stroke="#475569" />
                <line x1={chartLeft} y1={y} x2={plotRightX} y2={y} stroke="#e2e8f0" strokeWidth="1" />
                <text x={chartLeft - 10} y={y + 4} textAnchor="end" fontSize="10.5" fill="#334155">
                  {v.toFixed(3)}
                </text>
              </g>
            );
          })}

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

          <text
            x={16}
            y={(chartTop + (height - chartBottom)) / 2}
            transform={`rotate(-90 16 ${(chartTop + (height - chartBottom)) / 2})`}
            textAnchor="middle"
            fontSize="11.5"
            fill="#0f172a"
          >
            Mean
          </text>
          <text x={(chartLeft + plotRightX) / 2} y={height - 8} textAnchor="middle" fontSize="11.5" fill="#0f172a">
            Timestamp (processed_data)
          </text>

          <polyline fill="none" stroke="#0d9488" strokeWidth="2.2" points={polyline} />

          {points.map((p) => {
            const x = toSvgX(p.timeMs);
            const y = toSvgY(p.mean);
            const fill = labelColors[p.label] || labelColors.unknown;
            const labelText = String(p.label);
            const tw = Math.max(36, labelText.length * 7 + 10);
            return (
              <g key={`${p.id ?? "row"}-${p.timeMs}`}>
                <circle cx={x} cy={y} r="5.5" fill={fill} stroke="#fff" strokeWidth="1.5" />
                <line x1={x} y1={y - 6} x2={x} y2={y - 22} stroke={fill} strokeWidth="1.4" />
                <rect
                  x={x - tw / 2}
                  y={y - 40}
                  width={tw}
                  height={16}
                  rx={4}
                  fill={fill}
                  fillOpacity="0.95"
                  stroke="#ffffff"
                  strokeWidth="1"
                />
                <text
                  x={x}
                  y={y - 28}
                  fill="#ffffff"
                  textAnchor="middle"
                  fontSize="10"
                  fontWeight="700"
                >
                  {labelText}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

export default ProcessedMeanTimeline;
