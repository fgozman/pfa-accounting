import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    // The activation and reset links carry a one-use token in the query string, so
    // the application never advertises where it came from.  Production hosts must
    // send the same header (plus `Cache-Control: no-store` on token pages); see
    // README.md.
    headers: { "Referrer-Policy": "no-referrer" },
    // No proxy on purpose.  The SPA is served from its own origin and calls the
    // web services at `VITE_API_BASE_URL` directly, so development exercises the
    // real cross-origin contract (CORS preflight included) instead of hiding it
    // behind a proxy that does not exist in production.
    // Every path on this server is therefore an application path, including the
    // OAuth redirect target `/auth/callback`.
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
