import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "../api/client";
import { extractToken, fetchDevMailbox } from "../api/devMailbox";

type State = "verifying" | "verified" | "failed";
type ResendState = "idle" | "sending" | "sent";

const MISSING_TOKEN_MESSAGE =
  "Linkul de verificare este incomplet: nu conține un token. Cereți un link nou de verificare.";

/**
 * Email verification target of the activation link.
 *
 * The link points at the SPA, so this page consumes the token and calls the API.
 * A missing, expired or already-used token is explained instead of silently
 * leaving the visitor on a login screen — and because activation links are
 * short-lived, the page can issue a fresh one.
 */
export function VerifyEmailPage({
  navigate,
  token: tokenProp,
}: {
  navigate: (path: string) => void;
  token?: string;
}) {
  const params = new URLSearchParams(window.location.search);
  const tokenFromUrl = tokenProp ?? params.get("token") ?? "";
  // `/verify-email?resend=1` is the "request a new link" entry point. It has no
  // token by design, so it must not be reported as a broken activation link.
  const resendRequested = params.get("resend") === "1";
  const [state, setState] = useState<State>(tokenFromUrl ? "verifying" : "failed");
  const [message, setMessage] = useState<string | null>(
    tokenFromUrl || resendRequested ? null : MISSING_TOKEN_MESSAGE,
  );
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [resendState, setResendState] = useState<ResendState>("idle");
  const [resendMessage, setResendMessage] = useState<string | null>(null);
  const [devLink, setDevLink] = useState<string | null>(null);
  const started = useRef(false);

  const scrubTokenFromUrl = useCallback(() => {
    // The token is a one-use credential: once the exchange has been attempted, drop
    // it from the address bar and history so it cannot leak through a screen share,
    // a bookmark, browser history or a later Referer header.
    const url = new URL(window.location.href);
    if (!url.searchParams.has("token")) return;
    url.searchParams.delete("token");
    const query = url.searchParams.toString();
    window.history.replaceState({}, "", `${url.pathname}${query ? `?${query}` : ""}`);
  }, []);

  const verifyToken = useCallback(
    async (token: string) => {
      setState("verifying");
      setMessage(null);
      try {
        await apiFetch("/auth/verify-email", { method: "POST", body: { token } });
        setState("verified");
      } catch (caught) {
        setState("failed");
        setMessage(
          caught instanceof ApiError
            ? caught.problem.title
            : "Linkul de verificare nu a putut fi confirmat.",
        );
      } finally {
        scrubTokenFromUrl();
      }
    },
    [scrubTokenFromUrl],
  );

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (tokenFromUrl) void verifyToken(tokenFromUrl);
  }, [tokenFromUrl, verifyToken]);

  async function requestNewLink(event: React.FormEvent) {
    event.preventDefault();
    setResendState("sending");
    setResendMessage(null);
    setDevLink(null);
    try {
      const body = await apiFetch<{ message: string }>("/auth/verify-email/resend", {
        method: "POST",
        body: { email },
      });
      setResendState("sent");
      setResendMessage(body.message);
      // Development captures mail locally: offer the captured link directly.
      const mailbox = await fetchDevMailbox("verification", email);
      const first = mailbox?.items?.[0];
      if (first?.link) setDevLink(first.link);
    } catch (caught) {
      setResendState("idle");
      setResendMessage(
        caught instanceof ApiError
          ? caught.problem.title
          : "Solicitarea nu a putut fi trimisă. Reîncercați.",
      );
    }
  }

  async function activateCapturedLink() {
    const token = devLink ? extractToken(devLink) : null;
    if (token) await verifyToken(token);
  }

  return (
    <main className="login">
      <h1>
        {resendRequested && !message
          ? "Trimiteți un link nou de verificare"
          : "Confirmarea adresei de email"}
      </h1>
      {state === "verifying" ? <p role="status">Se verifică…</p> : null}

      {state === "verified" ? (
        <div>
          <p role="status" className="info">
            Adresa de email a fost confirmată. Contul este activ.
          </p>
          <p>Vă puteți autentifica folosind adresa și parola alese la înregistrare.</p>
          <button type="button" className="primary" onClick={() => navigate("/")}>
            Autentificare
          </button>
        </div>
      ) : null}

      {state === "failed" ? (
        <div>
          {message ? (
            <>
              <p role="alert" className="error">
                {message}
              </p>
              <p className="muted">
                Linkurile de verificare sunt valabile o singură dată și expiră. Cereți un link
                nou, apoi deschideți-l din nou.
              </p>
            </>
          ) : (
            <p className="muted">
              Introduceți adresa de email folosită la înregistrare și vă trimitem un link nou de
              activare.
            </p>
          )}

          <form onSubmit={requestNewLink}>
            <h2>Trimiteți un link nou</h2>
            <label htmlFor="resend-email">Adresă de email</label>
            <input
              id="resend-email"
              type="email"
              value={email}
              autoComplete="email"
              required
              onChange={(event) => setEmail(event.target.value)}
            />
            <button
              type="submit"
              className="primary"
              disabled={resendState === "sending" || email.trim() === ""}
            >
              {resendState === "sending" ? "Se trimite…" : "Trimite un link nou"}
            </button>
          </form>

          {resendMessage ? (
            <p role="status" className="info">
              {resendMessage}
            </p>
          ) : null}

          {devLink ? (
            <div className="info" role="status">
              <p>
                Nu există un furnizor de email în development: linkul nou este capturat local.
              </p>
              <p>
                <code>{devLink}</code>
              </p>
              <button type="button" className="primary" onClick={activateCapturedLink}>
                Activează contul acum
              </button>
              <p>
                <a href={devLink}>sau deschideți linkul de activare</a>
              </p>
            </div>
          ) : null}

          <button type="button" onClick={() => navigate("/")}>
            Înapoi la autentificare
          </button>
        </div>
      ) : null}
    </main>
  );
}
