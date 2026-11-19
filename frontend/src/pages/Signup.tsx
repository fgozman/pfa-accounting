import { useState } from "react";

import { ApiError, apiFetch } from "../api/client";
import { extractToken, fetchDevMailbox } from "../api/devMailbox";

/**
 * Sign-up.
 *
 * The account is created but inactive until the address is verified.  In
 * development no email provider is configured: the message is captured locally,
 * so this page offers either to pull the captured link or to paste a link/token.
 */
export function SignupPage({ navigate }: { navigate: (path: string) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [registered, setRegistered] = useState(false);
  const [pasted, setPasted] = useState("");
  const [busy, setBusy] = useState(false);
  const [devHint, setDevHint] = useState<string | null>(null);
  const [devLink, setDevLink] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setErrors({});
    setMessage(null);
    setDevHint(null);
    try {
      const body = await apiFetch<{ message: string }>("/auth/register", {
        method: "POST",
        body: { email, password },
      });
      setMessage(body.message);
      setRegistered(true);
      const mailbox = await fetchDevMailbox("verification", email);
      if (mailbox && mailbox.items.length > 0) {
        setDevHint(
          `Nu există un furnizor de email configurat în development: mesajul este capturat local (${mailbox.sink_directory}). Linkul de activare poate fi preluat direct de mai jos.`,
        );
        setDevLink(mailbox.items[0]?.link ?? null);
      }
    } catch (caught) {
      if (caught instanceof ApiError) {
        setErrors(caught.fieldErrors);
        setMessage(caught.problem.title);
      }
    }
  }

  async function verify(value: string) {
    const token = extractToken(value);
    if (!token) {
      setErrors({ token: "invalid" });
      return;
    }
    setBusy(true);
    setErrors({});
    try {
      await apiFetch("/auth/verify-email", { method: "POST", body: { token } });
      navigate(`/verify-email?token=${token}`);
    } catch (caught) {
      setErrors({ token: "rejected" });
      setMessage(
        caught instanceof ApiError ? caught.problem.title : "Linkul nu a putut fi confirmat.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <h1>Creează un cont</h1>
      <p className="muted">
        Contul nu emite tokenuri: după creare veți primi un link de verificare, apoi vă
        autentificați prin serviciul de autorizare.
      </p>

      {!registered ? (
        <form onSubmit={submit}>
          <label htmlFor="email">Adresă de email</label>
          <input
            id="email"
            type="email"
            value={email}
            autoComplete="email"
            required
            aria-invalid={Boolean(errors.email)}
            aria-describedby={errors.email ? "email-error" : undefined}
            onChange={(event) => setEmail(event.target.value)}
          />
          {errors.email ? (
            <p className="field-error" id="email-error">
              Verificați adresa de email.
            </p>
          ) : null}

          <label htmlFor="password">Parolă (minimum 12 caractere)</label>
          <input
            id="password"
            type="password"
            value={password}
            minLength={12}
            autoComplete="new-password"
            required
            aria-invalid={Boolean(errors.password)}
            onChange={(event) => setPassword(event.target.value)}
          />
          {errors.password ? <p className="field-error">Parola este prea scurtă.</p> : null}

          <button type="submit" className="primary">
            Creează contul
          </button>
        </form>
      ) : null}

      {registered ? (
        <section aria-labelledby="activate">
          <h2 id="activate">Activați contul</h2>
          {devHint ? (
            <div className="info" role="status">
              <p>{devHint}</p>
              {devLink ? (
                <>
                  <p>
                    <code>{devLink}</code>
                  </p>
                  <button type="button" className="primary" onClick={() => verify(devLink)} disabled={busy}>
                    Activează contul acum
                  </button>
                  <p>
                    <a href={devLink}>sau deschideți linkul de activare</a>
                  </p>
                </>
              ) : (
                <p className="muted">
                  Mesajul capturat nu a fost găsit încă. Workerul îl procesează în câteva secunde;
                  reîncărcați pagina.
                </p>
              )}
            </div>
          ) : (
            <p className="muted">
              Verificați căsuța poștală și deschideți linkul de activare primit prin email.
            </p>
          )}

          <div className="field">
            <label htmlFor="activation-link">
              Ai deja linkul? Lipiți-l aici (sau doar tokenul) pentru a activa contul.
            </label>
            <input
              id="activation-link"
              value={pasted}
              aria-invalid={Boolean(errors.token)}
              placeholder="http://127.0.0.1:5173/verify-email?token=…"
              onChange={(event) => setPasted(event.target.value)}
            />
            {errors.token ? (
              <p className="field-error">
                Linkul sau tokenul nu este valid. Copiați linkul complet din email.
              </p>
            ) : null}
          </div>
          <button type="button" onClick={() => verify(pasted)} disabled={busy}>
            Verifică adresa
          </button>
          <p className="muted">
            Linkul a expirat sau nu a ajuns?{" "}
            <a
              href="/verify-email?resend=1"
              onClick={(event) => {
                event.preventDefault();
                navigate(`/verify-email?resend=1&email=${encodeURIComponent(email)}`);
              }}
            >
              Cereți un link nou de verificare
            </a>
            .
          </p>
        </section>
      ) : null}

      {message ? (
        <p role="status" className="info">
          {message}
        </p>
      ) : null}

      <p>
        <a
          href="/"
          onClick={(event) => {
            event.preventDefault();
            navigate("/");
          }}
        >
          Înapoi la autentificare
        </a>
      </p>
    </main>
  );
}
