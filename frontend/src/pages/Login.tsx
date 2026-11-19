import { useState } from "react";

import { authController, authorizationOriginProblem, configFromEnv } from "../auth/AuthController";
import { dictionary, type Language } from "../i18n";

export function LoginPage({ language }: { language: Language }) {
  const dict = dictionary(language);
  const [error, setError] = useState<string | null>(null);
  // surfaced before the click, so a misconfiguration is never a silent dead end
  const configurationProblem = authorizationOriginProblem(configFromEnv());

  return (
    <main className="login">
      <h1>{dict.appName}</h1>
      <p>
        Autentificarea are loc pe pagina serviciului de autorizare. Parola nu ajunge niciodată în
        aplicația web, iar autentificarea nu folosește cookie-uri.
      </p>
      {configurationProblem ? (
        <p className="error" role="alert">
          {configurationProblem}
        </p>
      ) : null}
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : null}
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          try {
            await authController.beginLogin();
          } catch (caught) {
            setError(
              caught instanceof Error
                ? caught.message
                : "Autentificarea nu a putut fi inițiată. Reîncercați.",
            );
          }
        }}
      >
        <button type="submit" className="primary" disabled={Boolean(configurationProblem)}>
          {dict.actions.login}
        </button>
      </form>
      <p className="muted">
        În development nu se trimit emailuri: linkurile de activare și de resetare sunt capturate
        local și pot fi preluate din sidoul de emailuri de dezvoltare.
      </p>
      <p>
        <a href="/cont/inscriere">Creați un cont nou</a> ·{" "}
        <a href="/password-reset">Am uitat parola</a> ·{" "}
        <a href="/verify-email?resend=1">Retrimiteți linkul de verificare</a>
      </p>
      <p className="muted">
        Sesiunea se păstrează doar în memoria browserului: reîncărcarea paginii necesită o nouă
        autentificare.
      </p>
    </main>
  );
}
