import { useState } from "react";

import { ApiError, apiFetch } from "../api/client";

/**
 * Accept an invitation.
 *
 * Acceptance requires a verified account with the *same* address the invitation
 * was issued for, so this page asks the visitor to sign in first and keeps the
 * token in the URL until then.  Existing accounts join without a duplicate
 * identity.
 */
export function AcceptInvitationPage({
  navigate,
  token,
  signedIn,
}: {
  navigate: (path: string) => void;
  token: string | undefined;
  signedIn: boolean;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function accept() {
    if (!token) {
      setError("Linkul invitației nu conține un token.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const body = await apiFetch<{ workspace_id: string; role: string }>(
        "/api/v1/invitations/accept",
        { method: "POST", body: { token } },
      );
      setMessage(
        `Ați fost adăugat(ă) în spațiul de lucru cu rolul ${body.role}. Toate documentele spațiului de lucru sunt vizibile ambelor roluri.`,
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.problem.title
          : "Invitația nu a putut fi acceptată.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <h1>Invitație în spațiul de lucru</h1>
      {!signedIn ? (
        <div>
          <p className="muted">
            Invitația este legată de adresa de email căreia i-a fost trimisă. Autentificați-vă cu
            acea adresă, apoi acceptați invitația.
          </p>
          <button
            type="button"
            className="primary"
            onClick={() => {
              // keep the token in the URL so it survives the sign-in round trip
              window.history.replaceState({}, "", `/invitations/accept?token=${token ?? ""}`);
              navigate("/");
            }}
          >
            Autentificare
          </button>
        </div>
      ) : (
        <div>
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
          {!message ? (
            <button type="button" className="primary" onClick={accept} disabled={busy}>
              Acceptă invitația
            </button>
          ) : (
            <button type="button" className="primary" onClick={() => navigate("/")}>
              Deschide spațiul de lucru
            </button>
          )}
          <p className="muted">
            Invitațiile sunt valabile 7 zile și pot fi folosite o singură dată. Dacă ați fost
            eliminat dintr-un spațiu de lucru, accesul se oprește imediat, iar documentele
            istorice rămân în spațiul de lucru.
          </p>
        </div>
      )}
    </main>
  );
}
