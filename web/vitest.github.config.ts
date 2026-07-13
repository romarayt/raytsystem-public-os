import react from "@vitejs/plugin-react";
import { playwright } from "@vitest/browser-playwright";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    fs: { allow: [".."] }
  },
  optimizeDeps: {
    include: ["sigma/rendering", "graphology-layout-forceatlas2", "graphology-layout-forceatlas2/worker"]
  },
  test: {
    include: ["src/test/githubScreenshots.browser.test.tsx"],
    testTimeout: 90_000,
    hookTimeout: 30_000,
    sequence: { concurrent: false },
    browser: {
      enabled: true,
      headless: true,
      api: { host: "127.0.0.1", port: 63316, strictPort: false },
      provider: playwright(),
      instances: [{ browser: "chromium", viewport: { width: 1440, height: 900 } }]
    }
  }
});
