// frontend/src/api.js
import axios from "axios";

// For local dev: leave VITE_API_BASE empty and let Vite proxy `/api/*` → :8000.
// For prod (Amplify): VITE_API_BASE should be your API Gateway URL,
// e.g. https://0u5wee35tg.execute-api.us-east-1.amazonaws.com
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || "",
  headers: { "Content-Type": "application/json" },
});

// ---- Occupancy endpoints ----
export const postOccupancy = (rec) =>
  api.post("/api/occupancy", rec).then((r) => r.data);

export const getCurrent = (lotId) =>
  api.get(`/api/occupancy/${lotId}/current`).then((r) => r.data);

export const getHistory = (lotId, minutes = 60) =>
  api
    .get(`/api/occupancy/${lotId}/history`, { params: { minutes } })
    .then((r) => r.data);

// Unified snapshot (current + mini-history)
export const getSnapshot = (lotId) =>
  api
    .get("/api/occupancy/snapshot", { params: { lot_id: lotId } })
    .then((r) => r.data);

// ---- Forecast endpoint ----
export const getForecast = (lotId, hours = 12) =>
  api
    .get("/api/forecast", { params: { lot_id: lotId, hours } })
    .then((r) => r.data);

// ---- System status ----
export const getStatus = () =>
  api.get("/api/status").then((r) => r.data);

// ---- Edge ingestion helper (not used by UI, but kept for tools) ----
export const ingestDetection = ({
  lotId,
  cameraId,
  tsISO,
  occupiedCount,
  totalSpots,
}) =>
  api
    .post("/api/ingest/detections", {
      lot_id: lotId,
      camera_id: cameraId,
      ts_iso: tsISO,
      occupied_count: occupiedCount,
      total_spots: totalSpots,
    })
    .then((r) => r.data);

export default api;
