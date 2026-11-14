import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build goes straight into the Python store package, which serves it.
// In development, `npm run dev` proxies /store to `richies serve --no-open` on port 8780.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../richies/static", emptyOutDir: true },
  server: { port: 5174, proxy: { "/store": "http://127.0.0.1:8780" } },
});
