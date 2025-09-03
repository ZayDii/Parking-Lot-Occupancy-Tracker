// src/Dashboard.jsx
import React, { useEffect, useMemo, useState } from "react";
import { getSnapshot, getForecast, getStatus } from "./api";

const LOT_ID = "96N";
const POLL_MS = 5000;

function fmtETDateTime(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  const tz = 'America/New_York';

  const date = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, year: 'numeric', month: 'short', day: '2-digit'
  }).format(d);

  const time = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, hour: 'numeric', minute: '2-digit', hour12: true
  }).format(d);

  const abbr = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, timeZoneName: 'short'
  }).formatToParts(d).find(p => p.type === 'timeZoneName')?.value || 'ET';

  return { date, time, abbr }; // e.g. { date: "Sep 02, 2025", time: "10:20 PM", abbr: "EDT" }
}

export default function Dashboard() {
  const [snap, setSnap] = useState(null);
  const [forecast, setForecast] = useState([]);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [justRefreshed, setJustRefreshed] = useState(false);


  async function refresh(manual = false) {
  if (manual && isRefreshing) return;      // prevent double-click spams
  if (manual) setIsRefreshing(true);

  let ok = false;
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
    ok = true;
  } catch {
    setError("Backend unavailable or no data yet.");
  } finally {
    if (manual) {
      setIsRefreshing(false);
      if (ok) {
        setJustRefreshed(true);
        setTimeout(() => setJustRefreshed(false), 900);
      }
    }
  }
}


  useEffect(() => {
    refresh(false);
    const id = setInterval(() => refresh(false), POLL_MS);
    return () => clearInterval(id);
}, []);


  const pct = useMemo(
    () => (snap ? Math.round(snap.occupancy_rate * 100) : 0),
    [snap]
  );

const apiOk = Boolean(status?.api_ok ?? true);
const camsOk = Number(status?.cameras_online) > 0;
const edgeAgeMin = status?.edge_last_seen_iso
  ? Math.floor((Date.now() - Date.parse(status.edge_last_seen_iso)) / 60000)
  : Infinity;
const edgeClass = !status?.edge_last_seen_iso ? "bad" : edgeAgeMin > 15 ? "warn" : "ok";

  return (
    <section className="container">
      {/* Header uses the responsive toolbar pattern */}
      <header className="header toolbar" style={{ marginBottom: 12 }}>
        <div>
          <h2 className="h2" style={{ margin: 0 }}>Dashboard</h2>
          <p className="subtle" style={{ margin: "4px 0 0" }}>
            Lot: <b>{LOT_ID}</b>
          </p>
        </div>
        <div className="row">
         <button
  className={`btn btn-primary btn-lg ${isRefreshing ? 'is-loading' : ''}`}
  onClick={() => refresh(true)}
  disabled={isRefreshing}
  aria-busy={isRefreshing}
  title={isRefreshing ? 'Refreshing…' : 'Refresh now'}
>
  {/* Use your favicon here; .svg/.png/.ico all fine */}
  <img className="icon-img" src="/arrow.png" alt="" />

  <span className="btn-label">
    {isRefreshing ? 'Refreshing…' : justRefreshed ? 'Refreshed' : 'Refresh'}
  </span>
</button>


        </div>
      </header>

      {error && (
        <p style={{ color: "crimson", marginBottom: 16 }}>{error}</p>
      )}

      {/* Responsive grid: 1 col on mobile, 2 on small tablets, 3 on desktop */}
      <section className="grid">
        {/* Card 1 — Now */}
        <Card title="Now">
          {snap ? (
            <div
              style={{
                display: "grid",
                gap: 12,
                gridTemplateColumns: "minmax(120px,160px) 1fr",
                alignItems: "center",
              }}
            >
              <Gauge percent={pct} />
              <div>
                <KV label="Occupied" value={<b>{snap.occupied_count}</b>} right={` / ${snap.total_spots}`} />
                <Progress percent={pct} />
                <p className="subtle" style={{ marginTop: 8 }}>
                  Updated: {snap.ts_iso}
                </p>
              </div>
            </div>
          ) : (
            <Skeleton lines={4} />
          )}
        </Card>

        {/* Card 2 — Next 12 hours */}
        <Card title="Next 12 hours">
          {forecast.length ? (
            <div className="media">
              {/* Make the SVG fill the responsive media box */}
              <MiniBars data={forecast} />
            </div>
          ) : (
            <Skeleton lines={6} />
          )}
        </Card>

        {/* Card 3 — System */}
        <Card title="System">
          {status ? (
            <div style={{ display: "grid", gap: 8 }}>
              <KV label="Uptime" value={fmtUptime(status.service_uptime_s)} />
              <KV label="Cameras online" value={String(status.cameras_online)} />
              {(() => {
  const et = status?.edge_last_seen_iso ? fmtETDateTime(status.edge_last_seen_iso) : null;
  return (
    <KV
      label="Edge last seen"
      value={
        et ? (
          <div className="kv-stack">
            <div className="mono">{et.date}</div>
            <div className="mono">
  {et.time} <span className="tz">{et.abbr.replace('GMT','ET')}</span></div>
          </div>
        ) : "—"
      }
    />
  );
})()}
              <div className="badges">
  <span className={`badge ${apiOk ? "ok" : "bad"}`}><span className="dot" />API</span>
  <span className={`badge ${camsOk ? "ok" : "bad"}`}><span className="dot" />Cameras</span>
  <span className={`badge ${edgeClass}`}><span className="dot" />Edge</span>
</div>
            </div>
          ) : (
            <Skeleton lines={4} />
          )}
        </Card>
      </section>
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

function KV({ label, value, right }) {
  return (
    <div className="kv" style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
      <span className="label" style={{ color: "var(--muted)" }}>{label}</span>
      <span className="value">
        {value}{right ?? null}
      </span>
    </div>
  );
}

function Progress({ percent }) {
  return (
    <div
      className="progress"
      aria-label="Occupancy progress"
      style={{
        marginTop: 8,
        width: "100%",
        height: 10,
        borderRadius: 999,
        background: "rgba(255,255,255,0.08)",
        overflow: "hidden",
        border: "1px solid var(--border)",
      }}
    >
      <span
        style={{
          display: "block",
          width: `${percent}%`,
          height: "100%",
          background: "var(--accent)",
        }}
      />
    </div>
  );
}

function Skeleton({ lines = 3 }) {
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="skel"
          style={{
            height: 12,
            borderRadius: 8,
            background:
              "linear-gradient(90deg, rgba(255,255,255,0.04), rgba(255,255,255,0.12), rgba(255,255,255,0.04))",
            backgroundSize: "200% 100%",
            animation: "shimmer 1.2s linear infinite",
          }}
        />
      ))}
    </div>
  );
}

