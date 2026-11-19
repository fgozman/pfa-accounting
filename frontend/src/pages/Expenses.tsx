import { useEffect, useState } from "react";

import type { Workspace } from "../App";
import { ApiError, apiFetch, newIdempotencyKey } from "../api/client";
import { useApi } from "../api/useApi";
import { ErrorNote } from "../components/StatusBadge";
import { ForeignInvoiceHint } from "../components/ForeignInvoiceHint";
import { CountrySelect } from "../components/CountrySelect";
import {
  VatFields,
  vatRateApplies,
  vatRateRequired,
  vatTreatmentLabel,
} from "../components/VatFields";
import { formatMoney, formatPercent, formatShortDate, todayIso, trimDecimal } from "../format";
import { dictionary, type Language } from "../i18n";
import {
  DEFAULT_EXPENSE_DESCRIPTION,
  INTRACOMMUNITY_OPERATION_LABELS,
  deductibilityLabel,
  expenseCategoryLabel,
  expenseDescriptionLabel,
} from "../labels";

interface ExpenseRow {
  id: string;
  status: string;
  series: string | null;
  number: string | null;
  description: string | null;
  issue_date: string | null;
  currency: string;
  total_amount: string;
  total_vat: string;
  deductibility: string;
  //: null when the user has not stated it
  business_use_percent: string | null;
  expense_category: string | null;
  intracommunity_operation_type: string | null;
  supplier_country: string | null;
  supplier_vat_id: string | null;
  vat_treatment: string;
  //: B2B/B2C nature of the supplier; it is what classifies the partner in D394
  partner_kind: string | null;
  supplier_name_raw: string | null;
  revision: number;
}

interface DocumentDetail extends ExpenseRow {
  business_purpose: string | null;
  reverse_charge_justification: string | null;
  lines: { description: string; unit_price: string; vat_rate: string }[];
}

type Step = "add" | "verify" | "saved";

/**
 * Expenses page.
 *
 * Income-tax deductibility and VAT recoverability are shown as separate
 * determinations; advanced tax details only appear when they matter.
 */
