import { useState } from "react";

import { ApiError, apiFetch } from "../api/client";
import { type Language } from "../i18n";

/**
 * Onboarding: create the first PFA workspace.
 *
 * Without this, a freshly registered account has no workspace and the dashboard
 * has nothing to show.  That must be an explicit empty state with one next step —
 * not an endless "Se încarcă…".
 *
 * The creator becomes the first ADMIN.  A PFA can have several users, and every
 * document in it is shared with all active members regardless of who uploaded it.
 */
export function WorkspaceSetupPage({
  onCreated,
}: {
  language?: Language;
  onCreated: (workspaceId: string) => void;
}) {
  const [name, setName] = useState("");
  const [cui, setCui] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      const workspace = await apiFetch<{ id: string }>("/api/v1/workspaces", {
        method: "POST",
        body: { name, cui: cui.trim() === "" ? null : cui.trim() },
      });
      onCreated(workspace.id);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(caught.problem.title);
        setFieldErrors(caught.fieldErrors);
      } else {
        setError("Spațiul de lucru nu a putut fi creat.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h1>Configurați PFA</h1>
      <p>
        Nu există încă un spațiu de lucru. Creați-l pe primul: veți fi primul administrator, iar
        datele fiscale, documentele și declarațiile sunt păstrate în acest spațiu de lucru.
      </p>

      <form onSubmit={submit} className="section">
        <div className="field">
          <label htmlFor="workspace-name">Denumirea PFA</label>
          <input
            id="workspace-name"
            value={name}
            required
            minLength={2}
            autoComplete="organization"
            aria-invalid={Boolean(fieldErrors.name)}
            aria-describedby={fieldErrors.name ? "workspace-name-error" : undefined}
            onChange={(event) => setName(event.target.value)}
          />
          {fieldErrors.name ? (
            <p className="field-error" id="workspace-name-error">
              Denumirea trebuie să aibă cel puțin 2 caractere.
            </p>
          ) : null}
        </div>

        <div className="field">
          <label htmlFor="workspace-cui">CUI / CIF</label>
          <input
            id="workspace-cui"
            value={cui}
            inputMode="numeric"
            aria-invalid={Boolean(fieldErrors.cui)}
            onChange={(event) => setCui(event.target.value)}
          />
          <small className="muted">Opțional acum; îl puteți completa din Setări.</small>
        </div>

        {error ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}

        <button type="submit" className="primary" disabled={busy}>
          Creează spațiul de lucru
        </button>
      </form>

      <section className="section">
        <h2>Ce urmează</h2>
        <ol>
          <li>Completați profilul fiscal (regim de impozitare, TVA, date de identificare).</li>
          <li>Adăugați documentele de venituri și cheltuieli și înregistrați încasările/plățile.</li>
          <li>
            Urmăriți termenele în pagina Acasă, generați declarațiile și încărcați recipisa ANAF.
          </li>
        </ol>
        <p className="muted">
          Invitațiile și rolurile sunt gestionate de administrator din Setări → Utilizatori. Ambele
          roluri văd toate documentele spațiului de lucru.
        </p>
      </section>
    </div>
  );
}
