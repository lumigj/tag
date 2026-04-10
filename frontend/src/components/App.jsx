import { useEffect, useState } from "react";
import { getProcessedLatest, getRawLatest, getSummary } from "../api/client";
import DeviceStatusCard from "./DeviceStatusCard";
import EventLogTable from "./EventLogTable";
import RawStrengthChart from "./RawStrengthChart";
import PredictionBreakdownChart from "./PredictionBreakdownChart";
import LabeledStrengthTimeline from "./LabeledStrengthTimeline";

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
          getRawLatest(1200),
          getProcessedLatest(200),
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
    const timer = setInterval(refresh, 1000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const liveEvents = processedRows
    .filter((row) => row.label === "drop" || row.label === "pickup")
    .slice(0, 6);
  const latestRaw = rawRows.length ? rawRows[rawRows.length - 1] : null;

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
                <p>Detected events: {liveEvents.map((event) => event.label).join(", ")}</p>
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

      <div className="bottom-row">
        <LabeledStrengthTimeline rawRows={rawRows} processedRows={processedRows} />
      </div>

      <div className="bottom-row">
        <EventLogTable events={processedRows} />
      </div>
    </div>
  );
}

export default App;
