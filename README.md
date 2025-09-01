# Parking Lot Occupancy Tracker

Monorepo scaffold with a **backend** (FastAPI) and **frontend** (Vite + React).

## Quick Start (local dev)

### Prereqs
- Python 3.11+
- Node 18+ (or 20+)
- (Optional) Docker & docker-compose
- (Optional) `gh` (GitHub CLI) logged in for easy repo creation

### 1) Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install --upgrade pip -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API is now at: http://localhost:8000 (docs at /docs)

### 2) Frontend
Open a new terminal:
```bash
cd frontend
npm install
npm run dev
```
App is at: http://localhost:5173

The Vite dev server proxies `/api/*` to the backend on port 8000.

### 3) Docker (optional, one command)
```bash
docker compose up --build
```
Frontend on http://localhost:5173, backend on http://localhost:8000

---

## Initialize a GitHub repo

Option A: Using GitHub CLI
```bash
gh repo create parking-lot-occupancy-tracker --source . --public --push
```

Option B: Manual
```bash
git init
git add .
git commit -m "chore: initial scaffold (backend+frontend)"
git branch -M main
git remote add origin https://github.com/<your-username>/parking-lot-occupancy-tracker.git
git push -u origin main
```

## Project layout
```
.
├─ backend/          # FastAPI service (port 8000)
├─ frontend/         # Vite+React app (port 5173)
├─ .github/workflows # CI
├─ docker-compose.yml
└─ README.md
```

## Next steps
- Define data model for **spots**, **cameras**, and **events**
- Implement `/api/spots` CRUD and simple in-memory store (see TODO in code)
- Add a real database (SQLite → Postgres), and auth if needed
- Wire a basic camera frame ingestion endpoint (multipart or RTSP reader)
- Replace placeholder UI with live occupancy and a camera grid
