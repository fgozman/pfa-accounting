import { useState } from "react";

import { authController, authorizationOriginProblem, configFromEnv } from "../auth/AuthController";

/**
 * Recovery page for a request that landed on the SPA origin instead of the
 * authorization service.
 *
 * This happens when the SPA's `VITE_AUTHORIZATION_ORIGIN` is missing or points at
 * the SPA itself: the static host answers `/oauth/authorize` with the application,
 * so the visitor sees the login form again and the button looks broken.  Instead of
 * silently repeating the login page, explain what happened and start a correct
 * authorization request.
 */
export function AuthorizationOriginNotice({ navigate }: { navigate: (path: string) => void }) {
  const [error, setError] = useState<string | null>(null);
  const config = configFromEnv();
  const problem = authorizationOriginProblem(config);

  return (
    <main className="login">
      <h1>Serviciul de autorizare</h1>
      <p role="alert" className="error">
        Această adresă aparține serviciului de autorizare, dar cererea a ajuns la aplicația web
        ({window.location.origin}). Autentificarea are loc pe originea serviciului de autorizare,
        iar credențialele nu trec niciodată prin aplicație.
      </p>

      {problem ? (
        <p className="muted">{problem}</p>
      ) : (
        <p className="muted">
          Serviciul de autorizare este configurat la {config.authorizationOrigin}. Porniți o cerere
          nouă de autorizare de acolo.
        </p>
      )}

      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}

      <div className="actions">
        <button
          type="button"
          className="primary"
          disabled={Boolean(problem)}
          onClick={async () => {
            setError(null);
            try {
              // builds a fresh, valid PKCE request on the configured origin
              await authController.beginLogin();
            } catch (caught) {
              setError(caught instanceof Error ? caught.message : "Autentificarea nu a putut fi inițiată.");
            }
          }}
        >
          Mergi la autentificare
        </button>
        <button type="button" onClick={() => navigate("/")}>
          Înapoi la aplicație
        </button>
      </div>
    </main>
  );
}
