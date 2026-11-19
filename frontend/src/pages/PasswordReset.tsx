import { useState } from "react";

import { ApiError, apiFetch } from "../api/client";

/**
 * Password reset.
 *
 * Without a token this is the *request* form; with a token it is the *confirm*
 * form (the target of the reset link).  The request form always shows the same
 * generic answer so it cannot be used to discover which addresses exist.
 */
export function PasswordResetPage({
  navigate,
  token,
}: {
  navigate: (path: string) => void;
  token?: string;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const confirming = Boolean(token);

  async function requestReset(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = await apiFetch<{ message: string }>("/auth/password-reset/request", {
        method: "POST",
        body: { email },
      });
      setMessage(body.message);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.problem.title : "Cererea nu a putut fi trimisă.");
    } finally {
      setBusy(false);
    }
  }

  async function confirmReset(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      await apiFetch("/auth/password-reset/confirm", {
        method: "POST",
        body: { token, new_password: password },
      });
      setMessage(
        "Parola a fost schimbată, iar toate sesiunile active au fost revocate. Autentificați-vă din nou.",
      );
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(caught.problem.title);
        setFieldErrors(caught.fieldErrors);
      } else {
        setError("Parola nu a putut fi schimbată.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <h1>{confirming ? "Parolă nouă" : "Resetarea parolei"}</h1>

      {message ? (
        <p role="status" className="info">
          {message}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}

      {confirming ? (
        <form onSubmit={confirmReset}>
          <label htmlFor="new-password">Parolă nouă (minimum 12 caractere)</label>
          <input
            id="new-password"
            type="password"
            minLength={12}
            autoComplete="new-password"
            required
            value={password}
            aria-invalid={Boolean(fieldErrors.new_password)}
            onChange={(event) => setPassword(event.target.value)}
          />
          {fieldErrors.new_password ? (
            <p className="field-error">Parola trebuie să aibă cel puțin 12 caractere.</p>
          ) : null}
          <button type="submit" className="primary" disabled={busy}>
            Schimbă parola
          </button>
        </form>
      ) : (
        <form onSubmit={requestReset}>
          <label htmlFor="reset-email">Adresă de email</label>
          <input
            id="reset-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <button type="submit" className="primary" disabled={busy}>
            Trimite linkul de resetare
          </button>
        </form>
      )}

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
