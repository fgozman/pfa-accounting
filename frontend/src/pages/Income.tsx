import { useEffect, useState } from "react";

import type { Workspace } from "../App";
import { ApiError, apiFetch, newIdempotencyKey } from "../api/client";
import { useApi } from "../api/useApi";
import { ErrorNote } from "../components/StatusBadge";
import { ForeignInvoiceHint } from "../components/ForeignInvoiceHint";
import { CountrySelect } from "../components/CountrySelect";
import { VatFields, vatRateApplies, vatRateRequired, vatTreatmentLabel } from "../components/VatFields";
import { formatMoney, formatShortDate, todayIso, trimDecimal } from "../format";
import { dictionary, statusLabel, type Language } from "../i18n";

interface DocumentRow {
  id: string;
  kind: string;
  status: string;
  series: string | null;
  number: string | null;
  description: string | null;
  issue_date: string | null;
  currency: string;
  total_amount: string;
  total_vat: string;
  vat_treatment: string;
  //: the client (partner) identity; it is what classifies the partner in D394
  supplier_name_raw: string | null;
  supplier_country: string | null;
  supplier_vat_id: string | null;
  partner_kind: string | null;
  revision: number;
}

interface DocumentDetail extends DocumentRow {
  lines: { description: string; unit_price: string; vat_rate: string }[];
}

interface Settlement {
  settled: string;
  outstanding: string;
  paid_in_full: boolean;
  //: the settlements exceed the document total (possible after lowering it)
  over_settled: boolean;
  over_amount: string;
}

type Step = "add" | "verify" | "saved";

/**
 * Income page.
 *
 * The normal path is Adaugă document → Verifică datele → Salvează.  The question
 * "Ai încasat?" reveals amount/date/source and records a *separate* settlement;
 * it is never preselected just because an attachment exists.
 */
