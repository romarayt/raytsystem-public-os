import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/raytsystem/webapp/static",
    emptyOutDir: true,
    assetsInlineLimit: 0,
    sourcemap: false,
    target: "es2022"
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8765"
    }
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    include: ["src/test/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["src/test/**/*.browser.test.tsx"],
    css: true
  }
});
