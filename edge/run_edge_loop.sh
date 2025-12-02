#!/bin/bash
set -euo pipefail

PROJECT_DIR="/home/ee96/Parking-Lot-Occupancy-Tracker/edge"
HAILO_ENV="/home/ee96/hailo-rpi5-examples/setup_env.sh"

# Workaround for setup_env.sh expecting ZSH_VERSION under set -u
export ZSH_VERSION=""
export PYTHONPATH="${PYTHONPATH-}"

# Edge → backend config
export EDGE_LOT_ID="96N"
export EDGE_CAMERA_ID="96N-east-1"

# Use the same origin you configured as VITE_API_BASE for the frontend
# export EDGE_INGEST_URL="https://your-backend-domain.com/api/ingest/detections"

# Optional: if the backend requires a Bearer token
# export EDGE_API_KEY="your-api-key"
# Optional: custom DB path (otherwise defaults to ~/edge_data/edge_events.db)
# export EDGE_DB_PATH="/home/ee96/Parking-Lot-Occupancy-Tracker/edge/edge_events.db"

# ---------- New scheduling + occupancy TTL config ----------
# Time-of-day (local time) when the edge loop is allowed to start
START_AT_HOUR=6   # 6 AM
START_AT_MIN=0

# How long a "last occupancy" value is considered valid (seconds)
OCC_TTL_SEC=600   # 10 minutes

# Where hailo_margin_counter.py writes last occupancy
LAST_STATE_FILE="$PROJECT_DIR/state/last.json"
# -----------------------------------------------------------

wait_until_start_time() {
  local target_h="$1"
  local target_m="$2"

  while true; do
    local now_h now_m
    now_h=$(date +%H)
    now_m=$(date +%M)

    # If current time >= target time, we can start
    if ((10#$now_h > 10#$target_h)) || { ((10#$now_h == 10#$target_h)) && ((10#$now_m >= 10#$target_m)); }; then
      echo "Current time $(date +%H:%M) is past scheduled start ${target_h}:${target_m} – starting edge loop."
      break
    fi

    local now_total=$((10#$now_h * 60 + 10#$now_m))
    local tgt_total=$((10#$target_h * 60 + 10#$target_m))
    local remaining_min=$((tgt_total - now_total))

    echo "Waiting for start time ${target_h}:${target_m} (current $(date +%H:%M), ~${remaining_min} min remaining)..."
    sleep 30
  done
}

compute_seed_and_bootstrap() {
  # Defaults: no prior state → do bootstrap scan
  BOOTSTRAP_SECS=10
  SEED_OCC=0

  if [[ ! -f "$LAST_STATE_FILE" ]]; then
    echo "[SEED] No $LAST_STATE_FILE found – using bootstrap scan."
    return 0
  fi

  # Ask Python to read last.json and output: "<occupancy> <age_seconds>"
  local result
  result=$(LAST_STATE_FILE="$LAST_STATE_FILE" python3 - << 'EOF'
import json, os, sys
from datetime import datetime, timezone

path = os.environ.get("LAST_STATE_FILE")
if not path:
    sys.exit(0)

try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    ts = data.get("ts")
    occ = int(data.get("occupancy", 0))
    if not ts:
        raise ValueError("missing ts")

    # Handle ISO timestamps like "2025-11-27T05:23:54.123456+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    age = (datetime.now(timezone.utc) - dt).total_seconds()
    print(f"{occ} {int(age)}")
except Exception:
    # On any error, just print nothing (shell will treat it as "no state")
    pass
sys.exit(0)
EOF
)

  if [[ -z "${result:-}" ]]; then
    echo "[SEED] Could not read a valid last state – using bootstrap scan."
    return 0
  fi

  local occ age
  read -r occ age <<< "$result"

  if [[ -z "${occ:-}" || -z "${age:-}" ]]; then
    echo "[SEED] Parsed state is incomplete – using bootstrap scan."
    return 0
  fi

  if (( age <= OCC_TTL_SEC )); then
    BOOTSTRAP_SECS=0
    SEED_OCC="$occ"
    echo "[SEED] Using recent occupancy=${SEED_OCC} (age=${age}s ≤ ${OCC_TTL_SEC}s) – skipping bootstrap scan."
  else
    echo "[SEED] Last state is too old (age=${age}s > ${OCC_TTL_SEC}s) – using bootstrap scan."
    BOOTSTRAP_SECS=10
    SEED_OCC=0
  fi
}

# Clear watchdog counter from previous runs
rm -f /tmp/hailo_edge_watchdog_count 2>/dev/null || true

# Wait until scheduled start time (only once, on process start)
wait_until_start_time "$START_AT_HOUR" "$START_AT_MIN"

# 1) Activate Hailo env / venv (must be run from hailo-rpi5-examples dir)
cd /home/ee96/hailo-rpi5-examples
source "$HAILO_ENV"

cd "$PROJECT_DIR" || exit 1

cleanup_procs() {
  echo "Cleaning up leftover processes..."
  # Kill any old edge scripts that might still be around
  pkill -f "hailo_margin_counter.py" 2>/dev/null || true

  # Kill common camera users just in case
  pkill -f "rpicam-hello" 2>/dev/null || true
  pkill -f "libcamera-vid" 2>/dev/null || true
  pkill -f "libcamera-still" 2>/dev/null || true

  # Give the kernel/driver a few seconds to release the camera + Hailo
  sleep 5
}

MAX_SEGFAULTS=3
segfaults=0

while true; do
  cleanup_procs

  # Decide seed + bootstrap behavior for this run
  compute_seed_and_bootstrap

  echo "=== Starting Hailo margin counter at $(date) ==="
  python3 hailo_margin_counter.py \
    --input rpi \
    --use-frame \
    --flip_user_frame \
    --bootstrap_secs "${BOOTSTRAP_SECS}" \
    --bootstrap_offset 0 \
    --yref center \
    --min_speed 0.5 \
    --max_speed_px_s 2000 \
    --hyst_px 3 \
    --cooldown_s 5 \
    --min_box_w 4 \
    --min_box_h 4 \
    --max_capacity 73 \
    --seed_occupancy "${SEED_OCC}" \
    --debug_hits

  status=$?
  echo "=== margin_counter exited with code $status at $(date) ==="

  # Ctrl+C: let you stop everything (you can still use your 2x Ctrl+C habit)
  if [[ $status -eq 130 ]]; then
    echo "Detected manual interrupt. Stopping loop."
    break
  fi

  if [[ $status -eq 200 ]]; then
    echo "Watchdog reported 3 freezes – rebooting via sudo reboot..."
    sudo reboot
    exit 0
  fi

  # Segfaults (139) – likely Hailo/camera wedge
  if [[ $status -eq 139 ]]; then
    segfaults=$((segfaults + 1))
    echo "Segfault $segfaults/$MAX_SEGFAULTS"

    if [[ $segfaults -ge $MAX_SEGFAULTS ]]; then
      echo "Segfault threshold reached – rebooting via sudo reboot..."
      sudo reboot
      exit 0
    fi
  else
    # Any non-segfault run resets the segfault counter
    segfaults=0
  fi

  echo "Restarting in 5s (Ctrl+C again to stop)..."
  sleep 5
done
