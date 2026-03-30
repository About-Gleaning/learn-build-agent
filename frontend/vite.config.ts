import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendTarget = process.env.MY_AGENT_VITE_BACKEND_URL?.trim() || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: backendTarget,
        changeOrigin: false,
      },
      "/healthz": {
        target: backendTarget,
        changeOrigin: false,
      },
    },
  },
});
