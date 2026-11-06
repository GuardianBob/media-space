import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backend = "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // The backend only accepts its own origin, so dev requests present it.
      "/api": { target: backend, changeOrigin: true, headers: { origin: backend } },
    },
  },
});
