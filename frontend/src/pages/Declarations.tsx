import { useEffect, useRef, useState } from "react";

import type { Workspace } from "../App";
import { ApiError, apiFetch, downloadArtifact, newIdempotencyKey } from "../api/client";
import { useApi } from "../api/useApi";
import { ErrorNote, StatusBadge } from "../components/StatusBadge";
import { formatLongDate, formatMoney } from "../format";
import { dictionary, formLabel, type Language } from "../i18n";
import { calculatedValueLabel, missingInputHint, missingInputLabel } from "../labels";

//: keys that are metadata rather than amounts, kept out of the figures table
const CALCULATED_METADATA_KEYS = new Set([
  "income_year",
  "regime",
  "rule_pack_code",
  "rule_pack_version",
  "formula_version",
  "engine_version",
]);

//: integer counts, shown without decimals
const CALCULATED_COUNT_KEYS = new Set(["nr_pag", "nrOPI"]);

function formatCalculatedValue(key: string, value: number | string): string {
  return CALCULATED_COUNT_KEYS.has(key) ? String(value) : formatMoney(String(value));
}

/** Scalar figures worth showing; arrays, objects and metadata are skipped. */
function calculatedRows(values: Record<string, unknown>): [string, number | string][] {
  return Object.entries(values).filter(([key, value]) => {
    if (CALCULATED_METADATA_KEYS.has(key)) return false;
    if (typeof value === "number") return true;
    return typeof value === "string" && /^-?\d/.test(value);
  }) as [string, number | string][];
}

interface Declaration {
  id: string;
  form_code: string;
  form_name_ro: string | null;
  income_year: number;
  period_start: string;
  period_end: string;
  status: string;
  current_version: number;
  rule_pack_code: string | null;
  rule_pack_version: number | null;
  estimate_only: boolean;
  approved_at: string | null;
  accepted_at: string | null;
  submission_available: boolean;
  submission_unavailable_reason: string | null;
  calculated_values?: Record<string, unknown>;
  trace?: { code: string; label_ro: string; formula: string; value: string; legal_reference: string }[];
}

/**
 * Declaration review page.
 *
 * Generation, filing acceptance and payment are three separate statuses.  A
 * declaration is only "accepted" once a verified receipt is attached; the exact
 * artifact is downloaded through an authenticated fetch.
 */
