import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the gateway (python -m pantheon.dev) runs on :9000.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:9000", "/v1": "http://127.0.0.1:9000" } },
});
