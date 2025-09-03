// frontend/src/api.js
import axios from "axios";

// For dev, leave VITE_API_BASE empty ("") and let Vite proxy /api → :8000.
// For deploy, set VITE_API_BASE to the backend origin (e.g., https://api.example.com).
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || "",
  headers: { "Content-Type": "application/json" },
});

// --- health & spots ---
export const getHealth   = () => api.get("/api/health").then(r => r.data);
export const listSpots   = () => api.get("/api/spots").then(r => r.data);
export const createSpot  = (spot) => api.post("/api/spots", spot).then(r => r.data);
export const patchSpot   = (id, body) => api.patch(`/api/spots/${id}`, body).then(r => r.data);

// --- occupancy (same shapes as before: camelCase body) ---
export const postOccupancy = (rec) => api.post("/api/occupancy", rec).then(r => r.data);
export const getCurrent    = (lotId) => api.get(`/api/occupancy/${lotId}/current`).then(r => r.data);
export const getHistory    = (lotId, minutes = 60) =>
  api.get(`/api/occupancy/${lotId}/history`, { params: { minutes } }).then(r => r.data);

// --- new: unified snapshot & baseline forecast ---
export const getSnapshot = (lotId) =>
  api.get("/api/occupancy/snapshot", { params: { lot_id: lotId } }).then(r => r.data);

export const getForecast = (lotId, hours = 12) =>
  api.get("/api/forecast", { params: { lot_id: lotId, hours } }).then(r => r.data);

// --- new: system status ---
export const getStatus = () => api.get("/api/status").then(r => r.data);

// --- new: edge ingestion (Pi → server)
// Accepts camelCase and maps to backend's snake_case payload.
export const ingestDetection = ({ lotId, cameraId, tsISO, occupiedCount, totalSpots }) =>
  api.post("/api/ingest/detections", {
    lot_id: lotId,
    camera_id: cameraId,
    ts_iso: tsISO,                 // e.g., new Date().toISOString()
    occupied_count: occupiedCount,
    total_spots: totalSpots,
  }).then(r => r.data);

export default api;