export function DeclarationsPage({ language, workspace }: { language: Language; workspace: Workspace }) {
  const dict = dictionary(language);
  const [selected, setSelected] = useState<Declaration | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showTrace, setShowTrace] = useState(false);
  const [busy, setBusy] = useState(false);

  const declarations = useApi<{ items: Declaration[] }>(
    `/api/v1/workspaces/${workspace.id}/declarations`,
  );

  // opened from the dashboard for a specific obligation ("Verifică și generează")
  const requestedDeclarationId = new URLSearchParams(window.location.search).get("declaration");
  useEffect(() => {
    if (selected || !requestedDeclarationId || !declarations.data) return;
    const match = declarations.data.items.find((entry) => entry.id === requestedDeclarationId);
    if (match) setSelected(match);
  }, [declarations.data, requestedDeclarationId, selected]);

  const reviewRef = useRef<HTMLElement | null>(null);
  const [openTick, setOpenTick] = useState(0);

  useEffect(() => {
    if (!selected || openTick === 0) return;
    // The review panel renders below the list: bring it into view, otherwise the
    // click looks like it did nothing.  jsdom has no scrollIntoView, hence `?.`.
    reviewRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [openTick, selected]);

  function openDeclaration(item: Declaration) {
    setSelected(item);
    setOpenTick((value) => value + 1);
  }

  async function removeDeclaration() {
    if (!selected) return;
    const confirmed = window.confirm(
      `Ștergeți declarația ${formLabel(selected.form_code)} ${selected.income_year}? ` +
        "Se șterg și versiunile calculate. Declarațiile aprobate sau depuse nu pot fi șterse.",
    );
    if (!confirmed) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await apiFetch(`/api/v1/declarations/${selected.id}`, { method: "DELETE" });
      setSelected(null);
      declarations.reload();
      setNotice("Declarația a fost ștearsă.");
    } catch (caught) {
      setError(caught as Error);
    } finally {
      setBusy(false);
    }
  }

  async function act(action: string, body?: unknown) {
    if (!selected) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await apiFetch<Declaration>(
        `/api/v1/declarations/${selected.id}/${action}`,
        { method: "POST", body, idempotencyKey: newIdempotencyKey() },
      );
      setSelected(updated);
      declarations.reload();
      if (action === "export") {
        setNotice(
          updated.status === "awaiting_external_filing"
            ? "Fișier generat ca document de lucru: verificați mesajul de avertizare înainte de depunere."
            : "Fișier generat. Descărcați-l și încărcați-l în SPV, apoi importați recipisa.",
        );
      }
      if (action === "submit") {
        setNotice(
          "Transmiterea a fost înregistrată. Acceptarea este confirmată doar de recipisa ANAF.",
        );
      }
    } catch (caught) {
      setError(caught as Error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <h1>{dict.nav.declarations}</h1>
      <p className="muted">
        Flux: {dict.actions.verify} → {dict.actions.generate} → {dict.actions.download} →{" "}
        {dict.actions.uploadReceipt}. Generarea, acceptarea de către ANAF și plata sunt stări
        distincte.
      </p>

      {declarations.loading ? <p>{dict.labels.loading}</p> : null}
      {declarations.error ? <ErrorNote error={declarations.error.problem} /> : null}
      {declarations.data && declarations.data.items.length === 0 ? (
        <p className="muted">
          Nu există declarații pregătite. Pregătiți una din lista de obligații de pe pagina Acasă.
        </p>
      ) : null}

      {declarations.data && declarations.data.items.length > 0 ? (
        <table>
          <thead>
            <tr>
              <th scope="col">Declarație</th>
              <th scope="col">{dict.labels.period}</th>
              <th scope="col">{dict.labels.status}</th>
              <th scope="col">{dict.labels.ruleVersion}</th>
              <th scope="col" />
            </tr>
          </thead>
          <tbody>
            {declarations.data.items.map((item) => (
              <tr
                key={item.id}
                className={selected?.id === item.id ? "row--selected" : undefined}
              >
                <th scope="row">
                  {formLabel(item.form_code)}
                  {item.estimate_only ? <span className="warn"> · estimativ</span> : null}
                </th>
                <td>
                  {formatLongDate(item.period_start)} – {formatLongDate(item.period_end)}
                </td>
                <td>
                  <StatusBadge status={item.status} language={language} />
                  {item.accepted_at ? <small> · {formatLongDate(item.accepted_at)}</small> : null}
                </td>
                <td>
                  {item.rule_pack_code ?? "—"}
                  {item.rule_pack_version ? ` v${item.rule_pack_version}` : ""}
                </td>
                <td>
                  <button
                    type="button"
                    aria-pressed={selected?.id === item.id}
                    onClick={() => openDeclaration(item)}
                  >
                    {selected?.id === item.id ? "Deschisă" : "Deschide"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {selected ? (
        <section
          className="section"
          aria-labelledby="declaration-review"
          ref={reviewRef}
          tabIndex={-1}
        >
          <h2 id="declaration-review">
            {formLabel(selected.form_code)} — {selected.income_year}
          </h2>
          <dl className="summary">
            <div>
              <dt>Contribuabil</dt>
              <dd>{workspace.name}</dd>
            </div>
            <div>
              <dt>{dict.labels.ruleVersion}</dt>
              <dd>
                {selected.rule_pack_code} v{selected.rule_pack_version}
              </dd>
            </div>
            <div>
              <dt>{dict.labels.status}</dt>
              <dd>
                <StatusBadge status={selected.status} language={language} />
              </dd>
            </div>
          </dl>

          {selected.estimate_only ? (
            <p className="warn" role="status">
              Schema oficială pentru acest an de venit nu este disponibilă: fișierul este un document
              de lucru estimativ și nu poate fi depus.
            </p>
          ) : null}

          {selected.calculated_values ? (
            <table>
              <caption>Valori calculate</caption>
              <tbody>
                {calculatedRows(selected.calculated_values).map(([key, value]) => (
                  <tr key={key}>
                    <th scope="row">{calculatedValueLabel(key)}</th>
                    <td>{formatCalculatedValue(key, value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}

          <div className="actions">
            <button type="button" onClick={() => act("calculate")} disabled={busy}>
              {dict.actions.verify}
            </button>
            <button type="button" onClick={() => act("validate")} disabled={busy}>
              Validează
            </button>
            <button type="button" onClick={() => act("export")} disabled={busy}>
              {dict.actions.generate}
            </button>
            <button
              type="button"
              onClick={() =>
                downloadArtifact(
                  `/api/v1/declarations/${selected.id}/artifacts/latest`,
                  `${selected.form_code}-${selected.income_year}.xml`,
                )
              }
            >
              {dict.actions.download}
            </button>
            <button type="button" onClick={() => act("approve")} disabled={busy}>
              Aprobă
            </button>
            <button
              type="button"
              className="primary"
              onClick={() => act("submit")}
              disabled={busy || !selected.submission_available}
              title={selected.submission_unavailable_reason ?? undefined}
            >
              {dict.actions.sendToAnaf}
            </button>
            <button type="button" onClick={() => void removeDeclaration()} disabled={busy}>
              Șterge declarația
            </button>
          </div>

          {selected.submission_unavailable_reason ? (
            <p className="muted">
              {dict.actions.sendToAnaf}: indisponibil. {selected.submission_unavailable_reason}
            </p>
          ) : null}

          <h3>{dict.actions.why}</h3>
          {(selected.trace ?? []).length === 0 ? (
            <p className="muted">
              Nu există explicații încă: apar după calculul declarației (butonul „Verifică").
            </p>
          ) : (
            <>
              <button type="button" onClick={() => setShowTrace((value) => !value)}>
                {showTrace ? "Ascunde explicațiile" : "Arată explicațiile"}
              </button>
              {showTrace ? (
                <ol className="trace">
                  {(selected.trace ?? []).map((line) => (
                    <li key={line.code}>
                      <strong>{line.label_ro}</strong>: {line.formula} = {line.value}
                      <br />
                      <small>{line.legal_reference}</small>
                    </li>
                  ))}
                </ol>
              ) : null}
            </>
          )}

          <ReceiptUpload declarationId={selected.id} onDone={() => declarations.reload()} />
        </section>
      ) : null}

      {error instanceof ApiError && error.missingInputs.length > 0 ? (
        <div className="error" role="alert">
          <p>{error.problem.title}</p>
          <ul>
            {error.missingInputs.map((key) => (
              <li key={key}>
                {missingInputLabel(key)}
                {missingInputHint(key) ? (
                  <div>
                    <small className="muted">{missingInputHint(key)}</small>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
          <p className="muted">
            Datele de identificare (denumire, CUI/CIF, adresă) se completează în Setări → Profil
            fiscal → „Date de identificare". Informațiile fiscale pe an, în Setări → „Informații
            fiscale pe an". Clasificarea operațiunilor se completează pe documentul semnalat.
          </p>
        </div>
      ) : error ? (
        <ErrorNote error={{ title: (error as Error).message }} />
      ) : null}
      {notice ? (
        <p className="info" role="status">
          {notice}
        </p>
      ) : null}
    </div>
  );
}

function ReceiptUpload({
  declarationId,
  onDone,
}: {
  declarationId: string;
  onDone: () => void;
}) {
  const [payload, setPayload] = useState(
    '{"stare": "ok", "cif": "", "formular": "", "perioada": ""}',
  );
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [busy, setBusy] = useState(false);

  return (
    <div className="section">
      <h3>Încarcă recipisa</h3>
      <p className="muted">
        Acceptarea este confirmată doar dacă recipisa corespunde contribuabilului, formularului,
        perioadei și fișierului depus.
      </p>
      <div className="field">
        <label htmlFor="receipt-payload">Conținut recipisă (XML sau date)</label>
        <textarea
          id="receipt-payload"
          rows={4}
          value={payload}
          onChange={(event) => setPayload(event.target.value)}
        />
      </div>
      {error ? <ErrorNote error={{ title: (error as Error).message }} /> : null}
      {result ? <p role="status">{result}</p> : null}
      <button
        type="button"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            const parsed = JSON.parse(payload) as Record<string, unknown>;
            const response = await apiFetch<{ verified: boolean; manually_reported: boolean; note: string | null }>(
              `/api/v1/declarations/${declarationId}/receipts`,
              { method: "POST", body: { payload: parsed } },
            );
            setResult(
              response.verified
                ? "Recipisă verificată: declarația este acceptată."
                : `Recipisa nu a putut fi verificată. ${response.note ?? ""}`,
            );
            onDone();
          } catch (caught) {
            setError(caught as Error);
          } finally {
            setBusy(false);
          }
        }}
      >
        {dictionary("ro").actions.uploadReceipt}
      </button>
    </div>
  );
}
