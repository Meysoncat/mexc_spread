/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Allow tunnelled/preview hosts (e.g. *.vercel.run) to reach the dev server.
    allowedHosts: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8006",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});