function Gauge({ percent }) {
  const clamped = Math.min(100, Math.max(0, percent));
  const deg = clamped * 3.6;
  return (
    <div
      className="gauge-ring"
      style={{
        width: 140,
        aspectRatio: "1 / 1",
        borderRadius: "50%",
        background: `conic-gradient(var(--accent) ${deg}deg, #e5e7eb 0deg)`,
        display: "grid",
        placeItems: "center",
      }}
    >
      <div
        className="gauge-inner"
        style={{
          width: "72%",
          aspectRatio: "1 / 1",
          borderRadius: "50%",
          background: "var(--card)",
          display: "grid",
          placeItems: "center",
          border: "1px solid var(--border)",
        }}
      >
        <div className="gauge-num" style={{ fontWeight: 700, fontSize: 20 }}>
          {clamped}%
        </div>
        <div className="gauge-sub" style={{ fontSize: 12, opacity: 0.7 }}>
          full
        </div>
      </div>
    </div>
  );
}

function MiniBars({ data }) {
  // ViewBox stays fixed; parent .media controls actual size.
  const width = 420, height = 160;

  // Reserve a right gutter for labels so they don't overlap bars.
  const pad = { l: 24, r: 64, t: 12, b: 24 };

  const w = width - pad.l - pad.r;
  const h = height - pad.t - pad.b;
  const step = w / data.length;
  const bw = Math.max(6, Math.floor(step) - 6);

  const axisRightX = pad.l + w;

  return (
    <svg
      role="img"
      aria-label="Forecast"
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      height="100%"
      preserveAspectRatio="xMidYMid meet"
    >
      {/* X-axis */}
      <line
        x1={pad.l}
        y1={pad.t + h}
        x2={axisRightX}
        y2={pad.t + h}
        stroke="#e5e7eb"
        strokeWidth="1"
        shapeRendering="crispEdges"
      />

      {/* Bars */}
      {data.map((pt, i) => {
        const x = pad.l + i * step;
        const v = Math.max(0, Math.min(1, pt.expected_occupancy_rate));
        const barH = v * h;
        const y = pad.t + (h - barH);
        const hhmm = pt.ts_iso.slice(11, 16);
        return (
          <g key={pt.ts_iso}>
            <rect
              x={x}
              y={y}
              width={bw}
              height={barH}
              rx="4"
              ry="4"
              fill="var(--accent)"
              opacity="0.9"
            />
            {i % 3 === 0 && (
              <text
                x={x + bw / 2}
                y={pad.t + h + 16}
                fontSize="10"
                fill="#6b7280"
                textAnchor="middle"
              >
                {hhmm}
              </text>
            )}
          </g>
        );
      })}

      {/* Y-axis labels in the right gutter (outside the plot) */}
      {[0, 25, 50, 75, 100].map((p) => (
        <text
          key={p}
          x={axisRightX + 6}           // <- outside the plot
          y={pad.t + (h - (p / 100) * h)}
          fontSize="10"
          fill="#9ca3af"
          textAnchor="start"           // align to the left edge
          dominantBaseline="middle"
        >
          {p}%
        </text>
      ))}
    </svg>
  );
}


/* ---------- helpers ---------- */
function fmtUptime(s) {
  const d = Math.floor(s / 86400),
    h = Math.floor((s % 86400) / 3600),
    m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h ${m}m`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}
