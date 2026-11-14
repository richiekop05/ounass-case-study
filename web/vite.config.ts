import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build goes straight into the Python app package, which serves it.
// In development, `npm run dev` proxies /api to `tagger app --no-open` on port 8765.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/app/static", emptyOutDir: true },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
});
