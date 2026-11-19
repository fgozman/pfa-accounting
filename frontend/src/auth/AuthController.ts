/**
 * The single in-memory auth controller.
 *
 * Responsibilities:
 * * start the cookie-free authorization-code + PKCE (S256) flow;
 * * verify `state` on the callback and exchange the code;
 * * serialise refresh attempts so concurrent 401s produce exactly one refresh;
 * * pause the affected request and retry it once, never retrying a
 *   non-idempotent request without its original Idempotency-Key;
 * * clear tenant data on logout or workspace switch.
 */

import { apiUrl } from "../api/client";
import {
  clearRoundTrip,
  createChallenge,
  createState,
  createVerifier,
  readPersistedRefreshToken,
  readRoundTrip,
  scrubCallbackUrl,
  storeRoundTrip,
  tokenMemory,
  type TokenPair,
} from "./tokens";

export interface AuthConfig {
  clientId: string;
  callbackUri: string;
  authorizationOrigin: string;
  scope: string;
}

export function configFromEnv(): AuthConfig {
  const env = import.meta.env as Record<string, string | undefined>;
  return {
    clientId: env.VITE_SPA_CLIENT_ID ?? "pfa-web-dev",
    callbackUri: env.VITE_SPA_CALLBACK_URI ?? `${window.location.origin}/auth/callback`,
    authorizationOrigin: env.VITE_AUTHORIZATION_ORIGIN ?? window.location.origin,
    scope: "pfa.read pfa.write pfa.manage",
  };
}

/**
 * Detect a configuration that can never work.
 *
 * The authorization service is a *different* origin from the SPA.  If the origin
 * is missing, the SPA falls back to its own origin, and then "Autentificare"
 * navigates to `<spa>/oauth/authorize`, the static host answers with the SPA
 * itself and the visitor sees the login page again — apparently nothing happens.
 * Reporting it explicitly is better than a silent dead end.
 */
export function authorizationOriginProblem(config: AuthConfig): string | null {
  if (!config.authorizationOrigin) {
    return "Lipsește adresa serviciului de autorizare.";
  }
  let origin: URL;
  try {
    origin = new URL(config.authorizationOrigin);
  } catch {
    return `Adresa serviciului de autorizare nu este un URL valid: ${config.authorizationOrigin}`;
  }
  if (origin.origin === window.location.origin) {
    return (
      "Serviciul de autorizare este configurat pe aceeași origine ca aplicația " +
      `(${origin.origin}), deci butonul „Autentificare” ar reveni în aceeași pagină. ` +
      "Setați VITE_AUTHORIZATION_ORIGIN către serviciul de autorizare (de exemplu " +
      "http://127.0.0.1:8000) și reporniți SPA-ul, sau porniți tot stack-ul cu `make dev`, " +
      "care transmite configurația automat."
    );
  }
  return null;
}

export function authorizeUrl(
  config: AuthConfig,
  challenge: string,
  state: string,
): string {
  const url = new URL("/oauth/authorize", config.authorizationOrigin);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("redirect_uri", config.callbackUri);
  url.searchParams.set("scope", config.scope);
  url.searchParams.set("state", state);
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  return url.toString();
}

export interface SessionInfo {
  userId: string;
  email: string;
  scope: string[];
}

type Listener = (session: SessionInfo | null) => void;

export class AuthController {
  private readonly config: AuthConfig;
  private readonly listeners = new Set<Listener>();
  private refreshInFlight: Promise<TokenPair | null> | null = null;
  private session: SessionInfo | null = null;

