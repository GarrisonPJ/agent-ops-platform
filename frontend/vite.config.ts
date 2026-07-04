import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const IS_MOCK = process.env.VITE_MOCK_API === "true";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: IS_MOCK
    ? { "import.meta.env.VITE_MOCK_API": JSON.stringify("true") }
    : {},
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: IS_MOCK
      ? {}
      : {
          "/api": {
            target: process.env.VITE_API_TARGET ?? "http://api:8000",
            changeOrigin: true,
          },
        },
  },
});
