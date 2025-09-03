import React, { useEffect, useState } from "react";
import { listSpots, createSpot, patchSpot } from "./api";

export default function SpotManager() {
  const [spots, setSpots] = useState([]);
  const [id, setId] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function refresh() {
    try {
      const data = await listSpots();
      setSpots(data);
      setErr("");
    } catch {
      setErr("Failed to load spots.");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onCreate(e) {
    e.preventDefault();
    if (!id.trim() || !label.trim()) return;
    setBusy(true);
    try {
      await createSpot({ id: id.trim(), label: label.trim(), occupied: false });
      setId(""); setLabel("");
      await refresh();
    } catch (e) {
      setErr(e?.response?.data?.detail || "Create failed");
    } finally {
      setBusy(false);
    }
  }

  async function toggleOccupied(spot) {
    setBusy(true);
    try {
      await patchSpot(spot.id, { occupied: !spot.occupied });
      await refresh();
    } catch {
      setErr("Update failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section style={{ padding: 20 }}>
      <h2 style={{ marginTop: 0 }}>Spot Manager</h2>
      {err && <p style={{ color: "crimson" }}>{err}</p>}

      <form onSubmit={onCreate} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <input
          placeholder="id (e.g., A1)"
          value={id}
          onChange={e => setId(e.target.value)}
          style={{ padding: 8, borderRadius: 6, border: "1px solid #ddd" }}
        />
        <input
          placeholder="label"
          value={label}
          onChange={e => setLabel(e.target.value)}
          style={{ padding: 8, borderRadius: 6, border: "1px solid #ddd" }}
        />
        <button disabled={busy} style={{ padding: "8px 12px", borderRadius: 6 }}>
          Add
        </button>
      </form>

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <Th>ID</Th>
            <Th>Label</Th>
            <Th>Occupied</Th>
            <Th>Action</Th>
          </tr>
        </thead>
        <tbody>
          {spots.map(s => (
            <tr key={s.id} style={{ borderTop: "1px solid #eee" }}>
              <Td>{s.id}</Td>
              <Td>{s.label}</Td>
              <Td>{s.occupied ? "Yes" : "No"}</Td>
              <Td>
                <button onClick={() => toggleOccupied(s)} disabled={busy}>
                  Toggle
                </button>
              </Td>
            </tr>
          ))}
          {!spots.length && (
            <tr><Td colSpan={4} style={{ opacity: 0.7, paddingTop: 10 }}>No spots yet.</Td></tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

function Th({ children }) { return <th style={{ textAlign: "left", padding: 8 }}>{children}</th>; }
function Td({ children, ...rest }) { return <td {...rest} style={{ padding: 8 }}>{children}</td>; }
