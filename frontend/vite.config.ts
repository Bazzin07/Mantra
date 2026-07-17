import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Proxy /api to the backend and inject the dev API key here, so the app code
// stays key-free and same-origin (no CORS). Override with VITE_API_KEY (in
// frontend/.env, gitignored). loadEnv() is required — Vite does not auto-load
// .env into process.env for the config file itself, only into client code.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: Number(env.PORT) || 5174,
      proxy: {
        "/api": {
          target: "http://localhost:8000",
          changeOrigin: true,
          headers: { "X-API-Key": env.VITE_API_KEY || "change-me-local-dev" },
        },
      },
    },
  };
});
