import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API runs on :8000 in development; the built UI is served by FastAPI itself.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
