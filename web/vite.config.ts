import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    // All API calls stay same-origin in dev, matching production's single-origin
    // serving — no CORS anywhere, ever.
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
