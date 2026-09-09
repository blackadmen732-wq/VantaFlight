import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND = process.env.VANTAFLIGHT_BACKEND ?? "http://127.0.0.1:8000";

// Local-only dev server. API and WebSocket calls are proxied to the Flight
// Core so the browser talks to a single origin during development.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
      "/ws": { target: BACKEND, ws: true, changeOrigin: true },
    },
  },
});
