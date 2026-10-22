import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND = process.env.VANTAFLIGHT_BACKEND ?? "http://127.0.0.1:8000";

// Local-only dev server. API and WebSocket calls are proxied to the Flight
// Core so the browser talks to a single origin during development.
export default defineConfig({
  plugins: [react()],
  build: {
    // three.js alone is ~560 kB; this is a local desktop app, not a website.
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        // Keep the 3D engine in its own cacheable chunk.
        manualChunks(id: string) {
          if (id.includes("node_modules/three")) return "three";
          if (/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler)\//.test(id)) return "react";
          return undefined;
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
      "/ws": { target: BACKEND, ws: true, changeOrigin: true },
    },
  },
});
