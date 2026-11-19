import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AuthController,
  authorizationOriginProblem,
  clearTenantCache,
  registerTenantCache,
} from "./AuthController";
import { createChallenge, createVerifier, tokenMemory } from "./tokens";

const config = {
  clientId: "pfa-web-test",
  callbackUri: "http://127.0.0.1:5173/auth/callback",
  authorizationOrigin: "http://127.0.0.1:8000",
  scope: "pfa.read pfa.write pfa.manage",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AuthController", () => {
  beforeEach(() => {
    tokenMemory.clear();
    sessionStorage.clear();
    vi.restoreAllMocks();
    // jsdom lacks WebCrypto's subtle digest in some versions
    if (!globalThis.crypto?.subtle) {
      Object.defineProperty(globalThis, "crypto", {
        value: { getRandomValues: (array: Uint8Array) => array.fill(7), subtle: {} },
      });
    }
  });

  afterEach(() => {
    tokenMemory.clear();
    sessionStorage.clear();
  });

  it("produces an RFC 7636 compliant verifier and S256 challenge", async () => {
    const verifier = createVerifier();
    expect(verifier).toMatch(/^[A-Za-z0-9\-._~]{43,128}$/);
    const challenge = await createChallenge(verifier);
    expect(challenge).toMatch(/^[A-Za-z0-9\-_]{43}$/);
    // the challenge must never be the verifier itself
    expect(challenge).not.toBe(verifier);
  });

  it("rejects an unsolicited callback whose state does not match", async () => {
    const controller = new AuthController(config);
    sessionStorage.setItem(
      "pfa.pkce.roundtrip",
      JSON.stringify({ verifier: createVerifier(), state: "expected-state", createdAt: Date.now() }),
    );
    await expect(
      controller.completeLogin(new URLSearchParams({ code: "abc", state: "attacker-state" })),
    ).rejects.toThrow("state_mismatch");
  });

  it("exchanges the code, keeps the access token in memory and persists only the refresh token", async () => {
    const controller = new AuthController(config);
    const verifier = createVerifier();
    sessionStorage.setItem(
      "pfa.pkce.roundtrip",
      JSON.stringify({ verifier, state: "good-state", createdAt: Date.now() }),
    );
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          access_token: "access-1",
          refresh_token: "refresh-1",
          expires_in: 300,
          scope: "pfa.read",
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ user_id: "u1", email: "a@example.test", scope: ["pfa.read"] }));
    vi.stubGlobal("fetch", fetchMock);

    const session = await controller.completeLogin(
      new URLSearchParams({ code: "code-1", state: "good-state" }),
    );
    expect(session.email).toBe("a@example.test");
    expect(tokenMemory.accessToken).toBe("access-1");
    // no localStorage at all, and the PKCE round trip is gone
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.getItem("pfa.pkce.roundtrip")).toBeNull();
    // the access token never persists; only the tab-scoped refresh token does
    expect(JSON.stringify(sessionStorage)).not.toContain("access-1");
    expect(sessionStorage.getItem("pfa.session.refresh")).toContain("refresh-1");
  });

  it("restores the session from the tab-scoped refresh token after a reload", async () => {
    // a reload keeps sessionStorage but loses module memory: simulate it with a
    // fresh module graph, which is exactly what the browser does
    sessionStorage.setItem(
      "pfa.session.refresh",
      JSON.stringify({ token: "refresh-persisted", expiresAt: 0 }),
    );
    vi.resetModules();
    const reloaded = await import("./AuthController");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          jsonResponse({
            access_token: "access-restored",
            refresh_token: "refresh-rotated",
            expires_in: 300,
            scope: "pfa.read",
          }),
        )
        .mockResolvedValueOnce(
          jsonResponse({ user_id: "u1", email: "a@example.test", scope: ["pfa.read"] }),
        ),
    );

    const controller = new reloaded.AuthController(config);
    const session = await controller.loadSession();

    expect(session?.email).toBe("a@example.test");
    // the rotated refresh token replaces the used one
    expect(sessionStorage.getItem("pfa.session.refresh")).toContain("refresh-rotated");
  });

  it("serialises concurrent refreshes into a single rotation", async () => {
    const controller = new AuthController(config);
    tokenMemory.set({
      access_token: "access-old",
      refresh_token: "refresh-old",
      expires_in: 300,
      scope: "pfa.read",
    });
    let refreshCalls = 0;
    const fetchMock = vi.fn().mockImplementation(async (_url: string, init: RequestInit) => {
      const body = String(init.body);
      if (body.includes("grant_type=refresh_token")) {
        refreshCalls += 1;
        await new Promise((resolve) => setTimeout(resolve, 5));
        return jsonResponse({
          access_token: "access-new",
          refresh_token: "refresh-new",
          expires_in: 300,
          scope: "pfa.read",
        });
      }
      return jsonResponse({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const [a, b, c] = await Promise.all([
      controller.refresh(),
      controller.refresh(),
      controller.refresh(),
    ]);
    expect(refreshCalls).toBe(1);
    expect(a).toBe(b);
    expect(c?.access_token).toBe("access-new");
    expect(tokenMemory.accessToken).toBe("access-new");
  });

  it("reports an authorization origin that points at the SPA itself", () => {
    // the configuration that made "Autentificare" navigate to the SPA origin,
    // where the static host answered with the login page again
    const broken = { ...config, authorizationOrigin: window.location.origin };
    const problem = authorizationOriginProblem(broken);
    expect(problem).not.toBeNull();
    expect(problem).toContain("aceeași origine");
    expect(problem).toContain("VITE_AUTHORIZATION_ORIGIN");

    expect(authorizationOriginProblem(config)).toBeNull();
    expect(authorizationOriginProblem({ ...config, authorizationOrigin: "not a url" })).toContain(
      "URL valid",
    );
  });

  it("refuses to start a login that could only loop back to the SPA", async () => {
    const controller = new AuthController({ ...config, authorizationOrigin: window.location.origin });
    await expect(controller.beginLogin()).rejects.toThrow(/aceeași origine/);
    // nothing was stored, so no stale round trip is left behind
    expect(sessionStorage.getItem("pfa.pkce.roundtrip")).toBeNull();
  });

  it("clears everything when the refresh is refused", async () => {
    const controller = new AuthController(config);
    tokenMemory.set({
      access_token: "access-old",
      refresh_token: "refresh-old",
      expires_in: 300,
      scope: "pfa.read",
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ code: "invalid_grant" }, 400)));
    const result = await controller.refresh();
    expect(result).toBeNull();
    expect(tokenMemory.accessToken).toBeNull();
    // a refused refresh must not leave a dead token behind for the next reload
    expect(sessionStorage.getItem("pfa.session.refresh")).toBeNull();
  });

  it("drops cached tenant data on logout and on workspace switch", () => {
    let reset = 0;
    registerTenantCache(() => {
      reset += 1;
    });
    clearTenantCache();
    expect(reset).toBe(1);
  });
});
