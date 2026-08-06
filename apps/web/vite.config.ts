import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Proxy in dev so the browser never deals with CORS and the app can be
    // served from the same origin as the API in production without changing
    // a single fetch call.
    proxy: {
      "/api": { target: process.env.VITE_API_URL ?? "http://localhost:8000", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true, target: "es2020" },
});
