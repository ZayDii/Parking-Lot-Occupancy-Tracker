// src/App.jsx
import React, { useEffect, useState } from "react";
import Dashboard from "./Dashboard";

const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/+$/, "");

export default function App() {
  const [health, setHealth] = useState("...");

  useEffect(() => {
    fetch(`${API_BASE}/api/status`)
      .then((r) => r.json())
      .then((d) => setHealth(String(d?.status ?? "ok")))
      .catch(() => setHealth("error"));
  }, []);

  return (
    <div style={{ fontFamily: "system-ui, sans-serif" }}>
      <header style={{ padding: 20, borderBottom: "1px solid #eee" }}>
        <h1 style={{ margin: 0 }}>Parking Lot Occupancy Tracker</h1>
        <p style={{ margin: "6px 0 0", opacity: 0.8 }}>
          API status: <b>{String(health)}</b>
        </p>
      </header>

      <main style={{ padding: 16, maxWidth: 1200, margin: "0 auto" }}>
        <Dashboard />
        {/* Spots table and Spot Manager removed */}
      </main>
    </div>
  );
}