import { useEffect, useState } from "react";

import { ApiError, apiFetch } from "../api/client";
import { todayIso, trimDecimal } from "../format";

export interface RegimeRow {
  id: string;
  kind: string;
  income_year: number;
  effective_from: string;
  effective_to: string | null;
  norm_county: string | null;
  norm_locality: string | null;
  norm_activity: string | null;
  norm_amount: string | null;
}

export interface VatRow {
  id: string;
  kind: string;
  vat_code?: string | null;
  effective_from: string;
  effective_to: string | null;
  reporting_frequency: string | null;
}

export interface FiscalProfileLike {
  tax_regimes: RegimeRow[];
  vat_registrations: VatRow[];
}

export const REGIME_LABELS: Record<string, string> = {
  real: "Sistem real",
  norm: "Normă de venit",
};

export const VAT_LABELS: Record<string, string> = {
  none: "Neînregistrat",
  article_316: "Înregistrat (art. 316)",
  article_317: "Înregistrat (art. 317)",
};

type RegimeKind = "real" | "norm";
type VatKind = "none" | "article_316" | "article_317";
type Frequency = "monthly" | "quarterly";

function latestRegime(rows: RegimeRow[]): RegimeRow | null {
  const open = rows.filter((row) => row.effective_to === null);
  const pool = open.length > 0 ? open : rows;
  return [...pool].sort((a, b) => b.effective_from.localeCompare(a.effective_from))[0] ?? null;
}

function latestVatRegistration(rows: VatRow[]): VatRow | null {
  const open = rows.filter((row) => row.effective_to === null);
  const pool = open.length > 0 ? open : rows;
  return [...pool].sort((a, b) => b.effective_from.localeCompare(a.effective_from))[0] ?? null;
}

/**
 * Fiscal regime and VAT registration selection.
 *
 * The profile is *versioned*: saving closes the previous period on the chosen
 * effective date (today by default, so a change is never backdated into an existing
 * period) and opens the new one, so history is preserved and the same values can be
 * saved twice without creating duplicates.  The regime is also written as the
 * `regime` year fact, which is what the declaration engine reads.
 */
