import { useState } from "react";

import type { Workspace } from "../App";
import { ApiError, apiFetch } from "../api/client";
import { useApi } from "../api/useApi";
import { ErrorNote } from "../components/StatusBadge";
import { formatMoney, formatShortDate, todayIso, trimDecimal } from "../format";

interface Allocation {
  document_id: string;
  document: string | null;
  //: False when the document was deleted after the settlement was recorded
  document_exists: boolean;
  issue_date: string | null;
  amount: string;
  allocation_kind: string;
}

interface KindOption {
  value: string;
  direction: "inflow" | "outflow";
  label: string;
  /** how the movement is registered for accounting (OMFP 170/2015, Cod fiscal art. 68) */
  rule: string;
}

const KIND_OPTIONS: KindOption[] = [
  {
    value: "owner_contribution",
    label: "Aportul titularului",
    direction: "inflow",
    rule:
      "Aportul titularului: încasare nu-venit în RJIP (Cod fiscal art. 68 alin. (2) lit. 1) — nu intră în venitul brut.",
  },
  {
    value: "owner_withdrawal",
    label: "Retragerea titularului",
    direction: "outflow",
    rule:
      "Retragerea titularului (utilizarea venitului net): plată nedeductibilă în RJIP — nu este cheltuială a activității.",
  },
  {
    value: "owner_transfer",
    label: "Transfer între conturile personale",
    direction: "inflow",
    rule:
      "Transferul între conturi personale: se înregistrează în RJIP pentru soldul de casă/bancă, fără efect fiscal.",
  },
  {
    value: "loan_received",
    label: "Împrumut primit",
    direction: "inflow",
    rule:
      "Împrumutul primit: nu este venit brut (art. 68 alin. (2) lit. 2). În RJIP; se deduce numai dobânda.",
  },
  {
    value: "loan_repaid",
    label: "Rambursare de împrumut",
    direction: "outflow",
    rule:
      "Rambursarea împrumutului: rata de capital nu este deductibilă (se deduce numai dobânda). Se înregistrează în RJIP.",
  },
  {
    value: "tax_payment",
    label: "Plata de taxe (impozit / CAS / CASS)",
    direction: "outflow",
    rule:
      "Plata impozitului/CAS/CASS: în RJIP cu deductibilitate zero (art. 68 alin. (4)); CAS și CASS se deduc numai prin Declarația Unică.",
  },
];

interface Settlement {
  id: string;
  direction: string;
  kind: string;
  method: string;
  paid_on: string;
  currency: string;
  amount: string;
  amount_ron: string;
  reference: string | null;
  note: string | null;
  allocations: Allocation[];
}

const DIRECTION_LABELS: Record<string, string> = {
  inflow: "Încasare",
  outflow: "Plată",
};

const KIND_LABELS: Record<string, string> = {
  owner_contribution: "Aportul titularului",
  owner_withdrawal: "Retragerea titularului",
  owner_transfer: "Transfer titular",
  loan_received: "Împrumut primit",
  loan_repaid: "Rambursare împrumut",
  tax_payment: "Plată de taxe",
  customer_receipt: "Încasare factură",
  supplier_payment: "Plată furnizor",
};

/**
 * Recorded receipts and payments.
 *
 * A settlement is a separate fact from the document it pays, so it is recorded,
 * corrected and removed here — and removing it releases the document it was applied to.
 */
