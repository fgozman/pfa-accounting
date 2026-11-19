/**
 * Token storage policy.
 *
 * The **access token lives only in browser memory**.
 *
 * The **refresh token is kept in tab-scoped `sessionStorage`** so that reloading the
 * page restores the session instead of forcing a new login.  This is a deliberate,
 * reviewed policy choice (specification §7.4), not an accident:
 *
 * * `sessionStorage` is per tab and is cleared when the tab closes; a browser restart
 *   still requires logging in.
 * * it is never `localStorage`, IndexedDB, a URL or a cookie, so nothing outlives the
 *   tab and nothing is attached to requests automatically.
 * * a stolen refresh token is contained by single-use rotation with whole-family
 *   revocation on reuse, the 7-day absolute session lifetime, server-side session
 *   checks, and the strict Content-Security-Policy.
 *
 * The temporary PKCE verifier and `state` remain the only other values in
 * `sessionStorage`, and only for the redirect round trip.
 */

const ROUND_TRIP_KEY = "pfa.pkce.roundtrip";
const REFRESH_KEY = "pfa.session.refresh";

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  scope: string;
}

interface RoundTrip {
  verifier: string;
  state: string;
  createdAt: number;
}

/** In-memory token holder.  The access token is never persisted. */
class TokenMemory {
  private tokens: TokenPair | null = null;

  get(): TokenPair | null {
    return this.tokens;
  }

  set(tokens: TokenPair): void {
    this.tokens = tokens;
    persistRefreshToken(tokens);
  }

  clear(): void {
    this.tokens = null;
    clearPersistedRefreshToken();
  }

  get accessToken(): string | null {
    return this.tokens?.access_token ?? null;
  }

  get refreshToken(): string | null {
    return this.tokens?.refresh_token ?? null;
  }
}

export const tokenMemory = new TokenMemory();

// ---------------------------------------------------------------------------
// Tab-scoped refresh-token persistence (reload without re-login)
// ---------------------------------------------------------------------------

interface PersistedRefresh {
  token: string;
  /** refresh-token expiry from its own JWT, used only as a local hint */
  expiresAt: number;
}

/** JWT `exp` in milliseconds, or 0 when it cannot be read.  Not a verification. */
function jwtExpiryMs(token: string): number {
  try {
    const payload = token.split(".")[1];
    if (!payload) return 0;
    const decoded = JSON.parse(
      atob(payload.replace(/-/g, "+").replace(/_/g, "/")),
    ) as { exp?: number };
    return decoded.exp ? decoded.exp * 1000 : 0;
  } catch {
    return 0;
  }
}

function persistRefreshToken(tokens: TokenPair): void {
  try {
    const expiresAt = jwtExpiryMs(tokens.refresh_token);
    sessionStorage.setItem(
      REFRESH_KEY,
      JSON.stringify({ token: tokens.refresh_token, expiresAt } satisfies PersistedRefresh),
    );
  } catch {
    // storage unavailable (for example a locked-down private mode): stay memory-only
  }
}

/** The tab's refresh token, or null when absent or already expired. */
export function readPersistedRefreshToken(): string | null {
  try {
    const raw = sessionStorage.getItem(REFRESH_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedRefresh;
    if (parsed.expiresAt && parsed.expiresAt <= Date.now()) {
      sessionStorage.removeItem(REFRESH_KEY);
      return null;
    }
    return parsed.token;
  } catch {
    return null;
  }
}

function clearPersistedRefreshToken(): void {
  try {
    sessionStorage.removeItem(REFRESH_KEY);
  } catch {
    // ignore: nothing to clear
  }
}

// ---------------------------------------------------------------------------
// PKCE helpers
// ---------------------------------------------------------------------------

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(byteLength: number): string {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

export function createVerifier(): string {
  // RFC 7636: 43..128 characters from the unreserved set
  return randomString(48);
}

export async function createChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64UrlEncode(new Uint8Array(digest));
}

export function createState(): string {
  return randomString(32);
}

export function storeRoundTrip(roundTrip: RoundTrip): void {
  sessionStorage.setItem(ROUND_TRIP_KEY, JSON.stringify(roundTrip));
}

export function readRoundTrip(): RoundTrip | null {
  const raw = sessionStorage.getItem(ROUND_TRIP_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as RoundTrip;
  } catch {
    return null;
  }
}

export function clearRoundTrip(): void {
  sessionStorage.removeItem(ROUND_TRIP_KEY);
}

/** Remove authorization response parameters from browser history. */
export function scrubCallbackUrl(): void {
  const url = new URL(window.location.href);
  url.searchParams.delete("code");
  url.searchParams.delete("state");
  url.searchParams.delete("error");
  url.searchParams.delete("error_description");
  window.history.replaceState({}, document.title, url.pathname + url.hash);
}
