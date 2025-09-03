// src/Dashboard.jsx
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

  const pct = useMemo(
    () => (snap ? Math.round(snap.occupancy_rate * 100) : 0),
    [snap]
  );

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
          <button className="btn" onClick={refresh} title="Refresh now">⟳ Refresh</button>
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
              <KV label="Edge last seen" value={status.edge_last_seen_iso || "—"} />
              <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
                <span className={`badge ${true ? "ok" : "bad"}`}>API</span>
                <span className={`badge ${status.cameras_online ? "ok" : "bad"}`}>Cameras</span>
                <span className={`badge ${status.edge_last_seen_iso ? "ok" : "bad"}`}>Edge</span>
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
  // Keep a stable viewBox so labels stay legible; let the parent "media" box control sizing.
  const width = 420, height = 160;
  const pad = { l: 24, r: 28, t: 10, b: 24 };
  const w = width - pad.l - pad.r, h = height - pad.t - pad.b;
  const bw = Math.max(6, Math.floor(w / data.length) - 6);

  return (
    <svg
      role="img"
      aria-label="Forecast"
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      height="100%"
      preserveAspectRatio="xMidYMid meet"
    >
      <line
        x1={pad.l}
        y1={pad.t + h}
        x2={pad.l + w}
        y2={pad.t + h}
        stroke="#e5e7eb"
        strokeWidth="1"
      />
      {data.map((pt, i) => {
        const x = pad.l + i * (w / data.length);
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
                x={x + 2}
                y={pad.t + h + 16}
                fontSize="10"
                fill="#6b7280"
              >
                {hhmm}
              </text>
            )}
          </g>
        );
      })}
      {[0, 25, 50, 75, 100].map((p) => (
        <text
          key={p}
          x={pad.l + w + 6}
          y={pad.t + (h - (p / 100) * h)}
          fontSize="10"
          fill="#9ca3af"
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
