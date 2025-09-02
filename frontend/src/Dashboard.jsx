import React, { useEffect, useMemo, useState } from "react";
import { getCurrent, getHistory } from "./api";
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid
} from "recharts";

const LOTS = ["Lot-96N"]; // add more IDs as needed

export default function Dashboard() {
  const [lotId, setLotId] = useState(LOTS[0]);
  const [now, setNow] = useState(null);
  const [series, setSeries] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true); setErr(null);
      try {
        const [cur, hist] = await Promise.all([
          getCurrent(lotId),
          getHistory(lotId, 60),
        ]);
        if (!alive) return;
        setNow(cur);
        setSeries(hist.map(d => ({
          t: d.timestamp,
          occupied: d.spacesOccupied,
          total: d.spacesTotal,
        })));
      } catch (e) {
        setErr(e?.response?.data?.detail || e.message || "Request failed");
      } finally {
        setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [lotId]);

  const percent = useMemo(() => {
    if (!now) return null;
    return Math.round((now.spacesOccupied / now.spacesTotal) * 100);
  }, [now]);

  return (
    <section style={{padding: 20, maxWidth: 1000, margin: "0 auto"}}>
      <h2>Dashboard</h2>

      <div style={{display: "flex", gap: 8, alignItems: "center", marginBottom: 12}}>
        <label>Lot:</label>
        <select value={lotId} onChange={e => setLotId(e.target.value)}>
          {LOTS.map(l => <option key={l} value={l}>{l}</option>)}
        </select>
      </div>

      {err && <div style={{ background: "#fee2e2", color: "#991b1b", padding: 12, borderRadius: 8, marginBottom: 12 }}>
        Error: {err}
      </div>}

      {now && (
        <div style={{display: "grid", gridTemplateColumns: "1fr 2fr", gap: 16, marginBottom: 16}}>
          <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16 }}>
            <h3>Now</h3>
            <div style={{ fontSize: 28, fontWeight: "bold" }}>
              {now.spacesOccupied} / {now.spacesTotal}
            </div>
            <div>{percent}% occupied</div>
            <div style={{ fontSize: 12, color: "#666" }}>
              Updated {new Date(now.timestamp).toLocaleString()}
            </div>
          </div>

          <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16 }}>
            <h3>Last 60 minutes</h3>
            <div style={{ width: "100%", height: 260 }}>
              <ResponsiveContainer>
                <LineChart data={series}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="t" tickFormatter={(t) => new Date(t).toLocaleTimeString()} />
                  <YAxis />
                  <Tooltip labelFormatter={(t) => new Date(t).toLocaleTimeString()} />
                  <Line type="monotone" dataKey="occupied" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}