export function ExpensesPage({ language, workspace }: { language: Language; workspace: Workspace }) {
  const dict = dictionary(language);
  const [supplier, setSupplier] = useState("");
  const [partnerKind, setPartnerKind] = useState("");
  //: the series and number printed on the supplier's document, entered by the user
  const [series, setSeries] = useState("");
  const [number, setNumber] = useState("");
  const [description, setDescription] = useState("");
  const [amount, setAmount] = useState("");
  const [issueDate, setIssueDate] = useState(() => todayIso());
  const [vatTreatment, setVatTreatment] = useState("standard");
  const [vatRate, setVatRate] = useState("");
  const [operationType, setOperationType] = useState("");
  //: why the reverse charge applies — the evidence D301 needs, entered by the user
  const [reverseChargeJustification, setReverseChargeJustification] = useState("");
  const [supplierCountry, setSupplierCountry] = useState("");
  const [supplierVatId, setSupplierVatId] = useState("");
  //: nothing is preselected: an expense the user has not classified stays unclassified
  const [category, setCategory] = useState("general");
  const [businessUse, setBusinessUse] = useState("");
  const [deductibility, setDeductibility] = useState("undetermined");
  const [purpose, setPurpose] = useState("");
  const [step, setStep] = useState<Step>("add");
  //: the document that was just saved, shown on the final step
  const [savedDocument, setSavedDocument] = useState<ExpenseRow | null>(null);
  const [paid, setPaid] = useState(false);
  const [paidAmount, setPaidAmount] = useState("");
  const [paidDate, setPaidDate] = useState(() => todayIso());
  const [error, setError] = useState<ApiError | Error | null>(null);
  //: ``issued`` tells the save flow whether the document still needs finalizing
  const [editing, setEditing] = useState<{
    id: string;
    revision: number;
    issued: boolean;
  } | null>(null);
  //: the document being edited, kept in the tab so a reload resumes the edit instead of
  //: turning it into a second document (which the duplicate check then refuses)
  const EDIT_KEY = "pfa.edit.expense";
  const [busy, setBusy] = useState(false);
  //: a create refused as a duplicate offers to open the existing document for editing
  const [duplicateId, setDuplicateId] = useState<string | null>(null);

  const listing = useApi<{ items: ExpenseRow[] }>(
    `/api/v1/workspaces/${workspace.id}/documents`,
    { kind: "purchase_invoice" },
  );

  const visibleRows = listing.data?.items ?? [];

  async function editDraft(id: string) {
    setError(null);
    try {
      const detail = await apiFetch<DocumentDetail>(
        `/api/v1/workspaces/${workspace.id}/documents/${id}`,
      );
      const line = detail.lines[0];
      setSupplier(detail.supplier_name_raw ?? "");
      setPartnerKind(detail.partner_kind ?? "");
      setSeries(detail.series ?? "");
      setNumber(detail.number ?? "");
      setDescription(line?.description ?? DEFAULT_EXPENSE_DESCRIPTION);
      setAmount(trimDecimal(line?.unit_price) || "0");
      setIssueDate(detail.issue_date ?? todayIso());
      setVatTreatment(detail.vat_treatment ?? "undetermined");
      setVatRate(
        line?.vat_rate && Number(line.vat_rate) > 0 ? trimDecimal(line.vat_rate) : "",
      );
      setOperationType(detail.intracommunity_operation_type ?? "");
      setReverseChargeJustification(detail.reverse_charge_justification ?? "");
      setSupplierCountry(detail.supplier_country ?? "");
      setSupplierVatId(detail.supplier_vat_id ?? "");
      setCategory(detail.expense_category ?? "general");
      setBusinessUse(trimDecimal(detail.business_use_percent));
      setDeductibility(detail.deductibility ?? "undetermined");
      setPurpose(detail.business_purpose ?? "");
      setEditing({
        id,
        revision: detail.revision,
        issued: detail.status === "finalized",
      });
      // a settlement is recorded once: editing must not allocate a second payment to the
      // same document (which would exceed its total)
      setPaid(false);
      setPaidAmount("");
      if (detail.status !== "finalized") setStep("add");
    } catch (caught) {
      // a remembered document that no longer exists must not leave a stale error
      sessionStorage.removeItem(EDIT_KEY);
      setError(caught as Error);
    }
  }

  useEffect(() => {
    const remembered = sessionStorage.getItem(EDIT_KEY);
    if (!remembered) return;
    try {
      const parsed = JSON.parse(remembered) as { workspaceId?: string; id?: string };
      if (parsed.id && parsed.workspaceId === workspace.id) void editDraft(parsed.id);
    } catch {
      sessionStorage.removeItem(EDIT_KEY);
    }
    // restored once per mount, from the tab's own memory
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function verify() {
    const missing: string[] = [];
    if (vatTreatment === "reverse_charge" && reverseChargeJustification.trim() === "") {
      missing.push(
        "Justificarea taxării inverse (D301 nu poate fi validat fără temeiul operațiunii).",
      );
    }
    if (partnerKind !== "consumer" && supplier.trim() === "") {
      missing.push("Furnizorul — clasificarea partenerului se face din acest nume.");
    }
    if (vatRateRequired(vatTreatment) && vatRate.trim() === "") {
      missing.push("Cota de pe document (pentru un document taxabil sau autotaxabil).");
    }
    if (missing.length > 0) {
      setError(
        new Error("Completați informațiile obligatorii: " + missing.join(" ")),
      );
      return;
    }
    setError(null);
    setStep("verify");
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const body = {
        kind: "purchase_invoice",
        series: series.trim() || null,
        number: number.trim() || null,
        // the supplier identity and its B2B/B2C nature, recorded instead of inferred
        partner_kind: partnerKind || null,
        supplier_name_raw: partnerKind === "consumer" ? null : supplier,
        issue_date: issueDate,
        currency: "RON",
        vat_treatment: vatTreatment,
        intracommunity_operation_type:
          partnerKind === "consumer" ? null : operationType || null,
        supplier_country: partnerKind === "consumer" ? null : supplierCountry || null,
        supplier_vat_id: partnerKind === "consumer" ? null : supplierVatId || null,
        deductibility,
        reverse_charge_justification:
          vatTreatment === "reverse_charge" ? reverseChargeJustification.trim() : null,
        // an empty field means "not stated": the API must not receive an invented share
        business_use_percent: businessUse.trim() === "" ? null : businessUse,
        business_purpose: purpose,
        deductibility_justification: purpose,
        expense_category: category,
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
        ? await apiFetch<ExpenseRow>(
            `/api/v1/workspaces/${workspace.id}/documents/${editing.id}`,
            { method: "PATCH", body: { ...body, expected_revision: editing.revision } },
          )
        : await apiFetch<ExpenseRow>(`/api/v1/workspaces/${workspace.id}/documents`, {
            method: "POST",
            body,
          });
      const finalized = editing?.issued
        ? saved
        : await apiFetch<ExpenseRow>(
            `/api/v1/workspaces/${workspace.id}/documents/${saved.id}/finalize`,
            { method: "POST", idempotencyKey: newIdempotencyKey() },
          );
      setEditing(null);
      if (paid) {
        // the payment settles the document, so it defaults to its total, not to the net
        const value = paidAmount === "" ? finalized.total_amount : paidAmount;
        await apiFetch(`/api/v1/workspaces/${workspace.id}/payments`, {
          method: "POST",
          body: {
            direction: "outflow",
            kind: "supplier_payment",
            paid_on: paidDate,
            amount: value,
            method: "bank",
            allocations: [{ document_id: finalized.id, amount: value }],
          },
        });
      }
      setSavedDocument(finalized);
      setStep("saved");
      // the next document starts on the current date
      setIssueDate(todayIso());
      setPaidDate(todayIso());
      setVatTreatment("standard");
      setVatRate("");
      setOperationType("");
      setReverseChargeJustification("");
      setSupplierCountry("");
      setSupplierVatId("");
      setPartnerKind("");
      setNumber("");
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

  async function removeDocument(row: ExpenseRow) {
    const label = `${row.series ?? ""}${row.number ?? ""}` || row.description || row.issue_date || row.id;
    const confirmed = window.confirm(
      `Ștergeți documentul ${label}? Documentul și liniile lui se șterg, numărul devine din ` +
        "nou disponibil, iar plățile alocate lui rămân nealocate. În jurnalul de audit " +
        "rămâne înregistrată ștergerea.",
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

  // the tax details are shown as soon as the expense is classified, so a value the form
  // filled in is never hidden from the user
  const advanced = deductibility !== "undetermined" || category === "vehicle";

  function changeDeductibility(value: string) {
    setDeductibility(value);
    // "fully deductible" means the whole expense is a business expense, so the share
    // defaults to 100 — visible in the field and editable, and a share the user already
    // stated is never overwritten
    if (value === "fully_deductible" && businessUse.trim() === "") {
      setBusinessUse("100");
    }
  }

  return (
    <div className="stack">
      <h1>{dict.nav.expenses}</h1>
      <ol className="steps" aria-label="Pași">
        <li aria-current={step === "add" ? "step" : undefined}>{dict.actions.addDocument}</li>
        <li aria-current={step === "verify" ? "step" : undefined}>{dict.actions.verify}</li>
        <li aria-current={step === "saved" ? "step" : undefined}>{dict.actions.save}</li>
      </ol>

      {step !== "saved" ? (
      <section className="section" aria-labelledby="new-expense">
        <h2 id="new-expense">{editing ? "Editați documentul" : dict.actions.addDocument}</h2>
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
              }}
            >
              Renunță
            </button>
          </p>
        ) : null}
        <div className="field">
          <label htmlFor="expense-partner-kind">Tip furnizor</label>
          <select
            id="expense-partner-kind"
            value={partnerKind}
            onChange={(event) => setPartnerKind(event.target.value)}
          >
            <option value="">— nespecificat —</option>
            <option value="business">Furnizor persoană juridică (B2B)</option>
            <option value="consumer">Persoană fizică (B2C, fără date de partener)</option>
          </select>
          <small className="muted">
            Partenerul se declară în D394. Pentru o achiziție de la o persoană fizică nu există
            date de partener de completat, iar declarația nu trebuie blocată pentru ele.
          </small>
        </div>
        {partnerKind === "consumer" ? (
          <p className="muted">
            Achiziție de la persoană fizică: nu se înregistrează nume, țară sau cod TVA de
            partener.
          </p>
        ) : (
          <>
            <div className="field">
              <label htmlFor="expense-supplier">Furnizor</label>
              <input
                id="expense-supplier"
                value={supplier}
                onChange={(event) => setSupplier(event.target.value)}
              />
              <small className="muted">
                Numele furnizorului este folosit la clasificarea partenerului în D394 și la
                întocmirea declarațiilor. Fără el, declarația nu poate fi validată.
              </small>
            </div>
          </>
        )}
        <div className="field">
          <label htmlFor="expense-series">Serie (opțională)</label>
          <input
            id="expense-series"
            value={series}
            onChange={(event) => setSeries(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="expense-number">Număr</label>
          <input
            id="expense-number"
            value={number}
            onChange={(event) => setNumber(event.target.value)}
          />
          <small className="muted">
            Seria și numărul de pe documentul furnizorului, exact cum apar. Seria poate lipsi;
            numărul este cel care identifică documentul.
          </small>
        </div>
        <div className="field">
          <label htmlFor="expense-description">Descriere</label>
          <input
            id="expense-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
          <ForeignInvoiceHint />
          <small className="muted">
            Ce s-a cumpărat sau ce serviciu a fost prestat, pentru a putea identifica cheltuiala.
          </small>
        </div>
        <div className="field">
          <label htmlFor="expense-amount">Sumă (RON, fără TVA)</label>
          <input
            id="expense-amount"
            inputMode="decimal"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
          <small className="muted">
            Valoarea cheltuielii înainte de TVA, în lei; nu introduceți aici totalul de plată cu TVA.
          </small>
        </div>
        <div className="field">
          <label htmlFor="expense-date">Data documentului</label>
          <input
            id="expense-date"
            type="date"
            value={issueDate}
            onChange={(event) => setIssueDate(event.target.value)}
          />
          <small className="muted">Data înscrisă pe factură sau pe documentul de cheltuială, nu data plății.</small>
        </div>

        <VatFields
          idPrefix="expense"
          treatment={vatTreatment}
          onTreatmentChange={setVatTreatment}
          rate={vatRate}
          onRateChange={setVatRate}
        />

        {partnerKind === "consumer" ? null : (
          <>
            <CountrySelect
              id="expense-supplier-country"
              label="Țara furnizorului"
              value={supplierCountry}
              onChange={setSupplierCountry}
            />
            <div className="field">
              <label htmlFor="expense-supplier-vat">
                Cod fiscal furnizor (CUI/CIF sau cod TVA)
              </label>
              <input
                id="expense-supplier-vat"
                value={supplierVatId}
                placeholder="ex. 12345678, RO12345678 sau DE123456789"
                onChange={(event) => setSupplierVatId(event.target.value)}
              />
              <small className="muted">
                Pentru un furnizor din România: CUI/CIF-ul. Pentru unul din alt stat membru: codul
                de TVA, cu prefixul țării (fără prefix în D390). Le cer declarațiile D390/D394.
              </small>
            </div>
          </>
        )}
        {vatTreatment === "reverse_charge" && partnerKind !== "consumer" ? (
          <div className="subform">
            <div className="field">
              <label htmlFor="expense-operation-type">Tip operațiune intracomunitară</label>
              <select
                id="expense-operation-type"
                value={operationType}
                onChange={(event) => setOperationType(event.target.value)}
              >
                <option value="">— nespecificat —</option>
                {Object.entries(INTRACOMMUNITY_OPERATION_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <small className="muted">
                Alegeți tipul achiziției din alt stat membru UE, așa cum va fi raportată în D390.
              </small>
            </div>
            <div className="field">
              <label htmlFor="expense-reverse-charge-justification">
                Justificarea taxării inverse
              </label>
              <input
                id="expense-reverse-charge-justification"
                value={reverseChargeJustification}
                placeholder="ex. achiziție intracomunitară de servicii, art. 331"
                onChange={(event) => setReverseChargeJustification(event.target.value)}
              />
              <small className="muted">
                Temeiul operațiunii, preluat din date: fără el D301 (decontul special) nu poate
                fi validat.
              </small>
            </div>
            <small className="muted">
              Necesar pentru D390: tipul operațiunii intracomunitare este preluat din date, nu
              ghicit. Fără el declarația nu poate fi validată.
            </small>
          </div>
        ) : null}
        <div className="field">
          <label htmlFor="expense-category">Categorie</label>
          <select
            id="expense-category"
            value={category}
            onChange={(event) => setCategory(event.target.value)}
          >
            <option value="general">Cheltuieli generale</option>
            <option value="vehicle">Vehicul</option>
            <option value="computers">Echipamente IT</option>
            <option value="travel">Deplasare</option>
            <option value="protocol">Protocol</option>
          </select>
          <small className="muted">
            Alegeți natura cheltuielii; pentru vehicule se pot aplica reguli fiscale specifice.
          </small>
        </div>

        <details open={advanced}>
          <summary>Detalii fiscale (doar când sunt relevante)</summary>
          <div className="field">
            <label htmlFor="expense-deductibility">Deductibilitate impozit pe venit</label>
            <select
              id="expense-deductibility"
              value={deductibility}
              onChange={(event) => changeDeductibility(event.target.value)}
            >
              <option value="undetermined">— nespecificată —</option>
              <option value="fully_deductible">Integral deductibilă</option>
              <option value="partially_deductible">Parțial deductibilă</option>
              <option value="non_deductible">Nedeductibilă</option>
            </select>
            <small className="muted">
              Indică dacă și în ce măsură cheltuiala reduce venitul impozabil; este separată de
              dreptul de deducere a TVA.
            </small>
          </div>
          <div className="field">
            <label htmlFor="expense-use">Pondere utilizare în scop de afaceri (%)</label>
            <input
              id="expense-use"
              inputMode="numeric"
              value={businessUse}
              onChange={(event) => setBusinessUse(event.target.value)}
            />
            <small className="muted">
              {deductibility === "non_deductible"
                ? "Nu contează pentru o cheltuială nedeductibilă."
                : "Obligatorie pentru o cheltuială deductibilă: ponderea înmulțește suma deductibilă. " +
                  "Lăsată goală, declarația o raportează ca informație lipsă."}
            </small>
          </div>
          <div className="field">
            <label htmlFor="expense-purpose">Scop economic / justificare</label>
            <input
              id="expense-purpose"
              value={purpose}
              onChange={(event) => setPurpose(event.target.value)}
            />
            <small className="muted">
              Explicați legătura dintre achiziție și activitatea PFA-ului (de exemplu, echipament
              folosit pentru proiecte).
            </small>
          </div>
          <p className="muted">
            Deductibilitatea pentru impozitul pe venit este determinată separat de deductibilitatea
            TVA.
          </p>
        </details>

        {editing ? (
          <p className="muted">
            Plățile se înregistrează separat de document, după salvare: o corectură nu adaugă o
            a doua plată pentru același document.
          </p>
        ) : (
        <fieldset>
          <legend>{dict.labels.paid}</legend>
          <label>
            <input type="radio" name="paid" checked={paid} onChange={() => setPaid(true)} /> Da
          </label>
          <label>
            <input type="radio" name="paid" checked={!paid} onChange={() => setPaid(false)} /> Nu
          </label>
          {paid ? (
            <div className="subform">
              <div className="field">
                <label htmlFor="paid-amount">Sumă plătită</label>
                <input
                  id="paid-amount"
                  inputMode="decimal"
                  placeholder={amount}
                  value={paidAmount}
                  onChange={(event) => setPaidAmount(event.target.value)}
                />
                <small className="muted">
                  Introduceți suma achitată efectiv, inclusiv TVA, dacă există; poate fi o plată
                  parțială. Lăsați necompletat pentru valoarea integrală a documentului.
                </small>
              </div>
              <div className="field">
                <label htmlFor="paid-date">Data plății</label>
                <input
                  id="paid-date"
                  type="date"
                  value={paidDate}
                  onChange={(event) => setPaidDate(event.target.value)}
                />
              </div>
            </div>
          ) : null}
        </fieldset>
        )}

        <div className="actions">
          <button
            type="button"
            className="primary"
            onClick={() => void verify()}
            disabled={busy}
          >
            {dict.actions.verify}
          </button>
        </div>

        {duplicateId ? (
          <p className="info" role="status">
            Documentul există deja.{" "}
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
        {error ? <ErrorNote error={{ title: (error as Error).message }} /> : null}
      </section>
      ) : null}

      {step === "verify" ? (
        <section className="section" aria-labelledby="verify-expense">
          <h2 id="verify-expense">Verificați datele</h2>
          <dl className="summary">
            <div>
              <dt>Serie/număr</dt>
              <dd>
                {series || "—"}
                {number ? ` · ${number}` : ""}
              </dd>
            </div>
            <div>
              <dt>Furnizor</dt>
              <dd>
                {partnerKind === "consumer" ? "Persoană fizică (B2C)" : supplier || "—"}
                {supplierCountry ? `, ${supplierCountry}` : ""}
              </dd>
            </div>
            <div>
              <dt>Descriere</dt>
              <dd>{description}</dd>
            </div>
            <div>
              <dt>Sumă</dt>
              <dd>{formatMoney(amount)}</dd>
            </div>
            <div>
              <dt>Data documentului</dt>
              <dd>{formatShortDate(issueDate)}</dd>
            </div>
            <div>
              <dt>TVA</dt>
              <dd>
                {vatTreatmentLabel(vatTreatment)}
                {vatRate !== ""
                  ? ` · ${vatTreatment === "reverse_charge" ? "autotaxare" : "cotă"} ${vatRate}%`
                  : ""}
              </dd>
            </div>
            <div>
              <dt>Categorie</dt>
              <dd>{expenseCategoryLabel(category)}</dd>
            </div>
            <div>
              <dt>Deductibilitate impozit pe venit</dt>
              <dd>
                {deductibilityLabel(deductibility)}
                {deductibility === "fully_deductible" || deductibility === "partially_deductible"
                  ? ` · pondere ${businessUse || "—"}%`
                  : ""}
              </dd>
            </div>
            {purpose ? (
              <div>
                <dt>Scop economic / justificare</dt>
                <dd>{purpose}</dd>
              </div>
            ) : null}
            <div>
              <dt>{dict.labels.paid}</dt>
              <dd>{paid ? `Da — ${formatShortDate(paidDate)}` : "Nu încă"}</dd>
            </div>
          </dl>
          <div className="actions">
            <button type="button" className="primary" onClick={() => void save()} disabled={busy}>
              {dict.actions.save}
            </button>
            <button type="button" onClick={() => setStep("add")} disabled={busy}>
              Înapoi
            </button>
          </div>
        </section>
      ) : null}

      {step === "saved" && savedDocument ? (
        <section className="section" aria-labelledby="saved-expense">
          <h2 id="saved-expense">Document salvat</h2>
          <p role="status">
            {savedDocument.series}
            {savedDocument.number} a fost înregistrată
            {editing?.issued ? " (document emis corectat)" : ""}. Corecțiile se fac prin notă de
            credit sau prin ștergerea documentului și reînregistrarea lui.
          </p>
          <p>
            Total: {formatMoney(savedDocument.total_amount, savedDocument.currency)} · din care TVA:{" "}
            {formatMoney(savedDocument.total_vat, savedDocument.currency)}
          </p>
          <p className="muted">
            {paid ? "Plata a fost înregistrată separat." : "Nu a fost înregistrată nicio plată."}
          </p>
          <button
            type="button"
            onClick={() => {
              sessionStorage.removeItem(EDIT_KEY);
              setEditing(null);
              setSavedDocument(null);
              setStep("add");
              setIssueDate(todayIso());
              setPaidDate(todayIso());
              setVatTreatment("standard");
              setVatRate("");
              setOperationType("");
              setReverseChargeJustification("");
              setSupplierCountry("");
              setSupplierVatId("");
              setPartnerKind("");
              setSupplier("");
              setSeries("");
              setNumber("");
              setDescription("");
              setAmount("");
              setCategory("general");
              setDeductibility("undetermined");
              setBusinessUse("");
              setPurpose("");
              setPaid(false);
              setPaidAmount("");
            }}
          >
            Adaugă altă cheltuială
          </button>
        </section>
      ) : null}

      <section aria-labelledby="expense-list">
        <h2 id="expense-list">Cheltuieli înregistrate</h2>
        {listing.loading ? <p>{dict.labels.loading}</p> : null}
        {listing.error ? <ErrorNote error={listing.error.problem} /> : null}
        {listing.data && visibleRows.length === 0 ? (
          <p className="muted">{dict.labels.noItems}</p>
        ) : null}
        {listing.data && visibleRows.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th scope="col">Data</th>
                <th scope="col">Serie/număr</th>
                <th scope="col">Furnizor</th>
                <th scope="col">Descriere</th>
                <th scope="col">Categorie</th>
                <th scope="col">Total</th>
                <th scope="col">TVA</th>
                <th scope="col">Deductibilitate</th>
                <th scope="col">Utilizare</th>
                <th scope="col">Acțiuni</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row) => (
                <tr key={row.id}>
                  <th scope="row">{formatShortDate(row.issue_date)}</th>
                  <td>
                    {row.series ?? ""}
                    {row.number ?? "(fără număr)"}
                  </td>
                  <td>
                    {row.partner_kind === "consumer"
                      ? "Persoană fizică"
                      : (row.supplier_name_raw ?? "—")}
                  </td>
                  <td>{expenseDescriptionLabel(row.description)}</td>
                  <td>{expenseCategoryLabel(row.expense_category)}</td>
                  <td>{formatMoney(row.total_amount, row.currency)}</td>
                  <td>{formatMoney(row.total_vat, row.currency)}</td>
                  <td>{deductibilityLabel(row.deductibility)}</td>
                  <td>
                    {row.business_use_percent === null
                      ? "—"
                      : formatPercent(row.business_use_percent)}
                  </td>
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
