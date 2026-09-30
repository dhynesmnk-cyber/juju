import { defineConfig, devices } from "@playwright/test";

// Runs against a site on BASE_URL (default: `next start` on :3000) whose backend is seeded
// with the recorded game: `python -m juju.cli seed live` (see web/README.md).
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.BASE_URL ?? "http://127.0.0.1:3000",
    ...devices["Pixel 7"],
    browserName: "chromium",
    launchOptions: process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
  },
});