export function FiscalRegimeForm({
  workspaceId,
  profile,
  onSaved,
  onError,
}: {
  workspaceId: string;
  profile: FiscalProfileLike | null;
  onSaved: (message: string) => void;
  onError: (error: unknown) => void;
}) {
  const [effectiveFrom, setEffectiveFrom] = useState(() => todayIso());
  const [regimeKind, setRegimeKind] = useState<RegimeKind>("real");
  const [normCounty, setNormCounty] = useState("");
  const [normLocality, setNormLocality] = useState("");
  const [normActivity, setNormActivity] = useState("");
  const [normAmount, setNormAmount] = useState("");
  const [vatKind, setVatKind] = useState<VatKind>("none");
  const [vatFrequency, setVatFrequency] = useState<Frequency>("monthly");
  const [vatCode, setVatCode] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const regime = profile ? latestRegime(profile.tax_regimes) : null;
    setRegimeKind(regime?.kind === "norm" ? "norm" : "real");
    setNormCounty(regime?.norm_county ?? "");
    setNormLocality(regime?.norm_locality ?? "");
    setNormActivity(regime?.norm_activity ?? "");
    setNormAmount(trimDecimal(regime?.norm_amount ?? null));
  }, [profile]);

  useEffect(() => {
    const registration = profile ? latestVatRegistration(profile.vat_registrations) : null;
    setVatKind(
      registration?.kind === "article_316" || registration?.kind === "article_317"
        ? registration.kind
        : "none",
    );
    setVatFrequency(registration?.reporting_frequency === "quarterly" ? "quarterly" : "monthly");
    setVatCode(registration?.vat_code ?? "");
  }, [profile]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const incomeYear = Number(effectiveFrom.slice(0, 4)) || new Date().getFullYear();
      const yearFacts: {
        income_year: number;
        fact_key: string;
        value_kind: string;
        value: string;
      }[] = [
        { income_year: incomeYear, fact_key: "regime", value_kind: "text", value: regimeKind },
      ];
      if (regimeKind === "norm") {
        yearFacts.push({
          income_year: incomeYear,
          fact_key: "norm_amount",
          value_kind: "decimal",
          value: normAmount === "" ? "0" : normAmount,
        });
        yearFacts.push({
          income_year: incomeYear,
          fact_key: "norm_adjustment_percent",
          value_kind: "decimal",
          value: "0",
        });
      }
      await apiFetch(`/api/v1/workspaces/${workspaceId}/fiscal-profile`, {
        method: "PATCH",
        body: {
          tax_regimes: [
            {
              kind: regimeKind,
              income_year: incomeYear,
              effective_from: effectiveFrom,
              norm_county: regimeKind === "norm" ? normCounty || null : null,
              norm_locality: regimeKind === "norm" ? normLocality || null : null,
              norm_activity: regimeKind === "norm" ? normActivity || null : null,
              norm_amount: regimeKind === "norm" && normAmount !== "" ? normAmount : null,
            },
          ],
          vat_registrations: [
            {
              kind: vatKind,
              effective_from: effectiveFrom,
              vat_code: vatKind === "none" ? null : vatCode || null,
              reporting_frequency: vatKind === "none" ? null : vatFrequency,
            },
          ],
          year_facts: yearFacts,
        },
      });
      onSaved("Regimul fiscal și înregistrarea TVA au fost salvate.");
    } catch (caught) {
      onError(caught instanceof ApiError ? caught : caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save}>
      <h3>Regim fiscal și înregistrare TVA</h3>
      <p className="muted">
        Modificările sunt versionate: perioada anterioară se închide în prima zi a anului fiscal
        ales, iar istoricul rămâne vizibil mai sus.
      </p>

      <div className="field">
        <label htmlFor="effective-from">Valabil de la</label>
        <input
          id="effective-from"
          type="date"
          required
          value={effectiveFrom}
          onChange={(event) => setEffectiveFrom(event.target.value)}
        />
        <small className="muted">
          Data de la care se aplică regimul și statutul de TVA; perioada anterioară se închide în
          această zi.
        </small>
      </div>

      <fieldset>
        <legend>Regim fiscal</legend>
        <label>
          <input
            type="radio"
            name="regime"
            checked={regimeKind === "real"}
            onChange={() => setRegimeKind("real")}
          />
          {REGIME_LABELS.real}
        </label>
        <label>
          <input
            type="radio"
            name="regime"
            checked={regimeKind === "norm"}
            onChange={() => setRegimeKind("norm")}
          />
          {REGIME_LABELS.norm}
        </label>
        {regimeKind === "norm" ? (
          <div className="subform">
            <div className="field">
              <label htmlFor="norm-county">Județ (normă)</label>
              <input
                id="norm-county"
                value={normCounty}
                onChange={(event) => setNormCounty(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="norm-locality">Localitate (normă)</label>
              <input
                id="norm-locality"
                value={normLocality}
                onChange={(event) => setNormLocality(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="norm-activity">Cod CAEN (normă)</label>
              <input
                id="norm-activity"
                value={normActivity}
                onChange={(event) => setNormActivity(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="norm-amount">Venitul normat (RON/an)</label>
              <input
                id="norm-amount"
                inputMode="decimal"
                required
                value={normAmount}
                onChange={(event) => setNormAmount(event.target.value)}
              />
            </div>
          </div>
        ) : null}
      </fieldset>

      <fieldset>
        <legend>Înregistrare TVA</legend>
        <div className="field">
          <label htmlFor="vat-kind">Statut</label>
          <select
            id="vat-kind"
            value={vatKind}
            onChange={(event) => setVatKind(event.target.value as VatKind)}
          >
            <option value="none">{VAT_LABELS.none}</option>
            <option value="article_316">{VAT_LABELS.article_316}</option>
            <option value="article_317">{VAT_LABELS.article_317}</option>
          </select>
        </div>
        {vatKind !== "none" ? (
          <div className="subform">
            <div className="field">
              <label htmlFor="vat-code">Cod de TVA</label>
              <input
                id="vat-code"
                value={vatCode}
                placeholder="RO…"
                onChange={(event) => setVatCode(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="vat-frequency">Perioada de raportare</label>
              <select
                id="vat-frequency"
                value={vatFrequency}
                onChange={(event) => setVatFrequency(event.target.value as Frequency)}
              >
                <option value="monthly">Lunar</option>
                <option value="quarterly">Trimestrial</option>
              </select>
            </div>
          </div>
        ) : null}
      </fieldset>

      <button type="submit" className="primary" disabled={busy}>
        Salvează regimul fiscal
      </button>
    </form>
  );
}
