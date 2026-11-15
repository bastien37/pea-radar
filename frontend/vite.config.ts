import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// En développement, /api est relayé vers FastAPI : le navigateur ne voit qu'une origine.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
