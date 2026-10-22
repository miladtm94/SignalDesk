import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite runs this under Node; the project does not otherwise need Node's types.
declare const process: { env: Record<string, string | undefined> };

// The dev server proxies /api to the local backend; both can be moved, e.g. while
// the Docker console holds the default ports.
const apiTarget = process.env.TRADING_CONSOLE_API_URL ?? "http://127.0.0.1:8765";
const port = Number(process.env.TRADING_CONSOLE_FRONTEND_PORT ?? 5174);

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // The chart and markdown libraries change far less often than the app.
        manualChunks: {
          charts: ["lightweight-charts"],
          markdown: ["react-markdown"],
        },
      },
    },
  },
  server: {
    port,
    strictPort: true,
    proxy: {
      "/api": {
        target: apiTarget,
        ws: true,
      },
    },
  },
});