export function IncomePage({ language, workspace }: { language: Language; workspace: Workspace }) {
  const dict = dictionary(language);
  const [step, setStep] = useState<Step>("add");
  const [description, setDescription] = useState("");
  //: the series and number printed on the issued invoice, entered by the user
  const [series, setSeries] = useState("");
  const [number, setNumber] = useState("");
  const [client, setClient] = useState("");
  const [partnerKind, setPartnerKind] = useState("");
  const [clientCountry, setClientCountry] = useState("");
  const [clientVatId, setClientVatId] = useState("");
  const [amount, setAmount] = useState("");
  const [issueDate, setIssueDate] = useState(() => todayIso());
  const [vatTreatment, setVatTreatment] = useState("standard");
  const [vatRate, setVatRate] = useState("");
  const [received, setReceived] = useState(false);
  const [receivedAmount, setReceivedAmount] = useState("");
  const [receivedDate, setReceivedDate] = useState(() => todayIso());
  const [receivedSource, setReceivedSource] = useState("");
  const [savedDocument, setSavedDocument] = useState<DocumentRow | null>(null);
  //: ``issued`` tells the save flow whether the document still needs finalizing
  const [editing, setEditing] = useState<{
    id: string;
    revision: number;
    issued: boolean;
  } | null>(null);
  //: the document being edited, kept in the tab so a reload resumes the edit instead of
  //: turning it into a second document (which the duplicate check then refuses)
  const EDIT_KEY = "pfa.edit.income";
  const [settlement, setSettlement] = useState<Settlement | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [busy, setBusy] = useState(false);
  //: a create refused as a duplicate offers to open the existing document for editing
  const [duplicateId, setDuplicateId] = useState<string | null>(null);

  const listing = useApi<{ items: DocumentRow[] }>(
    `/api/v1/workspaces/${workspace.id}/documents`,
    { kind: "sales_invoice" },
  );

  const visibleRows = listing.data?.items ?? [];

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const body = {
        kind: "sales_invoice",
        series: series.trim() || null,
        number: number.trim() || null,
        issue_date: issueDate,
        supply_date: issueDate,
        currency: "RON",
        vat_treatment: vatTreatment,
        // the partner identity and its B2B/B2C nature, recorded instead of inferred
        partner_kind: partnerKind || null,
        supplier_name_raw: partnerKind === "consumer" ? null : client.trim() || null,
        supplier_country: partnerKind === "consumer" ? null : clientCountry || null,
        supplier_vat_id: partnerKind === "consumer" ? null : clientVatId.trim() || null,
        lines: [
          {
            description,
            quantity: "1",
            unit_price: amount,
            vat_rate: vatRateApplies(vatTreatment) && vatRate !== "" ? vatRate : "0",
            vat_treatment: vatTreatment,
          },
        ],
      };
      const saved = editing
        ? await apiFetch<DocumentRow>(
            `/api/v1/workspaces/${workspace.id}/documents/${editing.id}`,
            { method: "PATCH", body: { ...body, expected_revision: editing.revision } },
          )
        : await apiFetch<DocumentRow>(`/api/v1/workspaces/${workspace.id}/documents`, {
            method: "POST",
            body,
          });
      // an issued document is already locked: editing it must not finalize it again
      const finalized = editing?.issued
        ? saved
        : await apiFetch<DocumentRow>(
            `/api/v1/workspaces/${workspace.id}/documents/${saved.id}/finalize`,
            { method: "POST", idempotencyKey: newIdempotencyKey() },
          );
      setSavedDocument(finalized);
      setEditing(null);
      sessionStorage.removeItem(EDIT_KEY);

      if (received) {
        // the receipt settles the invoice, so it defaults to the document's total (with
        // VAT), never to the net amount typed in the form
        const value = receivedAmount === "" ? finalized.total_amount : receivedAmount;
        await apiFetch(`/api/v1/workspaces/${workspace.id}/payments`, {
          method: "POST",
          body: {
            direction: "inflow",
            kind: "customer_receipt",
            paid_on: receivedDate,
            amount: value,
            method: "bank",
            reference: receivedSource,
            allocations: [{ document_id: finalized.id, amount: value }],
          },
        });
        const summary = await apiFetch<Settlement>(
          `/api/v1/workspaces/${workspace.id}/documents/${finalized.id}/settlement`,
        );
        setSettlement(summary);
      }

      setStep("saved");
      listing.reload();
    } catch (caught) {
      // the API names the document it matched: offer to correct that one instead of
      // leaving the user stuck with a refusal
      if (caught instanceof ApiError && caught.duplicateDocumentIds.length > 0) {
        setDuplicateId(caught.duplicateDocumentIds[0]!);
      }
      setError(caught as Error);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    const remembered = sessionStorage.getItem(EDIT_KEY);
    if (!remembered) return;
    try {
      const parsed = JSON.parse(remembered) as { workspaceId?: string; id?: string };
      if (parsed.id && parsed.workspaceId === workspace.id) void editDraft(parsed.id);
      else if (parsed.workspaceId && parsed.workspaceId !== workspace.id) return;
    } catch {
      sessionStorage.removeItem(EDIT_KEY);
    }
    // the edit is restored once per mount, from the tab's own memory
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function editDraft(id: string) {
    setError(null);
    try {
      const detail = await apiFetch<DocumentDetail>(
        `/api/v1/workspaces/${workspace.id}/documents/${id}`,
      );
      const line = detail.lines[0];
      setDescription(line?.description ?? "");
      setSeries(detail.series ?? "");
      setNumber(detail.number ?? "");
      setClient(detail.supplier_name_raw ?? "");
      setPartnerKind(detail.partner_kind ?? "");
      setClientCountry(detail.supplier_country ?? "");
      setClientVatId(detail.supplier_vat_id ?? "");
      setAmount(trimDecimal(line?.unit_price) || "0");
      setIssueDate(detail.issue_date ?? todayIso());
      setVatTreatment(detail.vat_treatment ?? "undetermined");
      setVatRate(
        line?.vat_rate && Number(line.vat_rate) > 0 ? trimDecimal(line.vat_rate) : "",
      );
      setEditing({
        id,
        revision: detail.revision,
        issued: detail.status === "finalized",
      });
      // a settlement is recorded once: editing must not allocate a second receipt to the
      // same document (which would exceed its total)
      setReceived(false);
      setReceivedAmount("");
      // remember which workspace the document belongs to: after a reload the app may be
      // in another one, and restoring the wrong document only produces a 404
      sessionStorage.setItem(EDIT_KEY, JSON.stringify({ workspaceId: workspace.id, id }));
      setStep("add");
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (caught) {
      // a remembered document that no longer exists must not leave a stale error
      sessionStorage.removeItem(EDIT_KEY);
      setError(caught as Error);
    }
  }

  async function removeDocument(row: DocumentRow) {
    const label = `${row.series ?? ""}${row.number ?? "(ciornă)"}`;
    const confirmed = window.confirm(
      `Ștergeți documentul ${label}? Documentul și liniile lui se șterg, numărul devine ` +
        "din nou disponibil, iar încasările alocate lui rămân nealocate. În jurnalul de " +
        "audit rămâne înregistrată ștergerea.",
    );
    if (!confirmed) return;
    setError(null);
    try {
      await apiFetch(`/api/v1/workspaces/${workspace.id}/documents/${row.id}`, {
        method: "DELETE",
      });
      listing.reload();
    } catch (caught) {
      setError(caught as Error);
    }
  }

  return (
    <div className="stack">
      <h1>{dict.nav.income}</h1>
      <ol className="steps" aria-label="Pași">
        <li aria-current={step === "add" ? "step" : undefined}>{dict.actions.addDocument}</li>
        <li aria-current={step === "verify" ? "step" : undefined}>{dict.actions.verify}</li>
        <li aria-current={step === "saved" ? "step" : undefined}>{dict.actions.save}</li>
      </ol>

      {step !== "saved" ? (
        <section className="section" aria-labelledby="new-income">
          <h2 id="new-income">{editing ? "Editați documentul" : dict.actions.addDocument}</h2>
          {editing ? (
            <p className="info" role="status">
              {editing.issued
                ? "Se editează un document emis: starea de dinainte rămâne în istoricul " +
                  "documentului, iar seria, numărul și datele se pot corecta. "
                : "Se editează o ciornă. "}
              „{dict.actions.save}" {editing.issued ? "salvează modificările." : "o salvează și o finalizează."}{" "}
              <button
                type="button"
                onClick={() => {
                  sessionStorage.removeItem(EDIT_KEY);
                  setEditing(null);
                  setSeries("");
                }}
              >
                Renunță
              </button>
            </p>
          ) : null}
          <div className="field">
            <label htmlFor="income-description">Descriere</label>
            <input
              id="income-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
            <ForeignInvoiceHint />
          </div>
          <div className="field">
            <label htmlFor="income-series">
              Serie (opțională)
            </label>
            <input
              id="income-series"
              value={series}
              onChange={(event) => setSeries(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="income-number">Număr</label>
            <input
              id="income-number"
              value={number}
              onChange={(event) => setNumber(event.target.value)}
            />
            <small className="muted">
              Numărul de pe documentul emis este obligatoriu; seria doar dacă documentul are una.
              Aplicația nu le generează, iar numărul rămâne unic în acest spațiu de lucru.
            </small>
          </div>
          <div className="field">
            <label htmlFor="income-partner-kind">Tip client</label>
            <select
              id="income-partner-kind"
              value={partnerKind}
              onChange={(event) => setPartnerKind(event.target.value)}
            >
              <option value="">— nespecificat —</option>
              <option value="business">Client persoană juridică (B2B)</option>
              <option value="consumer">Consumator final (B2C, fără date de partener)</option>
            </select>
            <small className="muted">
              Partenerul se declară în D394. Pentru o vânzare către consumator final nu există
              date de partener de completat, iar declarația nu trebuie blocată pentru ele.
            </small>
          </div>
          {partnerKind === "consumer" ? (
            <p className="muted">
              Vânzare către consumator final: nu se înregistrează nume, țară sau cod TVA de
              partener.
            </p>
          ) : (
            <>
              <div className="field">
                <label htmlFor="income-client">Client</label>
                <input
                  id="income-client"
                  value={client}
                  onChange={(event) => setClient(event.target.value)}
                />
                <small className="muted">
                  Numele clientului este folosit la clasificarea partenerului în D394 și la
                  întocmirea facturii. Fără el, declarația nu poate fi validată.
                </small>
              </div>
              <CountrySelect
                id="income-client-country"
                label="Țara clientului"
                value={clientCountry}
                onChange={setClientCountry}
              />
              <div className="field">
                <label htmlFor="income-client-vat">Cod fiscal client (CUI/CIF sau cod TVA)</label>
                <input
                  id="income-client-vat"
                  value={clientVatId}
                  placeholder="ex. 12345678, RO12345678 sau DE123456789"
                  onChange={(event) => setClientVatId(event.target.value)}
                />
                <small className="muted">
                  Pentru un client din România: CUI/CIF-ul (cu sau fără prefix RO). Pentru un client
                  din alt stat membru: codul de TVA, cu prefixul țării. Sunt preluate din date, nu
                  ghicite: le cer declarațiile D390/D394 și factura electronică.
                </small>
              </div>
            </>
          )}
          <div className="field">
            <label htmlFor="income-amount">Sumă (RON, fără TVA)</label>
            <input
              id="income-amount"
              inputMode="decimal"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="income-date">Data emiterii</label>
            <input
              id="income-date"
              type="date"
              value={issueDate}
              onChange={(event) => setIssueDate(event.target.value)}
            />
          </div>

          <VatFields
            idPrefix="income"
            treatment={vatTreatment}
            onTreatmentChange={setVatTreatment}
            rate={vatRate}
            onRateChange={setVatRate}
          />

          {editing ? (
            <p className="muted">
              Încasările se înregistrează separat de document, după salvare: o corectură nu
              adaugă o a doua încasare pentru aceeași factură.
            </p>
          ) : (
          <fieldset>
            <legend>{dict.labels.received}</legend>
            <label>
              <input
                type="radio"
                name="received"
                checked={received}
                onChange={() => setReceived(true)}
              />
              Da
            </label>
            <label>
              <input
                type="radio"
                name="received"
                checked={!received}
                onChange={() => setReceived(false)}
              />
              Nu încă
            </label>
            <p className="muted">
              Un document atașat nu este dovada plății. Încasarea se înregistrează separat.
            </p>
            {received ? (
              <div className="subform">
                <div className="field">
                  <label htmlFor="received-amount">Sumă încasată (poate fi parțială)</label>
                  <input
                    id="received-amount"
                    inputMode="decimal"
                    placeholder={amount}
                    value={receivedAmount}
                    onChange={(event) => setReceivedAmount(event.target.value)}
                  />
                </div>
                <div className="field">
                  <label htmlFor="received-date">Data încasării</label>
                  <input
                    id="received-date"
                    type="date"
                    value={receivedDate}
                    onChange={(event) => setReceivedDate(event.target.value)}
                  />
                </div>
                <div className="field">
                  <label htmlFor="received-source">{dict.labels.source}</label>
                  <input
                    id="received-source"
                    value={receivedSource}
                    onChange={(event) => setReceivedSource(event.target.value)}
                  />
                </div>
              </div>
            ) : null}
          </fieldset>
          )}

          {error ? <ErrorNote error={{ title: (error as Error).message }} /> : null}
          {duplicateId ? (
            <p className="info" role="status">
              Documentul există deja. 
              <button
                type="button"
                onClick={() => {
                  const id = duplicateId;
                  setDuplicateId(null);
                  setError(null);
                  void editDraft(id);
                }}
              >
                Deschide-l pentru editare
              </button>
            </p>
          ) : null}

          <div className="actions">
            <button
              type="button"
              onClick={() => {
                if (vatRateRequired(vatTreatment) && vatRate.trim() === "") {
                  setError(
                    new Error(
                      "Cota de TVA este obligatorie pentru un document taxabil. Completați cota " +
                        "de pe factură sau schimbați tratamentul TVA.",
                    ),
                  );
                  return;
                }
                setError(null);
                setStep("verify");
              }}
              disabled={busy}
            >
              {dict.actions.verify}
            </button>
          </div>
        </section>
      ) : null}

      {step === "verify" ? (
        <section className="section" aria-labelledby="verify-income">
          <h2 id="verify-income">Verificați datele</h2>
          <dl className="summary">
            <div>
              <dt>Descriere</dt>
              <dd>{description}</dd>
            </div>
            <div>
              <dt>Sumă</dt>
              <dd>{formatMoney(amount)}</dd>
            </div>
            <div>
              <dt>Data emiterii</dt>
              <dd>{formatShortDate(issueDate)}</dd>
            </div>
            <div>
              <dt>TVA</dt>
              <dd>
                {vatTreatmentLabel(vatTreatment)}
                {vatRateApplies(vatTreatment) && vatRate !== "" ? ` · ${vatRate}%` : ""}
              </dd>
            </div>
            <div>
              <dt>{dict.labels.received}</dt>
              <dd>{received ? `Da — ${formatShortDate(receivedDate)}` : "Nu încă"}</dd>
            </div>
          </dl>
          <div className="actions">
            <button type="button" className="primary" onClick={save} disabled={busy}>
              {dict.actions.save}
            </button>
            <button type="button" onClick={() => setStep("add")} disabled={busy}>
              Înapoi
            </button>
          </div>
        </section>
      ) : null}

      {step === "saved" && savedDocument ? (
        <section className="section" aria-labelledby="saved-income">
          <h2 id="saved-income">Document salvat</h2>
          <p role="status">
            Factura {savedDocument.series ?? ""}
            {savedDocument.number} a fost emisă. Corecțiile se fac prin notă de credit sau prin
            ștergerea documentului și reînregistrarea lui.
          </p>
          <p>
            Total: {formatMoney(savedDocument.total_amount, savedDocument.currency)} · din care TVA:{" "}
            {formatMoney(savedDocument.total_vat, savedDocument.currency)}
          </p>
          {settlement ? (
            <p>
              Încasat: {formatMoney(settlement.settled)} ·{" "}
              {settlement.over_settled ? (
                <>
                  peste valoarea documentului cu {formatMoney(settlement.over_amount)} — verificați
                  încasările înregistrate
                </>
              ) : (
                <>
                  Rest de încasat: {formatMoney(settlement.outstanding)}
                  {settlement.paid_in_full ? " (achitat integral)" : " (parțial)"}
                </>
              )}
            </p>
          ) : (
            <p className="muted">Nu a fost înregistrată nicio încasare pentru acest document.</p>
          )}
          <button
            type="button"
            onClick={() => {
              setStep("add");
              setSavedDocument(null);
              setSettlement(null);
              setReceived(false);
              setReceivedAmount("");
              setEditing(null);
              setSeries("");
              setNumber("");
              setClient("");
              setPartnerKind("");
              setClientCountry("");
              setClientVatId("");
              setDescription("");
              setAmount("");
              setReceivedSource("");
              // a new document always starts on the current date
              setIssueDate(todayIso());
              setReceivedDate(todayIso());
              setVatTreatment("standard");
              setVatRate("");
            }}
          >
            Adaugă alt document
          </button>
        </section>
      ) : null}

      <section aria-labelledby="income-list">
        <h2 id="income-list">Facturi emise</h2>
        {listing.loading ? <p>{dict.labels.loading}</p> : null}
        {listing.error ? <ErrorNote error={listing.error.problem} /> : null}
        {listing.data && visibleRows.length === 0 ? (
          <p className="muted">{dict.labels.noItems}</p>
        ) : null}
        {listing.data && visibleRows.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th scope="col">Serie/număr</th>
                <th scope="col">Client</th>
                <th scope="col">Descriere</th>
                <th scope="col">Data</th>
                <th scope="col">Total</th>
                <th scope="col">TVA</th>
                <th scope="col">{dict.labels.status}</th>
                <th scope="col">Acțiuni</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row) => (
                <tr key={row.id}>
                  <th scope="row">
                    {row.series ?? ""}
                    {row.number ?? "ciornă"}
                  </th>
                  <td>
                    {row.partner_kind === "consumer"
                      ? "Consumator final"
                      : (row.supplier_name_raw ?? "—")}
                  </td>
                  <td>{row.description ?? "—"}</td>
                  <td>{formatShortDate(row.issue_date)}</td>
                  <td>{formatMoney(row.total_amount, row.currency)}</td>
                  <td>{formatMoney(row.total_vat, row.currency)}</td>
                  <td>{statusLabel(language, row.status)}</td>
                  <td>
                    <button type="button" onClick={() => void editDraft(row.id)}>
                      Editează
                    </button>
                    <button type="button" onClick={() => void removeDocument(row)}>
                      Șterge
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </section>
    </div>
  );
}
