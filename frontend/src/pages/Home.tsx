import { useEffect, useMemo, useState } from "react";

import type { Workspace } from "../App";
import { apiFetch, newIdempotencyKey } from "../api/client";
import { useApi } from "../api/useApi";
import { StatusBadge } from "../components/StatusBadge";
import { formatLongDate, formatMoney, formatDeadlineDistance, formatPeriod, formatShortDate } from "../format";
import { dictionary, formLabel, statusLabel, type Language } from "../i18n";
import { missingInputLabel } from "../labels";

interface Obligation {
  id: string;
  form_code: string;
  form_name_ro: string | null;
  period_start: string;
  period_end: string;
  declaration_id: string | null;
  triggering_evidence: string[] | null;
  applicability: string;
  applicability_reason: string;
  filing_due_date: string | null;
  payment_due_date: string | null;
  deadline_provenance: string | null;
  days_remaining: number | null;
  missing_inputs: string[];
  unsupported_reason: string | null;
  resolution_status: string;
  capabilities: { generate: boolean; submit: boolean; unavailable_reason: string | null } | null;
}

interface InvoiceIdentity {
  series: string | null;
  number: string | null;
  issue_date: string | null;
}

interface Dashboard {
  income_year: number;
  issued_revenue: string;
  collected_revenue: string;
  open_obligations: Obligation[];
}

interface NotificationItem {
  id: string;
  obligation_id: string;
  stage: string;
  title_ro: string;
  body_ro: string;
  due_on: string | null;
  days_remaining: number | null;
  overdue: boolean;
  read: boolean;
  next_step_kind: string | null;
  next_step_ref: string | null;
}

