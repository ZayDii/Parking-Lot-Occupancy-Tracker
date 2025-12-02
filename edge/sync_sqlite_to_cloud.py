#!/usr/bin/env python3
import sqlite3
import time
import requests

DB_PATH = "/home/ee96/Parking-Lot-Occupancy-Tracker/edge/edge_events.db"
API_URL = "https://<your-backend-domain>/api/edge-events/bulk"  # change this

BATCH_SIZE = 100
SLEEP_SECONDS = 30

def get_unsynced(conn, limit=BATCH_SIZE):
    cur = conn.execute("""
        SELECT id, ts, lot_id, gate, direction, occupancy
        FROM edge_events
        WHERE sent_to_cloud = 0
        ORDER BY id
        LIMIT ?
    """, (limit,))
    return cur.fetchall()

def mark_synced(conn, ids):
    conn.executemany(
        "UPDATE edge_events SET sent_to_cloud = 1 WHERE id = ?",
        [(i,) for i in ids]
    )
    conn.commit()

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    while True:
        rows = get_unsynced(conn)
        if not rows:
            time.sleep(SLEEP_SECONDS)
            continue

        payload = {
            "events": [
                {
                    "ts": row["ts"],
                    "lot_id": row["lot_id"],
                    "gate": row["gate"],
                    "direction": row["direction"],
                    "occupancy": row["occupancy"],
                }
                for row in rows
            ]
        }

        try:
            resp = requests.post(API_URL, json=payload, timeout=5)
            resp.raise_for_status()
        except Exception as e:
            print("Sync failed:", e)
            # keep data unsent, try again later
            time.sleep(SLEEP_SECONDS)
            continue

        ids = [row["id"] for row in rows]
        mark_synced(conn, ids)
        print(f"Synced {len(ids)} rows to cloud")

if __name__ == "__main__":
    main()
