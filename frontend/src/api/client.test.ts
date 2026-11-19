import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, apiUrl, downloadArtifact } from "./client";
import { tokenMemory } from "../auth/tokens";

/**
 * The SPA is a static client: every web-service call must go to the API origin,
 * never to the origin serving the application.  These assertions exist because a
 * relative URL silently works behind a dev proxy and then breaks in production.
 */
describe("api client targets the web service origin", () => {
  beforeEach(() => {
    tokenMemory.clear();
    vi.restoreAllMocks();
  });

  it("builds absolute URLs from VITE_API_BASE_URL", () => {
    expect(apiUrl("/api/v1/workspaces").startsWith("http://127.0.0.1:8000/api/v1/workspaces")).toBe(
      true,
    );
    expect(apiUrl("/api/v1/workspaces", { limit: 5, skip: undefined })).toBe(
      "http://127.0.0.1:8000/api/v1/workspaces?limit=5",
    );
    // never the application's own origin
    expect(apiUrl("/api/v1/me")).not.toContain(window.location.origin);
  });

  it("rejects a relative path instead of guessing an origin", () => {
    expect(() => apiUrl("api/v1/me")).toThrow(/must be absolute/);
  });

  it("sends requests to the API origin with a bearer token and no credentials", async () => {
    tokenMemory.set({
      access_token: "access-1",
      refresh_token: "refresh-1",
      expires_in: 300,
      scope: "pfa.read",
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/v1/me");

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toBe("http://127.0.0.1:8000/api/v1/me");
    expect(init.credentials).toBe("omit");
    expect(init.headers.Authorization).toBe("Bearer access-1");
  });

  it("downloads artifacts from the API origin", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(new Blob([new Uint8Array([1, 2, 3])]), {
        status: 200,
        headers: { "Content-Type": "application/xml" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    // jsdom has no object-URL implementation; stub only those two methods
    const createObjectURL = vi.fn(() => "blob:local");
    const revokeObjectURL = vi.fn();
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;

    await downloadArtifact("/api/v1/declarations/x/artifacts/latest", "d.xml");

    expect(String(fetchMock.mock.calls[0]![0])).toBe(
      "http://127.0.0.1:8000/api/v1/declarations/x/artifacts/latest",
    );
  });
});
