import { useEffect, useState } from "react";
import { getProcessedLatest, getRawLatest, getSummary } from "../api/client";
import DeviceStatusCard from "./DeviceStatusCard";
import EventLogTable from "./EventLogTable";
import RawStrengthChart from "./RawStrengthChart";
import PredictionBreakdownChart from "./PredictionBreakdownChart";
// import LabeledStrengthTimeline from "./LabeledStrengthTimeline";
import ProcessedMeanTimeline from "./ProcessedMeanTimeline";
import LocatorLiveMap from "./LocatorLiveMap";

const POLL_INTERVAL_MS = 10000;
const RAW_FETCH_LIMIT = 10;
const PROCESSED_FETCH_LIMIT = 10;
const EVENT_LOG_ROWS = 10;

function App() {
  const [deviceState, setDeviceState] = useState(null);
  const [processedRows, setProcessedRows] = useState([]);
  const [rawRows, setRawRows] = useState([]);
  const [errorMessage, setErrorMessage] = useState("");
  const [activeModeCard, setActiveModeCard] = useState("LIVE");

  useEffect(() => {
    let active = true;

    async function refresh() {
      try {
        const [summary, raw, processed] = await Promise.all([
          getSummary(),
          getRawLatest(RAW_FETCH_LIMIT),
          getProcessedLatest(PROCESSED_FETCH_LIMIT),
        ]);
        if (!active) return;
        setDeviceState(summary);
        setRawRows(raw.data || []);
        setProcessedRows((processed.data || []).slice().reverse());
        setErrorMessage("");
      } catch (error) {
        if (!active) return;
        setErrorMessage(String(error?.message || error));
      }
    }

    refresh();
    const timer = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const liveEvents = processedRows
    .filter((row) => row.label === "drop" || row.label === "pickup")
    .slice(0, 6);
  const latestRaw = rawRows.length ? rawRows[rawRows.length - 1] : null;

  function formatDateTime(value) {
    const parsed = Date.parse(String(value));
    if (!Number.isNaN(parsed)) {
      return new Date(parsed).toLocaleString([], {
        year: "2-digit",
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

  return (
    <div className="container">
      <h1>Smart Anti-Loss / Anti-Theft Dashboard</h1>
      {errorMessage ? <p className="error-banner">API error: {errorMessage}</p> : null}

      <div className="top-row">
        <div className="status-stack">
          <DeviceStatusCard deviceState={deviceState} />
          <div className="mode-card-row">
            <button
              type="button"
              className={`mode-card mode-live ${activeModeCard === "LIVE" ? "mode-card-active" : ""}`}
              onClick={() => setActiveModeCard("LIVE")}
            >
              <h3>LIVE</h3>
              {liveEvents.length ? (
                <p>
                  Reporting {liveEvents.length} event(s):
                  {" "}
                  {liveEvents
                    .map((event) => `${event.label}@${formatDateTime(event.timestamp)}`)
                    .join(", ")}
                </p>
              ) : (
                <p>No drop/pickup event reported.</p>
              )}
            </button>

            <button
              type="button"
              className={`mode-card mode-find ${activeModeCard === "FIND" ? "mode-card-active" : ""}`}
              onClick={() => setActiveModeCard("FIND")}
            >
              <h3>FIND</h3>
              <p>
                Live signal from raw_data:
                {latestRaw
                  ? ` strength=${Number(latestRaw.strength).toFixed(0)}, session=${latestRaw.session_id}`
                  : " waiting for samples"}
              </p>
            </button>
          </div>
        </div>
        <PredictionBreakdownChart rows={processedRows} />
      </div>

      <div className="bottom-row">
        <RawStrengthChart data={rawRows} />
      </div>

      {/* Strength timeline (raw strength + processed markers) — disabled
      <div className="bottom-row">
        <LabeledStrengthTimeline rawRows={rawRows} processedRows={processedRows} />
      </div>
      */}

      <div className="bottom-row">
        <ProcessedMeanTimeline processedRows={processedRows} />
      </div>

      {activeModeCard === "FIND" ? (
        <div className="bottom-row">
          <LocatorLiveMap />
        </div>
      ) : null}

      <div className="bottom-row">
        <EventLogTable events={processedRows.slice(0, EVENT_LOG_ROWS)} />
      </div>
    </div>
  );
}

export default App;
