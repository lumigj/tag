import { useEffect, useMemo, useRef, useState } from "react";
import { getLocatorLatest, postLocatorOffset } from "../api/client";

const LOCATOR_UI_THROTTLE_MS = 5000;

function LocatorLiveMap() {
  const [locator, setLocator] = useState(null);
  const [streamError, setStreamError] = useState("");
  const [offsetInput, setOffsetInput] = useState("0");
  const [offsetError, setOffsetError] = useState("");
  const latestLocatorRef = useRef(null);

  useEffect(() => {
    let active = true;
    let es = null;

    async function init() {
      try {
        const snapshot = await getLocatorLatest();
        if (active) {
          setLocator(snapshot);
          latestLocatorRef.current = snapshot;
          if (snapshot.offset !== undefined && snapshot.offset !== null) {
            setOffsetInput(String(snapshot.offset));
          }
        }
      } catch (error) {
        if (active) setStreamError(String(error?.message || error));
      }

      es = new EventSource("/api/locator/stream");
      es.addEventListener("locator", (event) => {
        if (!active) return;
        try {
          const parsed = JSON.parse(event.data);
          latestLocatorRef.current = parsed;
          setStreamError("");
        } catch {
          setStreamError("Invalid locator stream payload");
        }
      });
      es.onerror = () => {
        if (active) setStreamError("Locator stream disconnected. Retrying...");
      };
    }

    init();

    const flushInterval = setInterval(() => {
      if (!active) return;
      const latest = latestLocatorRef.current;
      if (latest) setLocator(latest);
    }, LOCATOR_UI_THROTTLE_MS);

    return () => {
      active = false;
      clearInterval(flushInterval);
      if (es) es.close();
    };
  }, []);

  const beacons = locator?.beacons || {
    B1: [0.15, 0.14],
    B2: [0.85, 0.14],
    B3: [0.5, 0.746218],
  };
  const mode = locator?.mode === 3 ? 3 : 2;
  const current = locator?.current || { x: 0.5, y: 0.34, color: "#adb5bd" };
  const trail = locator?.trail || [];

  const toSvg = useMemo(() => {
    const width = 640;
    const height = 320;
    const margin = 28;
    return {
      width,
      height,
      mapX: (x) => margin + x * (width - margin * 2),
      mapY: (y) => height - (margin + y * (height - margin * 2)),
    };
  }, []);

  const statusText = locator?.status_text || "Waiting for MQTT locator data...";
  const preview = locator?.offset_preview;

  async function applyOffset() {
    setOffsetError("");
    try {
      const next = Number(offsetInput.trim());
      if (!Number.isFinite(next)) {
        setOffsetError("Offset must be a number.");
        return;
      }
      const snapshot = await postLocatorOffset(next);
      latestLocatorRef.current = snapshot;
      setLocator(snapshot);
      setOffsetInput(String(snapshot.offset));
    } catch (err) {
      setOffsetError(String(err?.message || err));
    }
  }

  return (
    <div className="card">
      <h2>FIND Locator (Live)</h2>
      <p className="timeline-note">
        {locator?.connected ? "MQTT connected" : "MQTT disconnected"} | tag: {locator?.tag_id || "tag-1"}
      </p>
      <p className="timeline-note locator-offset-hint">
        Distance proxy = max(abs(rssi) - offset, 0). Same solve as <code>pc/subscriber_calibrated.py</code>.
      </p>
      <div className="locator-offset-row">
        <label className="locator-offset-label" htmlFor="locator-offset-input">
          Offset
        </label>
        <input
          id="locator-offset-input"
          type="text"
          inputMode="decimal"
          className="locator-offset-input"
          value={offsetInput}
          onChange={(e) => setOffsetInput(e.target.value)}
        />
        <button type="button" className="locator-offset-apply" onClick={() => void applyOffset()}>
          Apply offset
        </button>
        {preview ? (
          <span className="locator-offset-preview">
            -21 → {preview.rssi_neg21}, -50 → {preview.rssi_neg50}
          </span>
        ) : null}
      </div>
      {offsetError ? <p className="error-banner">{offsetError}</p> : null}
      {streamError ? <p className="error-banner">{streamError}</p> : null}

      <svg
        width={toSvg.width}
        height={toSvg.height}
        viewBox={`0 0 ${toSvg.width} ${toSvg.height}`}
        className="chart"
      >
        <rect x="0" y="0" width={toSvg.width} height={toSvg.height} fill="#f8fafc" rx="8" />

        <line
          x1={toSvg.mapX(beacons.B1[0])}
          y1={toSvg.mapY(beacons.B1[1])}
          x2={toSvg.mapX(beacons.B2[0])}
          y2={toSvg.mapY(beacons.B2[1])}
          stroke="#264653"
          strokeWidth="3"
        />
        {mode === 3 ? (
          <>
            <line
              x1={toSvg.mapX(beacons.B1[0])}
              y1={toSvg.mapY(beacons.B1[1])}
              x2={toSvg.mapX(beacons.B3[0])}
              y2={toSvg.mapY(beacons.B3[1])}
              stroke="#264653"
              strokeWidth="3"
            />
            <line
              x1={toSvg.mapX(beacons.B2[0])}
              y1={toSvg.mapY(beacons.B2[1])}
              x2={toSvg.mapX(beacons.B3[0])}
              y2={toSvg.mapY(beacons.B3[1])}
              stroke="#264653"
              strokeWidth="3"
            />
          </>
        ) : null}

        {Object.entries(beacons).map(([label, point]) => {
          const inactive = mode === 2 && label === "B3";
          return (
            <g key={label}>
              <rect
                x={toSvg.mapX(point[0]) - 9}
                y={toSvg.mapY(point[1]) - 9}
                width="18"
                height="18"
                fill={inactive ? "#cbd5e1" : "#1d3557"}
                stroke="#111827"
                strokeWidth="1"
                rx="2"
              />
              <text x={toSvg.mapX(point[0])} y={toSvg.mapY(point[1]) - 14} textAnchor="middle" fontSize="11" fontWeight="700">
                {label}
              </text>
            </g>
          );
        })}

        {trail.slice(0, -1).map((p, idx) => (
          <circle
            key={`${idx}-${p.x}-${p.y}`}
            cx={toSvg.mapX(p.x)}
            cy={toSvg.mapY(p.y)}
            r={4 + Math.floor((idx / Math.max(1, trail.length - 1)) * 4)}
            fill="#94a3b8"
            opacity="0.35"
          />
        ))}

        <circle
          cx={toSvg.mapX(current.x)}
          cy={toSvg.mapY(current.y)}
          r="11"
          fill={current.color || "#457b9d"}
          stroke="#111827"
          strokeWidth="1.2"
        />
      </svg>

      <pre className="locator-status">{statusText}</pre>
    </div>
  );
}

export default LocatorLiveMap;
