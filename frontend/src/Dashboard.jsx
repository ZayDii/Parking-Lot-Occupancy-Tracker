import React, { useEffect, useMemo, useState } from "react";
import { getSnapshot, getForecast, getStatus } from "./api";

const LOT_ID = "96N";
const POLL_MS = 5000;

export default function Dashboard() {
  const [snap, setSnap] = useState(null);
  const [forecast, setForecast] = useState([]);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      const [s, f, st] = await Promise.all([
        getSnapshot(LOT_ID),
        getForecast(LOT_ID, 12),
        getStatus(),
      ]);
      setSnap(s);
      setForecast(f.points || []);
      setStatus(st);
      setError("");
    } catch {
      setError("Backend unavailable or no data yet.");
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, []);

  const pct = useMemo(() => (snap ? Math.round(snap.occupancy_rate * 100) : 0), [snap]);

  return (
    <section className="container">
      <div className="header-row">
        <div>
          <h2 className="h2">Dashboard</h2>
          <p className="subtle">Lot: <b>{LOT_ID}</b></p>
        </div>
        <button className="btn-ghost" onClick={refresh} title="Refresh now">⟳ Refresh</button>
      </div>

      {error && <p style={{ color: "crimson", marginBottom: 10 }}>{error}</p>}

      <div className="grid-3">
        <Card title="Now">
          {snap ? (
            <div className="now-grid">
              <Gauge percent={pct} />
              <div>
                <div className="kv"><span className="label">Occupied</span><span className="value"><b>{snap.occupied_count}</b> / {snap.total_spots}</span></div>
                <div className="progress" aria-label="Occupancy progress">
                  <span style={{ width: `${pct}%` }} />
                </div>
                <p className="updated">Updated: {snap.ts_iso}</p>
              </div>
            </div>
          ) : <Skeleton lines={4} />}
        </Card>

        <Card title="Next 12 hours">
          {forecast.length ? <MiniBars data={forecast} /> : <Skeleton lines={6} />}
        </Card>

        <Card title="System">
          {status ? (
            <div style={{ display: "grid", gap: 8 }}>
              <KV label="Uptime" value={fmtUptime(status.service_uptime_s)} />
              <KV label="Cameras online" value={String(status.cameras_online)} />
              <KV label="Edge last seen" value={status.edge_last_seen_iso || "—"} />
              <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                <span className={`badge ${true ? 'ok' : 'bad'}`}>API</span>
                <span className={`badge ${status.cameras_online ? 'ok' : 'bad'}`}>Cameras</span>
                <span className={`badge ${status.edge_last_seen_iso ? 'ok' : 'bad'}`}>Edge</span>
              </div>
            </div>
          ) : <Skeleton lines={4} />}
        </Card>
      </div>
    </section>
  );
}

/* ---------- UI bits ---------- */

function Card({ title, children }) {
  return (
    <div className="card">
      <h3 style={{ marginTop: 0, fontSize: 16 }}>{title}</h3>
      {children}
    </div>
  );
}

function KV({ label, value }) {
  return (
    <div className="kv">
      <span className="label">{label}</span>
      <span className="value">{value}</span>
    </div>
  );
}

function Skeleton({ lines = 3 }) {
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {Array.from({ length: lines }).map((_, i) => <div key={i} className="skel" />)}
    </div>
  );
}

function Gauge({ percent }) {
  const deg = Math.min(100, Math.max(0, percent)) * 3.6;
  return (
    <div className="gauge-ring" style={{ background: `conic-gradient(var(--brand) ${deg}deg, #e5e7eb 0deg)` }}>
      <div className="gauge-inner">
        <div className="gauge-num">{percent}%</div>
        <div className="gauge-sub">full</div>
      </div>
    </div>
  );
}

function MiniBars({ data }) {
  const width = 420, height = 160;
  const pad = { l: 24, r: 12, t: 10, b: 22 };
  const w = width - pad.l - pad.r, h = height - pad.t - pad.b;
  const bw = Math.max(6, Math.floor(w / data.length) - 6);

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Forecast">
      <line x1={pad.l} y1={pad.t + h} x2={pad.l + w} y2={pad.t + h} stroke="#e5e7eb" strokeWidth="1" />
      {data.map((pt, i) => {
        const x = pad.l + i * (w / data.length);
        const v = Math.max(0, Math.min(1, pt.expected_occupancy_rate));
        const barH = v * h;
        const y = pad.t + (h - barH);
        const hhmm = pt.ts_iso.slice(11, 16);
        return (
          <g key={pt.ts_iso}>
            <rect x={x} y={y} width={bw} height={barH} rx="4" ry="4" fill="var(--brand)" opacity="0.9" />
            {i % 3 === 0 && <text x={x + 2} y={pad.t + h + 16} fontSize="10" fill="#6b7280">{hhmm}</text>}
          </g>
        );
      })}
      {[0,25,50,75,100].map(p => (
        <text key={p} x={pad.l + w + 6} y={pad.t + (h - (p/100)*h)} fontSize="10" fill="#9ca3af">{p}%</text>
      ))}
    </svg>
  );
}

/* ---------- helpers ---------- */
function fmtUptime(s) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h ${m}m`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}