export function SettlementsPage({ workspace }: { workspace: Workspace }) {
  const listing = useApi<{ items: Settlement[] }>(
    `/api/v1/workspaces/${workspace.id}/payments`,
  );
  const [editing, setEditing] = useState<Settlement | null>(null);
  const [amount, setAmount] = useState("");
  const [paidOn, setPaidOn] = useState(() => todayIso());
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  //: recording the other money kinds (aport, retrageri, împrumuturi, taxe)
  const [newKind, setNewKind] = useState("owner_contribution");
  const [newAmount, setNewAmount] = useState("");
  const [newPaidOn, setNewPaidOn] = useState(() => todayIso());
  const [newReference, setNewReference] = useState("");
  const [newNote, setNewNote] = useState("");
  const [adding, setAdding] = useState(false);

  const selectedKind = KIND_OPTIONS.find((option) => option.value === newKind) ?? KIND_OPTIONS[0]!;

  function startEdit(item: Settlement) {
    setEditing(item);
    setAmount(trimDecimal(item.amount));
    setPaidOn(item.paid_on);
    setReference(item.reference ?? "");
    setNote(item.note ?? "");
    setError(null);
    setNotice(null);
  }

  async function save() {
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await apiFetch<Settlement>(
        `/api/v1/workspaces/${workspace.id}/payments/${editing.id}`,
        {
          method: "PATCH",
          body: { amount, paid_on: paidOn, reference, note },
        },
      );
      setNotice(
        `Înregistrarea din ${formatShortDate(updated.paid_on)} a fost corectată.` +
          (updated.allocations.length === 1
            ? " Suma alocată documentului a urmat suma corectată."
            : ""),
      );
      setEditing(null);
      listing.reload();
    } catch (caught) {
      setError(caught as Error);
    } finally {
      setBusy(false);
    }
  }

  async function addMovement() {
    setAdding(true);
    setError(null);
    try {
      await apiFetch(`/api/v1/workspaces/${workspace.id}/payments`, {
        method: "POST",
        body: {
          direction: selectedKind.direction,
          kind: selectedKind.value,
          paid_on: newPaidOn,
          amount: newAmount,
          method: "bank",
          reference: newReference || null,
          note: newNote || null,
        },
      });
      setNotice(
        `Înregistrat. ${selectedKind.rule} Banii nu sunt alocați niciunui document.`,
      );
      setNewAmount("");
      setNewReference("");
      setNewNote("");
      listing.reload();
    } catch (caught) {
      setError(caught as Error);
    } finally {
      setAdding(false);
    }
  }

  async function remove(item: Settlement) {
    const confirmed = window.confirm(
      `Ștergeți ${DIRECTION_LABELS[item.direction]?.toLowerCase() ?? "înregistrarea"} de ` +
        `${formatMoney(item.amount, item.currency)} din ${formatShortDate(item.paid_on)}? ` +
        "Documentele rămân, dar sumele nu vor mai fi considerate încasate sau plătite.",
    );
    if (!confirmed) return;
    setError(null);
    try {
      await apiFetch(`/api/v1/workspaces/${workspace.id}/payments/${item.id}`, {
        method: "DELETE",
      });
      setNotice("Înregistrarea a fost ștearsă, iar alocările ei au fost eliberate.");
      setEditing(null);
      listing.reload();
    } catch (caught) {
      setError(caught as Error);
    }
  }

  const rows = listing.data?.items ?? [];

  return (
    <div className="stack">
      <h1>Încasări și plăți</h1>
      <p className="muted">
        Sumele încasate sau plătite sunt înregistrări separate de documente. Aici le puteți
        corecta sau șterge; ștergerea eliberează documentul de alocarea respectivă.
      </p>

      {listing.loading ? <p className="muted">Se încarcă…</p> : null}
      {listing.error ? <ErrorNote error={listing.error.problem} /> : null}
      {error ? <ErrorNote error={{ title: (error as Error).message }} /> : null}
      {notice ? (
        <p className="info" role="status">
          {notice}
        </p>
      ) : null}

      {editing ? (
        <section className="section" aria-labelledby="settlement-edit">
          <h2 id="settlement-edit">
            Corectați {DIRECTION_LABELS[editing.direction]?.toLowerCase() ?? "înregistrarea"}
          </h2>
          <div className="field">
            <label htmlFor="settlement-amount">Sumă</label>
            <input
              id="settlement-amount"
              inputMode="decimal"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="settlement-date">Data</label>
            <input
              id="settlement-date"
              type="date"
              value={paidOn}
              onChange={(event) => setPaidOn(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="settlement-reference">Referință</label>
            <input
              id="settlement-reference"
              value={reference}
              onChange={(event) => setReference(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="settlement-note">Notă</label>
            <input
              id="settlement-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
          {editing.allocations.length === 1 ? (
            <p className="muted">
              {editing.allocations[0]!.document_exists
                ? `Este alocată documentului ${editing.allocations[0]!.document}: suma alocată urmează suma corectată.`
                : "Documentul către care era alocată a fost șters între timp."}
            </p>
          ) : null}
          <div className="actions">
            <button type="button" className="primary" onClick={() => void save()} disabled={busy}>
              Salvează corectura
            </button>
            <button type="button" onClick={() => setEditing(null)}>
              Renunță
            </button>
          </div>
        </section>
      ) : null}

      <section className="section" aria-labelledby="settlements-record">
        <h2 id="settlements-record">Înregistrează mișcare de bani</h2>
        <p className="muted">
          Aceste mișcări intră în Registrul-jurnal de încasări și plăți (OMFP 170/2015,
          formularul 14-1-1/b) pentru soldul de casă/bancă, dar nu ajung în Registrul de
          evidență fiscală și nu se alocă niciunui document.
        </p>
        <div className="field">
          <label htmlFor="settlement-kind">Tipul mișcării</label>
          <select
            id="settlement-kind"
            value={newKind}
            onChange={(event) => setNewKind(event.target.value)}
          >
            {KIND_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.direction === "inflow" ? "Încasare — " : "Plată — "}
                {option.label}
              </option>
            ))}
          </select>
          <small className="muted">{selectedKind.rule}</small>
        </div>
        <div className="field">
          <label htmlFor="new-settlement-amount">Sumă</label>
          <input
            id="new-settlement-amount"
            inputMode="decimal"
            value={newAmount}
            onChange={(event) => setNewAmount(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="new-settlement-date">Data</label>
          <input
            id="new-settlement-date"
            type="date"
            value={newPaidOn}
            onChange={(event) => setNewPaidOn(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="new-settlement-reference">Referință</label>
          <input
            id="new-settlement-reference"
            value={newReference}
            onChange={(event) => setNewReference(event.target.value)}
          />
        </div>
        <div className="actions">
          <button
            type="button"
            className="primary"
            disabled={adding || newAmount.trim() === ""}
            onClick={() => void addMovement()}
          >
            Înregistrează
          </button>
        </div>
      </section>

      <section className="section" aria-labelledby="settlements-list">
        <h2 id="settlements-list">Înregistrate</h2>
        {listing.data && rows.length === 0 ? <p className="muted">Nu există înregistrări.</p> : null}
        {rows.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th scope="col">Data</th>
                <th scope="col">Tip</th>
                <th scope="col">Sumă</th>
                <th scope="col">Alocat către</th>
                <th scope="col">Referință</th>
                <th scope="col">Acțiuni</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((item) => (
                <tr key={item.id}>
                  <th scope="row">{formatShortDate(item.paid_on)}</th>
                  <td>
                    {DIRECTION_LABELS[item.direction] ?? item.direction}
                    <br />
                    <small className="muted">{KIND_LABELS[item.kind] ?? item.kind}</small>
                  </td>
                  <td>{formatMoney(item.amount, item.currency)}</td>
                  <td>
                    {item.allocations.length === 0
                      ? "Nealocată"
                      : item.allocations
                          .map((allocation) =>
                            allocation.document_exists
                              ? `${allocation.document} (${formatMoney(allocation.amount)})`
                              : `documentul a fost șters (${formatMoney(allocation.amount)})`,
                          )
                          .join(", ")}
                  </td>
                  <td>{item.reference ?? "—"}</td>
                  <td>
                    <button type="button" onClick={() => startEdit(item)}>
                      Editează
                    </button>{" "}
                    <button type="button" onClick={() => void remove(item)}>
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
