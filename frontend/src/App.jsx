import React, { useEffect, useState } from 'react'

export default function App() {
  const [health, setHealth] = useState('...')
  const [spots, setSpots] = useState([])
  const [label, setLabel] = useState('')

  useEffect(() => {
    fetch('/api/health').then(r => r.json()).then(d => setHealth(d.status)).catch(() => setHealth('error'))
    fetch('/api/spots').then(r => r.json()).then(setSpots).catch(() => setSpots([]))
  }, [])

  const addSpot = async () => {
    const id = label.trim().toLowerCase().replace(/\s+/g, '-')
    if (!id) return
    const res = await fetch('/api/spots', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ id, label, occupied: false })
    })
    if (res.ok) {
      const created = await res.json()
      setSpots(prev => [...prev, created])
      setLabel('')
    } else {
      alert('Failed to create spot (maybe duplicate id?)')
    }
  }

  const toggle = async (id, occupied) => {
    const res = await fetch(`/api/spots/${id}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ id, occupied: !occupied })
    })
    if (res.ok) {
      const updated = await res.json()
      setSpots(prev => prev.map(s => s.id === id ? updated : s))
    }
  }

  return (
    <div style={{fontFamily: 'system-ui, sans-serif', padding: 20, maxWidth: 800, margin: '0 auto'}}>
      <h1>Parking Lot Occupancy</h1>
      <p>Backend health: <strong>{health}</strong></p>

      <div style={{display: 'flex', gap: 8, marginBottom: 16}}>
        <input
          placeholder="New spot label (e.g., A1)"
          value={label}
          onChange={e => setLabel(e.target.value)}
          style={{padding: 8, flex: 1}}
        />
        <button onClick={addSpot} style={{padding: 8}}>Add spot</button>
      </div>

      <ul style={{listStyle: 'none', padding: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 12}}>
        {spots.map(s => (
          <li key={s.id} style={{border: '1px solid #ddd', borderRadius: 8, padding: 12}}>
            <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
              <strong>{s.label}</strong>
              <span style={{fontSize: 12, opacity: 0.7}}>{s.occupied ? 'Occupied' : 'Free'}</span>
            </div>
            <button onClick={() => toggle(s.id, s.occupied)} style={{marginTop: 8, padding: 8, width: '100%'}}>
              Toggle
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
