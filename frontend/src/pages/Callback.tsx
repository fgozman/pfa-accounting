import { useEffect, useRef, useState } from "react";

import { authController } from "../auth/AuthController";

/**
 * Authorization callback.
 *
 * The `state` is verified before the code is exchanged, unsolicited callbacks are
 * rejected, the temporary verifier/state are cleared, and the callback parameters
 * are removed from browser history.
 */
export function CallbackPage({ navigate }: { navigate: (path: string) => void }) {
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const params = new URLSearchParams(window.location.search);
    authController
      .completeLogin(params)
      .then(() => navigate("/"))
      .catch((caught: unknown) => {
        const reason = caught instanceof Error ? caught.message : "unknown";
        setError(
          reason === "state_mismatch" || reason === "missing_roundtrip"
            ? "Această cerere de autorizare nu a fost inițiată de aplicație (lipsește sau nu " +
              "corespunde parametrului state). Din motive de securitate, răspunsul este refuzat. " +
              "Reluați autentificarea din pagina de start."
            : "Autentificarea nu a putut fi finalizată. Reluați autentificarea.",
        );
      });
  }, [navigate]);

  return (
    <main className="login">
      <h1>Se finalizează autentificarea…</h1>
      {error ? (
        <div>
          <p className="error" role="alert">
            {error}
          </p>
          <p>
            <a href="/">Înapoi la autentificare</a>
          </p>
        </div>
      ) : null}
    </main>
  );
}
