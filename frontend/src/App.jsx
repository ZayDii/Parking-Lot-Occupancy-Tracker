import React, { useEffect, useState } from "react";
import Dashboard from "./Dashboard";
import SpotManager from "./SpotManager";

const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/+$/, "");

function SpotsTable({ spots }) {
  return (
    <table className="table">
      <thead>
        <tr>
          <th>Spot</th>
          <th>Status</th>
          <th>Updated</th>
        </tr>
      </thead>
      <tbody>
        {spots.map((s, i) => (
          <tr key={s.id ?? s.spotId ?? i}>
            <td data-label="Spot">{s.id ?? s.spotId ?? "—"}</td>
            <td data-label="Status">{s.occupied ? "Occupied" : "Open"}</td>
            <td data-label="Updated">
              {s.timestamp ? new Date(s.timestamp).toLocaleString() : "—"}
            </td>
          </tr>
        ))}
        {spots.length === 0 && (
          <tr>
            <td data-label="Spot" colSpan={3}>No spots to show</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

export default function App() {
  const [health, setHealth] = useState("...");
  const [spots, setSpots] = useState([]);

  useEffect(() => {
    // Replaces deprecated /api/health with /api/status
    fetch(`${API_BASE}/api/status`)
      .then(r => r.json())
      .then(d => setHealth(String(d?.status ?? "ok")))
      .catch(() => setHealth("error"));

    fetch(`${API_BASE}/api/spots`)
      .then(r => r.json())
      .then(setSpots)
      .catch(() => setSpots([]));
  }, []);

  const refreshSpots = () => {
    fetch(`${API_BASE}/api/spots`)
      .then(r => r.json())
      .then(setSpots)
      .catch(() => {});
  };

  return (
    <div style={{ fontFamily: "system-ui, sans-serif" }}>
      <header style={{ padding: 20, borderBottom: "1px solid #eee" }}>
        <h1 style={{ margin: 0 }}>Parking Lot Occupancy Tracker</h1>
        <p style={{ margin: "6px 0 0", opacity: 0.8 }}>
          API status: <b>{String(health)}</b>
        </p>
      </header>

      <main style={{ padding: 16, maxWidth: 1200, margin: "0 auto" }}>
        <Dashboard health={health} spots={spots} />

        {/* Mobile-responsive table (stacks on phones) */}
        <section style={{ marginTop: 24 }}>
          <h2 style={{ margin: "0 0 8px" }}>Spots</h2>
          <SpotsTable spots={spots} />
        </section>

        <hr style={{ margin: "24px 0" }} />

        <SpotManager onSaved={refreshSpots} />
      </main>
    </div>
  );
}