export function HomePage({
  language,
  workspace,
  navigate,
}: {
  language: Language;
  workspace: Workspace;
  navigate: (path: string) => void;
}) {
  const dict = dictionary(language);
  const obligations = useApi<{ items: Obligation[]; unassessed_forms: { form_code: string; name_ro: string; reason: string | null }[] }>(
    `/api/v1/workspaces/${workspace.id}/obligations`,
    { actionable_only: "true" },
  );
  const notifications = useApi<{ items: NotificationItem[] }>(
    `/api/v1/workspaces/${workspace.id}/notifications`,
  );
  const dashboard = useApi<Dashboard>(`/api/v1/workspaces/${workspace.id}/dashboard`);
  const [invoiceIdentities, setInvoiceIdentities] = useState<Record<string, InvoiceIdentity | null>>({});

  const invoiceIds = useMemo(() =>
    [...new Set((obligations.data?.items ?? [])
      .filter((item) => item.form_code === "EFACTURA")
      .map((item) => item.triggering_evidence?.[0])
      .filter((id): id is string => Boolean(id)))],
    [obligations.data],
  );

  useEffect(() => {
    let active = true;
    setInvoiceIdentities({});
    void Promise.all(invoiceIds.map(async (id) => {
      try {
        const invoice = await apiFetch<InvoiceIdentity>(
          `/api/v1/workspaces/${workspace.id}/documents/${id}`,
        );
        return [id, invoice] as const;
      } catch {
        return [id, null] as const;
      }
    })).then((entries) => {
      if (active) setInvoiceIdentities(Object.fromEntries(entries));
    });
    return () => { active = false; };
  }, [workspace.id, invoiceIds]);

  function invoiceLabel(obligation: Obligation | undefined): string | null {
    if (obligation?.form_code !== "EFACTURA") return null;
    const id = obligation.triggering_evidence?.[0];
    if (!id) return null;
    const invoice = invoiceIdentities[id];
    if (invoice === undefined) return "Se încarcă datele facturii…";
    if (invoice === null) return "Datele facturii nu sunt disponibile.";
    const identifier = [invoice.series, invoice.number].filter(Boolean).join(" / ") || "fără serie/număr";
    return `Factura ${identifier} · emisă la ${formatShortDate(invoice.issue_date)}`;
  }

  const [busyObligationId, setBusyObligationId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  const obligationById = useMemo(() => {
    const map = new Map<string, Obligation>();
    for (const item of obligations.data?.items ?? []) map.set(item.id, item);
    return map;
  }, [obligations.data]);

  /**
   * Prepare (or reopen) the declaration for an obligation, then show it.  Preparing
   * is what actually creates the draft; the review page only lists drafts, so without
   * this the "Verifică și generează" button led nowhere.
   */
  async function openDeclaration(obligation: Obligation | undefined) {
    if (!obligation) {
      navigate("/declaratii");
      return;
    }
    if (obligation.declaration_id) {
      navigate(`/declaratii?declaration=${obligation.declaration_id}`);
      return;
    }
    setBusyObligationId(obligation.id);
    setActionError(null);
    try {
      const created = await apiFetch<{ id: string }>(
        `/api/v1/workspaces/${workspace.id}/declarations`,
        {
          method: "POST",
          idempotencyKey: newIdempotencyKey(),
          body: {
            form_code: obligation.form_code,
            income_year: Number(obligation.period_end.slice(0, 4)),
            period_start: obligation.period_start,
            period_end: obligation.period_end,
          },
        },
      );
      obligations.reload();
      navigate(`/declaratii?declaration=${created.id}`);
    } catch (caught) {
      setActionError(
        caught instanceof Error ? caught.message : "Declarația nu a putut fi pregătită.",
      );
    } finally {
      setBusyObligationId(null);
    }
  }

  const grouped = useMemo(() => {
    const items = obligations.data?.items ?? [];
    const overdue: Obligation[] = [];
    const upcoming: Obligation[] = [];
    const incomplete: Obligation[] = [];
    for (const item of items) {
      if (item.applicability === "needs_information" || item.missing_inputs.length > 0) {
        incomplete.push(item);
      } else if (item.days_remaining !== null && item.days_remaining < 0) {
        overdue.push(item);
      } else {
        upcoming.push(item);
      }
    }
    const byDue = (a: Obligation, b: Obligation) =>
      (a.filing_due_date ?? "9999").localeCompare(b.filing_due_date ?? "9999");
    return {
      overdue: overdue.sort(byDue),
      upcoming: upcoming.sort(byDue),
      incomplete: incomplete.sort(byDue),
    };
  }, [obligations.data]);

  async function transmitInvoice(obligation: Obligation | undefined) {
    const documentId = obligation?.triggering_evidence?.[0];
    if (!obligation || !documentId) {
      setActionError("Nu pot identifica factura care trebuie transmisă prin e-Factura.");
      return;
    }
    setBusyObligationId(obligation.id);
    setActionError(null);
    setActionNotice(null);
    try {
      const outcome = await apiFetch<{ simulated?: boolean; note?: string }>(
        `/api/v1/workspaces/${workspace.id}/efactura/transmit/${documentId}`,
        { method: "POST", idempotencyKey: newIdempotencyKey() },
      );
      // never claim a filing that did not happen: a simulated transport says so
      setActionNotice(
        outcome.simulated
          ? "Transmitere SIMULATĂ: serviciul ANAF nu este configurat, deci fișierul nu a " +
              "fost trimis nicăieri. Acceptarea este confirmată doar de recipisa ANAF."
          : outcome.note ??
              "Transmiterea prin RO e-Factura a fost înregistrată. Acceptarea este " +
                "confirmată doar de recipisa ANAF.",
      );
      obligations.reload();
      notifications.reload();
    } catch (caught) {
      setActionError(
        caught instanceof Error ? caught.message : "Transmiterea nu a putut fi inițiată.",
      );
    } finally {
      setBusyObligationId(null);
    }
  }

  async function runNextStep(item: NotificationItem) {
    if (item.next_step_kind === "review_registration") {
      // the assisted registration flow starts from the fiscal profile
      navigate("/setari");
      return;
    }
    if (item.next_step_kind === "resolve_missing_information") {
      // the facts a declaration asks for are answered in Setări → Informații fiscale pe an
      navigate("/setari");
      return;
    }
    if (item.next_step_kind === "review_declaration" && item.next_step_ref) {
      navigate(`/declaratii?declaration=${item.next_step_ref}`);
      return;
    }
    const obligation = obligationById.get(item.obligation_id);
    if (item.next_step_kind === "transmit_invoice") {
      await transmitInvoice(obligation);
      return;
    }
    await openDeclaration(obligation);
  }

  function nextStepLabel(item: NotificationItem): string {
    if (busyObligationId === item.obligation_id) return "Se procesează…";
    if (item.next_step_kind === "review_registration") return "Verifică înregistrarea fiscală";
    if (item.next_step_kind === "resolve_missing_information") return "Completează informațiile";
    if (item.next_step_kind === "review_declaration") return "Deschide declarația";
    if (item.next_step_kind === "transmit_invoice") return "Transmite prin e-Factura";
    return "Verifică și generează";
  }

  return (
    <div className="stack">
      <h1>{dict.nav.home}</h1>
      <p className="muted">
        {workspace.name} · PFA activ. Aici vedeți ce necesită atenție, când este scadent și care
        este pasul următor.
      </p>

      <section aria-labelledby="attention">
        <h2 id="attention">Ce necesită atenție</h2>
        {notifications.loading ? <p>{dict.labels.loading}</p> : null}
        {notifications.error ? (
          <p className="error" role="alert">
            {notifications.error.title}
          </p>
        ) : null}
        {notifications.data && notifications.data.items.length === 0 ? (
          <p className="muted">Nu există termene restante sau scadente.</p>
        ) : null}
        <ul className="cards">
          {(notifications.data?.items ?? []).slice(0, 6).map((item) => (
            <li key={item.id} className="card">
              <div className="card__head">
                <strong>{item.title_ro}</strong>
                <StatusBadge
                  status={item.overdue ? "overdue" : item.stage === "due" ? "due" : "upcoming"}
                  language={language}
                />
              </div>
              <p>{item.body_ro}</p>
              {item.next_step_kind === "transmit_invoice" && invoiceLabel(obligationById.get(item.obligation_id)) ? (
                <p>{invoiceLabel(obligationById.get(item.obligation_id))}</p>
              ) : null}
              <p className="muted">
                {dict.labels.deadline}:{" "}
                {item.due_on ? formatLongDate(item.due_on) : dict.disclaimer.unknownDeadline} ·{" "}
                {formatDeadlineDistance(item.days_remaining)}
              </p>
              <button
                type="button"
                disabled={busyObligationId === item.obligation_id}
                onClick={() => void runNextStep(item)}
              >
                {nextStepLabel(item)}
              </button>
            </li>
          ))}
        </ul>
        {actionError ? (
          <p className="error" role="alert">
            {actionError}
          </p>
        ) : null}
        {actionNotice ? (
          <p className="info" role="status">
            {actionNotice}
          </p>
        ) : null}
      </section>

      <section aria-labelledby="deadlines">
        <h2 id="deadlines">{dict.labels.nextDeadlines}</h2>
        {obligations.loading ? <p>{dict.labels.loading}</p> : null}
        {obligations.data && grouped.upcoming.length === 0 && grouped.overdue.length === 0 ? (
          <p className="muted">{dict.labels.noItems}</p>
        ) : null}
        <table>
          <caption className="visually-hidden">
            Obligații și termene, ordonate cu restanțele primele
          </caption>
          <thead>
            <tr>
              <th scope="col">Document</th>
              <th scope="col">{dict.labels.period}</th>
              <th scope="col">{dict.labels.deadline}</th>
              <th scope="col">{dict.labels.status}</th>
            </tr>
          </thead>
          <tbody>
            {[...grouped.overdue, ...grouped.upcoming, ...grouped.incomplete].map((item) => (
              <tr key={item.id}>
                <th scope="row">
                  {formLabel(item.form_code)}
                  <span className="muted"> ({item.form_code})</span>
                  {invoiceLabel(item) ? <><br /><small>{invoiceLabel(item)}</small></> : null}
                </th>
                <td>{formatPeriod(item.period_start, item.period_end)}</td>
                <td>
                  {item.filing_due_date
                    ? formatLongDate(item.filing_due_date)
                    : dict.disclaimer.unknownDeadline}
                  <br />
                  <small>{formatDeadlineDistance(item.days_remaining)}</small>
                  <br />
                  <small className="muted">{item.deadline_provenance ?? "—"}</small>
                </td>
                <td>
                  <StatusBadge status={item.applicability} language={language} />
                  {item.missing_inputs.length > 0 ? (
                    <div>
                      <small>
                        {dict.labels.missingInformation}:{" "}
                        {item.missing_inputs.map(missingInputLabel).join(", ")}
                      </small>
                    </div>
                  ) : null}
                  {item.unsupported_reason ? (
                    <div>
                      <small className="warn">{item.unsupported_reason}</small>
                    </div>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section aria-labelledby="figures">
        <h2 id="figures">Situația anului {dashboard.data?.income_year ?? ""}</h2>
        <dl className="figures">
          <div>
            <dt>{dict.labels.issuedRevenue}</dt>
            <dd>{formatMoney(dashboard.data?.issued_revenue)}</dd>
          </div>
          <div>
            <dt>{dict.labels.collectedRevenue}</dt>
            <dd>{formatMoney(dashboard.data?.collected_revenue)}</dd>
          </div>
          <div>
            <dt>{dict.labels.estimatedTaxes}</dt>
            <dd>{dict.disclaimer.estimate}</dd>
          </div>
        </dl>
      </section>

      <section aria-labelledby="unassessed">
        <h2 id="unassessed">Formulare fără detectare automată</h2>
        <p className="muted">
          Aceste obligații nu sunt evaluate automat în versiunea curentă și rămân vizibile pentru a
          nu dispărea din tablou.
        </p>
        <ul>
          {(obligations.data?.unassessed_forms ?? []).map((item) => (
            <li key={item.form_code}>
              <strong>{item.name_ro}</strong> ({item.form_code}) — {item.reason ?? "flux asistat"}
            </li>
          ))}
        </ul>
      </section>

      <p className="muted">
        Stări: {statusLabel(language, "required")}, {statusLabel(language, "needs_information")},{" "}
        {statusLabel(language, "not_required")}.
      </p>
    </div>
  );
}
