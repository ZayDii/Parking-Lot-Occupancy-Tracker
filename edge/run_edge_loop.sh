#!/bin/bash
set -euo pipefail

PROJECT_DIR="/home/ee96/Parking-Lot-Occupancy-Tracker/edge"
HAILO_ENV="/home/ee96/hailo-rpi5-examples/setup_env.sh"

# Workaround for setup_env.sh expecting ZSH_VERSION under set -u
export ZSH_VERSION=""
export PYTHONPATH="${PYTHONPATH-}"

# Clear watchdog counter from previous runs
rm -f /tmp/hailo_edge_watchdog_count 2>/dev/null || true

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

  echo "=== Starting Hailo margin counter at $(date) ==="
  python3 hailo_margin_counter.py \
    --input rpi \
    --use-frame \
    --debug_hits \
    --flip_user_frame \
    --bootstrap_secs 10 \
    --bootstrap_offset 1 \
    --min_track_age 1 \
    --min_speed 0.1 \
    --hyst_px 0 \
    --cooldown_s 0.0 \
    --implied_seq \
    --g1_A 42 --g1_B 62  --g1_xmin 311  --g1_xmax 379 \
    --g2_A 60 --g2_B 79  --g2_xmin 958  --g2_xmax 1049 \
    --min_box_w 7 \
    --min_box_h 7 \
    --max_capacity 73 \
    --seed_occupancy 0

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
