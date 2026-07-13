import react from "@vitejs/plugin-react";
import { playwright } from "@vitest/browser-playwright";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    include: ["sigma/rendering", "graphology-layout-forceatlas2", "graphology-layout-forceatlas2/worker"]
  },
  test: {
    include: ["src/test/{forceLayout,layout,visual,accessibility}.browser.test.tsx"],
    testTimeout: 45_000,
    hookTimeout: 30_000,
    browser: {
      enabled: true,
      headless: true,
      api: { host: "127.0.0.1", port: 63315, strictPort: false },
      provider: playwright(),
      instances: [{ browser: "chromium", viewport: { width: 1280, height: 800 } }]
    }
  }
});
