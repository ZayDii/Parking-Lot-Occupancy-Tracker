import axios from "axios";
const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE || "" });

export const getHealth = () => api.get("/api/health").then(r => r.data);
export const listSpots = () => api.get("/api/spots").then(r => r.data);
export const createSpot = (spot) => api.post("/api/spots", spot).then(r => r.data);
export const patchSpot = (id, body) => api.patch(`/api/spots/${id}`, body).then(r => r.data);

export const postOccupancy = (rec) => api.post("/api/occupancy", rec).then(r => r.data);
export const getCurrent = (lotId) => api.get(`/api/occupancy/${lotId}/current`).then(r => r.data);
export const getHistory = (lotId, minutes=60) =>
  api.get(`/api/occupancy/${lotId}/history`, { params: { minutes } }).then(r => r.data);