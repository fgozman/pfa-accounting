import { useEffect, useMemo, useState } from "react";

import { ApiError, apiFetch } from "../api/client";

export interface FactChoice {
  value: string;
  label: string;
}

export interface FactItem {
  fact_key: string;
  income_year: number;
  value_kind: string;
  value: string | number | boolean | null;
  answered: boolean;
  label_ro: string;
  group: string;
  unit_ro: string | null;
  help_ro: string | null;
  choices: FactChoice[];
  /** declarations that block until this fact is answered */
  required_for: string[];
  /** other answers that make this fact apply (e.g. a norm field needs regime = norm) */
  applies_when: { fact_key: string; values: string[] }[];
}

interface TaxFactsResponse {
  income_year: number;
  items: FactItem[];
  supported_years: number[];
}

/** "45000.000000" -> "45000": string arithmetic only, never a float. */
function trimDecimal(value: string | number | boolean | null): string {
  if (value === null || typeof value === "boolean") return "";
  const text = String(value);
  return text.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

function asControlValue(item: FactItem): string {
  if (item.value === null || item.value === undefined) return "";
  if (typeof item.value === "boolean") return item.value ? "true" : "false";
  if (item.value_kind === "decimal") return trimDecimal(item.value);
  return String(item.value);
}

/**
 * The per-year fiscal facts a declaration asks for.
 *
 * The catalogue (labels, types, allowed values) comes from the backend, so this form
 * cannot drift from what the declaration builders read.  An empty control means *not
 * answered*: the fact goes back to unknown and keeps blocking, which is the honest
 * state — it never becomes zero or "no".
 */
export function TaxFactsForm({
  workspaceId,
  onSaved,
  onError,
}: {
  workspaceId: string;
  onSaved: (message: string) => void;
  onError: (error: unknown) => void;
}) {
  const [year, setYear] = useState(() => new Date().getFullYear());
  const [data, setData] = useState<TaxFactsResponse | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    apiFetch<TaxFactsResponse>(`/api/v1/workspaces/${workspaceId}/tax-facts`, {
      query: { income_year: year },
    })
      .then((response) => {
        if (!active) return;
        setData(response);
        setValues(
          Object.fromEntries(response.items.map((item) => [item.fact_key, asControlValue(item)])),
        );
      })
      .catch((error: unknown) => {
        if (active) onError(error);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
    // onError is stable for the life of the page; re-fetching on every render would loop
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, year]);

  // a fact that does not apply to the current answers is not shown, not asked for and
  // not reported: a norm-only field means nothing while the regime is "real"
  const visibleItems = useMemo(
    () =>
      (data?.items ?? []).filter((item) =>
        item.applies_when.every((condition) =>
          condition.values.includes(values[condition.fact_key] ?? ""),
        ),
      ),
    [data, values],
  );

  const groups = useMemo(() => {
    const byGroup = new Map<string, FactItem[]>();
    for (const item of visibleItems) {
      const bucket = byGroup.get(item.group) ?? [];
      bucket.push(item);
      byGroup.set(item.group, bucket);
    }
    return [...byGroup.entries()];
  }, [visibleItems]);

  function answer(item: FactItem): unknown {
    const raw = values[item.fact_key] ?? "";
    if (raw.trim() === "") return null;
    if (item.value_kind === "boolean") return raw === "true";
    return raw.trim();
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const response = await apiFetch<TaxFactsResponse>(
        `/api/v1/workspaces/${workspaceId}/tax-facts`,
        {
          method: "PATCH",
          body: {
            income_year: year,
            answers: (data?.items ?? []).map((item) => ({
              fact_key: item.fact_key,
              value: answer(item),
            })),
          },
        },
      );
      setData(response);
      setValues(
        Object.fromEntries(response.items.map((item) => [item.fact_key, asControlValue(item)])),
      );
      // only facts that apply and actually block a declaration are worth warning about
      const answers = Object.fromEntries(
        response.items.map((item) => [item.fact_key, asControlValue(item)]),
      );
      const blocking = response.items.filter(
        (item) =>
          !item.answered &&
          item.required_for.length > 0 &&
          item.applies_when.every((condition) =>
            condition.values.includes(answers[condition.fact_key] ?? ""),
          ),
      );
      onSaved(
        blocking.length === 0
          ? `Informațiile fiscale pentru ${year} au fost salvate. Declarațiile nu mai au informații lipsă.`
          : `Salvat. Blochează încă: ${blocking.map((item) => item.label_ro).join(", ")}.`,
      );
    } catch (caught) {
      onError(caught as ApiError | Error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save}>
      <div className="field">
        <label htmlFor="tax-facts-year">Anul de venit</label>
        <select
          id="tax-facts-year"
          value={year}
          onChange={(event) => setYear(Number(event.target.value))}
        >
          {(data?.supported_years ?? [year]).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </div>

      {loading ? <p className="muted">Se încarcă…</p> : null}

      {groups.map(([group, items]) => (
        <fieldset key={group}>
          <legend>{group}</legend>
          {items.map((item) => (
            <div className="field" key={item.fact_key}>
              <label htmlFor={`fact-${item.fact_key}`}>
                {item.label_ro}
                {item.unit_ro ? ` (${item.unit_ro})` : ""}
              </label>
              {item.value_kind === "boolean" ? (
                <select
                  id={`fact-${item.fact_key}`}
                  value={values[item.fact_key] ?? ""}
                  onChange={(event) =>
                    setValues((current) => ({ ...current, [item.fact_key]: event.target.value }))
                  }
                >
                  <option value="">— fără răspuns —</option>
                  <option value="true">Da</option>
                  <option value="false">Nu</option>
                </select>
              ) : item.choices.length > 0 ? (
                <select
                  id={`fact-${item.fact_key}`}
                  value={values[item.fact_key] ?? ""}
                  onChange={(event) =>
                    setValues((current) => ({ ...current, [item.fact_key]: event.target.value }))
                  }
                >
                  <option value="">— fără răspuns —</option>
                  {item.choices.map((choice) => (
                    <option key={choice.value} value={choice.value}>
                      {choice.label}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  id={`fact-${item.fact_key}`}
                  inputMode="decimal"
                  value={values[item.fact_key] ?? ""}
                  onChange={(event) =>
                    setValues((current) => ({ ...current, [item.fact_key]: event.target.value }))
                  }
                />
              )}
              {item.help_ro ? <small className="muted">{item.help_ro}</small> : null}
              {!item.answered && item.required_for.length > 0 ? (
                <small className="warn">
                  Fără răspuns: blochează {item.required_for.join(", ")}.
                </small>
              ) : null}
            </div>
          ))}
        </fieldset>
      ))}

      <div className="actions">
        <button type="submit" className="primary" disabled={busy || loading}>
          {busy ? "Se salvează…" : "Salvează informațiile fiscale"}
        </button>
      </div>
    </form>
  );
}
