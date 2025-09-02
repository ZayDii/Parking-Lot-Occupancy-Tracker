import React, { useEffect, useState } from "react";
import { getHealth, listSpots, createSpot, patchSpot } from "./api";

export default function SpotManager() {
  const [health, setHealth] = useState("...");
  const [spots, setSpots] = useState([]);
  const [label, setLabel] = useState("");

  useEffect(() => {
    getHealth().then(d => setHealth(d.status)).catch(() => setHealth("error"));
    listSpots().then(setSpots).catch(() => setSpots([]));
  }, []);

  const addSpot = async () => {
    const id = label.trim().toLowerCase().replace(/\s+/g, "-");
    if (!id) return;
    try {
      const created = await createSpot({ id, label, occupied: false });
      setSpots(prev => [...prev, created]);
      setLabel("");
    } catch {
      alert("Failed to create spot (maybe duplicate id?)");
    }
  };

  const toggle = async (id, occupied) => {
    try {
      const updated = await patchSpot(id, { id, occupied: !occupied });
      setSpots(prev => prev.map(s => (s.id === id ? updated : s)));
    } catch {}
  };

  return (
    <section style={{fontFamily: "system-ui, sans-serif", padding: 20, maxWidth: 1000, margin: "0 auto"}}>
      <h2>Spot Manager</h2>
      <p>Backend health: <strong>{health}</strong></p>

      <div style={{display: "flex", gap: 8, marginBottom: 16}}>
        <input
          placeholder="New spot label (e.g., A1)"
          value={label}
          onChange={e => setLabel(e.target.value)}
          style={{padding: 8, flex: 1}}
        />
        <button onClick={addSpot} style={{padding: 8}}>Add spot</button>
      </div>

      <ul style={{listStyle: "none", padding: 0, display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 12}}>
        {spots.map(s => (
          <li key={s.id} style={{border: "1px solid #ddd", borderRadius: 8, padding: 12}}>
            <div style={{display: "flex", justifyContent: "space-between", alignItems: "center"}}>
              <strong>{s.label}</strong>
              <span style={{fontSize: 12, opacity: 0.7}}>{s.occupied ? "Occupied" : "Free"}</span>
            </div>
            <button onClick={() => toggle(s.id, s.occupied)} style={{marginTop: 8, padding: 8, width: "100%"}}>
              Toggle
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}