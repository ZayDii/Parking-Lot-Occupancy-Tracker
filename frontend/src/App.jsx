import React from "react";
import Dashboard from "./Dashboard";
import SpotManager from "./SpotManager";

export default function App() {
  return (
    <div style={{fontFamily: "system-ui, sans-serif"}}>
      <header style={{padding: 20, borderBottom: "1px solid #eee"}}>
        <h1 style={{margin: 0}}>Parking Lot Occupancy Tracker</h1>
      </header>

      <main>
        <Dashboard />
        <hr style={{margin: "24px 0"}} />
        <SpotManager />
      </main>
    </div>
  );
}
