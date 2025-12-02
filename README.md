# Parking Lot Occupancy Tracker

Smart parking lot occupancy system with:

- **Edge**: Raspberry Pi 5 + Hailo-8L + camera running YOLOv8 and gate-based counting
- **Backend**: FastAPI service (events, occupancy, future analytics)
- **Frontend**: Vite + React dashboard

---

# Repo Layout

```text
.
├─ edge/              # Edge device code (Raspberry Pi + Hailo)
│  ├─ hailo_margin_counter.py  # Main Hailo-based pipeline
│  ├─ margin_core.py           # Shared gate/margin logic
│  ├─ margin_counter.py        # CPU-only version (no Hailo)
│  ├─ run_edge_loop.sh         # Watchdog wrapper (auto-restart / reboot)
│  ├─ events/                  # (optional) edge-side logs
│  └─ state/                   # (optional) edge-side state
├─ backend/           # FastAPI service (port 8000)
├─ frontend/          # Vite+React app (port 5173)
├─ .github/workflows  # CI
├─ docker-compose.yml
└─ README.md
Note: The Hailo SDK (hailo-rpi5-examples) and YOLO weight files (yolov8*.pt) are not committed. They should remain local on the edge device and are ignored via .gitignore.
```

# 1. Edge Device (Raspberry Pi + Hailo-8L)

## Hardware & OS

- Raspberry Pi 5 (or similar)
- Hailo-8 M.2 / PCIe accelerator
- Camera (IMX477 or similar) connected and working with libcamera
- Raspberry Pi OS / Debian Bookworm

## Hailo SDK Prereqs

1. Install HailoRT + hailo-rpi5-examples on the Pi following Hailo’s official guide.
You should end up with something like:

- /home/<user>/hailo-rpi5-examples/

2. In that folder, create and activate the Hailo venv (if not already done):
   
- cd ~/hailo-rpi5-examples
- python3 -m venv venv_hailo_rpi_examples
- source venv_hailo_rpi_examples/bin/activate
- pip install -r requirements.txt

3. Make sure setup_env.sh works:

- source setup_env.sh
   
That should export HAILO_ENV_FILE and other env vars needed by the pipelines.

## Path assumption:

edge/hailo_margin_counter.py currently assumes your Hailo examples are at:
  
- hailo_root = Path("/home/ee96/hailo-rpi5-examples")

## Running the Hailo Margin Counter (with watchdog)

From your Pi:

- cd ~/Parking-Lot-Occupancy-Tracker/edge

## Make the loop script executable (once)

- chmod +x run_edge_loop.sh

## (Recommended) activate the same venv used by Hailo examples

- source ~/hailo-rpi5-examples/venv_hailo_rpi_examples/bin/activate

## Start the edge pipeline with auto-restart / reboot behavior

- ./run_edge_loop.sh


## What this does:
- Cleans up any leftover camera / Hailo users (pkill for old pipelines, rpicam, libcamera, etc.).
- Runs hailo_margin_counter.py with:
  - --input libcamera (IMX477 via libcamerasrc)
  - Gate geometry (G1 / G2) and occupancy seed for your lot
- Uses a watchdog thread inside hailo_margin_counter.py that:
  - Detects when frames stop updating for >10s (frozen pipeline)
  - On 1st & 2nd freeze: simulates your double Ctrl+C → outer loop restarts
  - On 3rd freeze in one boot: exits with code 200
- The outer shell loop (run_edge_loop.sh) interprets exit codes:
  - 130 → manual interrupt (Ctrl+C) → stop loop
  - 139 → segfault (likely Hailo/camera wedge) → retry up to 3 times
  - 200 → watchdog signalled 3 freezes → reboot the Pi

## You’ll see a “User Frame” window on the Pi with:
- Green tracked vehicle boxes with id:<track_id>
- Yellow gate bands (G1/G2) drawn over the frame
- Occupancy HUD at bottom-left (e.g., Occupancy: 10)

## Safety / Notes
- Keep hailo-rpi5-examples/ and YOLO .pt files out of git; only the edge glue code lives in this repo.
- Gate positions, margins, and thresholds are passed via CLI flags in run_edge_loop.sh and shared by both:
  - margin_counter.py (CPU) and
  - hailo_margin_counter.py (Hailo)

# 2. Backend (FastAPI)

Local development on your laptop/desktop:

Prereqs

Python 3.11+

## Setup & Run
- cd backend
- python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
- pip install --upgrade pip -r requirements.txt
- uvicorn app.main:app --reload --port 8000

API is now at: http://localhost:8000

Interactive docs: http://localhost:8000/docs

# 3. Frontend (Vite + React)

Prereqs

- Node 18+ (or 20+)

## Setup & Run
- cd frontend
- npm install
= npm run dev

App is at: http://localhost:5173

The Vite dev server proxies /api/* → backend on port 8000.

## 4. Docker (optional, local all-in-one)
docker compose up --build
- Frontend → http://localhost:5173
- Backend → http://localhost:8000