  constructor(config: AuthConfig = configFromEnv()) {
    this.config = config;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(): void {
    for (const listener of this.listeners) listener(this.session);
  }

  get currentSession(): SessionInfo | null {
    return this.session;
  }

  /** Begin the authorization redirect. */
  async beginLogin(): Promise<void> {
    const problem = authorizationOriginProblem(this.config);
    if (problem) {
      // never navigate into a dead end silently
      throw new Error(problem);
    }
    const verifier = createVerifier();
    const state = createState();
    const challenge = await createChallenge(verifier);
    storeRoundTrip({ verifier, state, createdAt: Date.now() });
    window.location.assign(authorizeUrl(this.config, challenge, state));
  }

  /**
   * Complete the callback.  Rejects unsolicited callbacks whose `state` does not
   * match the stored value, clears the temporary values, and scrubs the URL.
   */
  async completeLogin(params: URLSearchParams): Promise<SessionInfo> {
    const roundTrip = readRoundTrip();
    const returnedState = params.get("state");
    const code = params.get("code");
    const error = params.get("error");

    try {
      if (!roundTrip) throw new Error("missing_roundtrip");
      if (!returnedState || returnedState !== roundTrip.state) throw new Error("state_mismatch");
      if (error) throw new Error(error);
      if (!code) throw new Error("missing_code");
      if (Date.now() - roundTrip.createdAt > 5 * 60 * 1000) throw new Error("roundtrip_expired");

      const response = await fetch(new URL("/oauth/token", this.config.authorizationOrigin), {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        // no credentials: the SPA never sends cookies
        credentials: "omit",
        body: new URLSearchParams({
          grant_type: "authorization_code",
          client_id: this.config.clientId,
          code,
          redirect_uri: this.config.callbackUri,
          code_verifier: roundTrip.verifier,
        }),
      });
      if (!response.ok) throw new Error("token_exchange_failed");
      const tokens = (await response.json()) as TokenPair;
      tokenMemory.set(tokens);
      await this.loadSession();
      return this.session as SessionInfo;
    } finally {
      clearRoundTrip();
      scrubCallbackUrl();
    }
  }

  async loadSession(): Promise<SessionInfo | null> {
    if (!tokenMemory.accessToken) {
      // A page reload leaves memory empty: restore the session from the tab-scoped
      // refresh token, so reloading does not log the user out.  A refused or expired
      // refresh clears it and falls back to the login page.
      const restored = await this.refresh();
      if (!restored) {
        this.session = null;
        this.emit();
        return null;
      }
    }
    const token = tokenMemory.accessToken;
    // the resource API lives on its own origin, not on the static host
    const response = await fetch(apiUrl("/api/v1/me"), {
      headers: { Authorization: `Bearer ${token}` },
      credentials: "omit",
    });
    if (!response.ok) {
      this.session = null;
      this.emit();
      return null;
    }
    const body = (await response.json()) as {
      user_id: string;
      email: string;
      scope: string[];
    };
    this.session = { userId: body.user_id, email: body.email, scope: body.scope };
    this.emit();
    return this.session;
  }

  async logout(): Promise<void> {
    const token = tokenMemory.accessToken;
    try {
      if (token) {
        await fetch(apiUrl("/api/v1/sessions/logout"), {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          credentials: "omit",
        });
      }
    } finally {
      tokenMemory.clear();
      this.session = null;
      this.emit();
      clearTenantCache();
    }
  }

  /**
   * Refresh once, serialised.  Concurrent callers await the same promise, so a
   * burst of 401s cannot race into multiple rotations.
   */
  async refresh(): Promise<TokenPair | null> {
    if (this.refreshInFlight) return this.refreshInFlight;
    // memory first; after a reload the tab-scoped token is the only copy
    const refreshToken = tokenMemory.refreshToken ?? readPersistedRefreshToken();
    if (!refreshToken) return null;

    this.refreshInFlight = (async () => {
      try {
        const response = await fetch(new URL("/oauth/token", this.config.authorizationOrigin), {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          credentials: "omit",
          body: new URLSearchParams({
            grant_type: "refresh_token",
            client_id: this.config.clientId,
            refresh_token: refreshToken,
          }),
        });
        if (!response.ok) {
          // a lost or refused refresh means reauthentication; the persisted copy is
          // cleared too, so the next load does not retry a dead token
          tokenMemory.clear();
          this.session = null;
          this.emit();
          return null;
        }
        const tokens = (await response.json()) as TokenPair;
        tokenMemory.set(tokens);
        return tokens;
      } finally {
        this.refreshInFlight = null;
      }
    })();
    return this.refreshInFlight;
  }
}

/**
 * Cached tenant data is dropped whenever the workspace changes or the user logs
 * out, so one PFA's data can never be shown while another is active.
 */
const tenantCaches = new Set<() => void>();

export function registerTenantCache(reset: () => void): () => void {
  tenantCaches.add(reset);
  return () => tenantCaches.delete(reset);
}

export function clearTenantCache(): void {
  for (const reset of tenantCaches) reset();
}

export const authController = new AuthController();
