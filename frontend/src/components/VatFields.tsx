export const VAT_TREATMENT_OPTIONS: { value: string; label: string }[] = [
  { value: "standard", label: "Taxabil — cotă standard" },
  { value: "reduced", label: "Taxabil — cotă redusă" },
  { value: "second_reduced", label: "Taxabil — cotă redusă specială" },
  { value: "non_deductible", label: "Taxabil, fără drept de deducere" },
  { value: "exempt", label: "Scutit de TVA" },
  { value: "reverse_charge", label: "Taxare inversă" },
  { value: "out_of_scope", label: "În afara sferei de aplicare" },
  { value: "undetermined", label: "Nedeclarat / necunoscut" },
];

/**
 * Whether the entered rate is used at all.  Matches the backend rule: an exempt,
 * out-of-scope or reverse-charge line carries no VAT computed from a rate.
 */
export function vatRateApplies(treatment: string): boolean {
  // a reverse charge carries no VAT on the paper, but the rate is still recorded: it is
  // what the beneficiary self-assesses, and the special return (D301) reports it
  return !["exempt", "out_of_scope"].includes(treatment);
}

/**
 * Taxable treatments carry a statutory rate: the invoice must record the rate it was
 * issued with, otherwise the document holds no VAT while the declaration applies the
 * legal rate and the two disagree.
 */
export function vatRateRequired(treatment: string): boolean {
  // a reverse charge has no VAT on the paper, but the rate is still needed: it is what the
  // beneficiary self-assesses in the special return (D301)
  return ["standard", "reduced", "second_reduced", "reverse_charge"].includes(treatment);
}

export function vatTreatmentLabel(value: string): string {
  return VAT_TREATMENT_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

/**
 * VAT treatment and rate of one document line.
 *
 * The rate is what is printed on the document; the application never guesses it.
 * VAT is only reported when the PFA is registered for VAT (Setări → Profil fiscal →
 * Înregistrare TVA) — the registration decides between D300 and the special D301.
 */
export function VatFields({
  idPrefix,
  treatment,
  onTreatmentChange,
  rate,
  onRateChange,
}: {
  idPrefix: string;
  treatment: string;
  onTreatmentChange: (value: string) => void;
  rate: string;
  onRateChange: (value: string) => void;
}) {
  return (
    <>
      <div className="field">
        <label htmlFor={`${idPrefix}-vat-treatment`}>Tratament TVA</label>
        <select
          id={`${idPrefix}-vat-treatment`}
          value={treatment}
          onChange={(event) => onTreatmentChange(event.target.value)}
        >
          {VAT_TREATMENT_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <small className="muted">
          Alegeți cum este tratată operațiunea din punct de vedere TVA: taxabilă, scutită sau
          supusă taxării inverse. Aceasta este diferită de deductibilitatea la impozitul pe venit.
        </small>
      </div>
      {vatRateApplies(treatment) ? (
        <div className="field">
          <label htmlFor={`${idPrefix}-vat-rate`}>
            {treatment === "reverse_charge" ? "Cotă de autotaxare (%)" : "Cotă TVA (%)"}
          </label>
          <input
            id={`${idPrefix}-vat-rate`}
            inputMode="decimal"
            value={rate}
            placeholder="ex. 21"
            required={vatRateRequired(treatment)}
            aria-invalid={vatRateRequired(treatment) && rate.trim() === ""}
            onChange={(event) => onRateChange(event.target.value)}
          />
          <small className="muted">
            {vatRateRequired(treatment)
              ? treatment === "reverse_charge"
                ? "Obligatorie pentru taxare inversă: cota la care vă autotaxați. Factura " +
                  "furnizorului nu conține TVA, dar decontul special (D301) o raportează. "
                : "Obligatorie pentru o operațiune taxabilă. "
              : ""}
            {treatment === "reverse_charge"
              ? "Cote legale: 21% standard, 11% redusă."
              : "Cota de pe document. TVA se aplică doar dacă PFA-ul este înregistrat în scopuri de TVA (Setări → Profil fiscal → Înregistrare TVA). Cote legale: 21% standard, 11% redusă."}
          </small>
        </div>
      ) : null}
    </>
  );
}
