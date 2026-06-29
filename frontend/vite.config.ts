import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build into the FastAPI static dir; SPA is served at /inbox.
export default defineConfig({
  base: "/inbox/",
  plugins: [react()],
  build: {
    outDir: "../src/ai_sdr/web/static/inbox",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
