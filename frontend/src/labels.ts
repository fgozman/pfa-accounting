import { ISO_COUNTRY_CODES } from "./countryCodes";

/**
 * Human-readable labels for stored enum codes.
 *
 * Codes are the storage contract (`vehicle`, `partially_deductible`, …); lists must
 * never show them to the user.  Values are Romanian-first, matching the interface.
 */

export const DEDUCTIBILITY_LABELS: Record<string, string> = {
  fully_deductible: "Integral deductibilă",
  partially_deductible: "Parțial deductibilă",
  non_deductible: "Nedeductibilă",
  undetermined: "Nedeterminată",
};

export const EXPENSE_CATEGORY_LABELS: Record<string, string> = {
  general: "Cheltuieli generale",
  vehicle: "Vehicul",
  computers: "Echipamente IT",
  travel: "Deplasare",
  protocol: "Protocol",
};

/** D390/D394 intra-community operation types (L/T/A/P/S/R). */
export const INTRACOMMUNITY_OPERATION_LABELS: Record<string, string> = {
  L: "Livrări intracomunitare de bunuri",
  T: "Livrări în operațiune triunghiulară",
  A: "Achiziții intracomunitare de bunuri",
  P: "Prestări intracomunitare de servicii",
  S: "Achiziții intracomunitare de servicii",
  R: "Livrări — regim special pentru agricultori",
};

/** Country codes accepted by the D390 nomenclator (XI = Northern Ireland, no GB). */
export const INTRACOMMUNITY_COUNTRIES: { code: string; name: string }[] = [
  { code: "AT", name: "Austria" },
  { code: "BE", name: "Belgia" },
  { code: "BG", name: "Bulgaria" },
  { code: "CZ", name: "Cehia" },
  { code: "CY", name: "Cipru" },
  { code: "HR", name: "Croația" },
  { code: "DK", name: "Danemarca" },
  { code: "EE", name: "Estonia" },
  { code: "FI", name: "Finlanda" },
  { code: "FR", name: "Franța" },
  { code: "DE", name: "Germania" },
  { code: "EL", name: "Grecia" },
  { code: "IE", name: "Irlanda" },
  { code: "XI", name: "Irlanda de Nord" },
  { code: "IT", name: "Italia" },
  { code: "LV", name: "Letonia" },
  { code: "LT", name: "Lituania" },
  { code: "LU", name: "Luxemburg" },
  { code: "MT", name: "Malta" },
  { code: "NL", name: "Olanda" },
  { code: "PL", name: "Polonia" },
  { code: "PT", name: "Portugalia" },
  { code: "SK", name: "Slovacia" },
  { code: "SI", name: "Slovenia" },
  { code: "ES", name: "Spania" },
  { code: "SE", name: "Suedia" },
  { code: "HU", name: "Ungaria" },
];

/** Every ISO alpha-2 country/territory plus the legacy D390 codes EL and XI.
 * The stored code remains unchanged; names come from the browser's Romanian locale.
 * This is deliberately separate from the smaller D390 nomenclator above.
 */
const regionNames = new Intl.DisplayNames(["ro"], { type: "region" });
const partnerCountries = [...ISO_COUNTRY_CODES, "EL", "XI"].map((code) => ({
  code,
  name: code === "XI" ? "Irlanda de Nord" : code === "EL" ? "Grecia" : regionNames.of(code) ?? code,
}));
const countrySort = new Intl.Collator("ro", { sensitivity: "base" });
export const PARTNER_COUNTRIES: { code: string; name: string }[] = [
  { code: "RO", name: "România" },
  ...partnerCountries.filter(({ code }) => code !== "RO")
    .sort((a, b) => countrySort.compare(a.name, b.name) || a.code.localeCompare(b.code)),
];

/**
 * Labels for the `calculated_values` keys shown in the declaration review.
 *
 * Keys are the storage/API contract (`balance_payable`, `collected_total`, …); the
 * review must never show them raw.  Unknown keys fall back to a humanised form.
 */
export const CALCULATED_VALUE_LABELS: Record<string, string> = {
  // income tax (D212 and the deterministic engine)
  income_year: "An de venit",
  regime: "Regim fiscal",
  gross_revenue: "Venit brut",
  eligible_expenses: "Cheltuieli deductibile",
  non_deductible_expenses: "Cheltuieli nedeductibile",
  depreciation: "Amortizare",
  net_income_before_losses: "Venit net înainte de pierderi",
  loss_offset: "Pierderi compensate",
  net_income: "Venit net",
  norm_income: "Venit normat",
  cas_base: "Bază CAS",
  cas_amount: "CAS datorată",
  cas_mandatory: "CAS obligatorie",
  cass_base: "Bază CASS",
  cass_amount: "CASS datorată",
  cass_mandatory: "CASS obligatorie",
  cass_ceiling_applied: "Plafon CASS aplicat",
  taxable_income: "Venit impozabil",
  income_tax: "Impozit pe venit",
  foreign_tax_credit: "Credit de impozit extern",
  total_contributions: "Total contribuții",
  total_due: "Total de plată",
  adjustments_total: "Total ajustări",
  chapters: "Capitole",
  // VAT (D300)
  collected_total: "TVA colectată",
  deductible_total: "TVA deductibilă",
  balance_payable: "TVA de plată",
  balance_carryforward: "TVA de reportat",
  previous_carryforward: "Sold de reportat anterior",
  refund_requested: "TVA de rambursat",
  sales: "Jurnal de vânzări",
  purchases: "Jurnal de cumpărări",
  assessments: "Evaluări pe operațiune",
  period: "Perioadă",
  // special VAT (D301 / D390 / D394)
  rows: "Operațiuni",
  operations: "Operațiuni",
  total_base: "Total bază",
  total_vat: "Total TVA",
  nr_pag: "Număr pagini",
  nrOPI: "Număr operatori intracomunitari",
  bazaL: "Bază L — livrări intracomunitare de bunuri",
  bazaT: "Bază T — operațiune triunghiulară",
  bazaA: "Bază A — achiziții intracomunitare de bunuri",
  bazaP: "Bază P — prestări intracomunitare de servicii",
  bazaS: "Bază S — achiziții intracomunitare de servicii",
  bazaR: "Bază R — regim special pentru agricultori",
  total_baza: "Total bază impozabilă",
  totalPlata_A: "Sumă de control",
  // line-level fields (VAT journal / assessment)
  transaction_id: "Identificator operațiune",
  tax_point: "Data exigibilității",
  base_ron: "Bază (RON)",
  vat_ron: "TVA (RON)",
  vat_rate: "Cotă TVA (%)",
  rate: "Cotă (%)",
  collected_vat: "TVA colectată",
  deductible_vat: "TVA deductibilă",
  category: "Categorie",
  counterparty_country: "Țara contrapartidei",
  note: "Notă",
  explanation: "Explicație",
  legal_reference: "Temei legal",
  unresolved: "Informații lipsă",
  warnings: "Avertismente",
  trace: "Explicații",
};

export function calculatedValueLabel(key: string): string {
  return (
    CALCULATED_VALUE_LABELS[key] ??
    key.replace(/_/g, " ").replace(/^./, (character) => character.toUpperCase())
  );
}

/**
 * Readable names for blocking inputs (`missing_inputs`), so a blocked validation says
 * what to complete instead of exposing storage keys.  Keys may carry a `:id` suffix.
 */
export const MISSING_INPUT_LABELS: Record<string, string> = {
  // declarant identity / PFA identification
  nume_declar: "Numele declarantului",
  prenume_declar: "Prenumele declarantului",
  functie_declar: "Funcția declarantului",
  den: "Denumirea PFA",
  adresa: "Adresa domiciliului fiscal",
  cui: "CUI / CIF valid",
  // classification
  intracommunity_operation_type: "Tipul operațiunii intracomunare (L/T/A/P/S/R)",
  counterparty_vat_id_verification: "Codul VIES verificat al partenerului",
  counterparty_classification: "Partenerul tranzacției (client sau furnizor)",
  business_use_percent: "Ponderea de utilizare în scop de afaceri",
  expense_business_use: "Ponderea de utilizare în scop de afaceri a unei cheltuieli",
  expense_deductibility: "Deductibilitatea unei cheltuieli",
  classification: "Justificarea clasificării operațiunii (taxare inversă)",
  vat_rate: "Cota de TVA de pe document (pentru autotaxare)",
  vat_transaction_classification: "Clasificarea TVA a unor documente",
  external_services_classification: "Clasificarea serviciilor externe",
  verified_registration_form: "Formularul de înregistrare fiscală verificat",
  // profile / year facts
  vat_registration: "Înregistrarea în scopuri de TVA",
  regime: "Regimul fiscal",
  norm_amount: "Venitul normat",
  cas_election: "Opțiunea CAS",
  cass_election: "Opțiunea CASS",
  prior_losses: "Pierderile reportate",
  salary_income_present: "Venituri din salarii",
  pension_status: "Statutul de pensionar",
  recalculation: "Recalcularea declarației",
};

export function missingInputLabel(key: string): string {
  const [base = key, reference] = key.split(":");
  const label = MISSING_INPUT_LABELS[base] ?? base.replace(/_/g, " ");
  // a per-document key carries the document id: name it, so the user knows where to look
  if (!reference) return label;
  return `${label} (document ${reference.slice(0, 8)})`;
}

/** Where the missing information is actually completed, by input key. */
const MISSING_INPUT_HINTS: Record<string, string> = {
  classification:
    "Cheltuieli: completați „Justificarea taxării inverse\" pe documentul cu tratament „Taxare inversă\".",
  vat_rate:
    "Cheltuieli: completați „Cotă TVA (%)\" pe documentul cu taxare inversă — cota la care se autotaxează.",
  tip: "Cheltuieli: alegeți „Tip operațiune intracomunitară\" (L/T/A/P/S/R) pe document.",
  tara: "Cheltuieli: completați „Țara furnizorului\" pe document.",
  denO: "Cheltuieli: completați „Furnizor\" pe document.",
  baza: "Cheltuieli: verificați suma documentului (baza operațiunii nu poate fi zero).",
  vat_transaction_classification:
    "Cheltuieli: stabiliți tratamentul TVA al documentelor semnalate (Setări → Profil fiscal arată înregistrarea).",
  external_services_classification:
    "Setări → Profil fiscal: înregistrați codul special (art. 317) pentru serviciile externe.",
  counterparty_classification:
    "Cheltuieli/Venituri: completați „Furnizor\" sau „Client\" pe document.",
  counterparty_vat_id_verification: "Cheltuieli: completați codul de TVA al partenerului.",
  business_use_percent:
    "Cheltuieli: completați „Pondere utilizare în scop de afaceri (%)\" în Detalii fiscale.",
  expense_business_use:
    "Cheltuieli: completați „Pondere utilizare în scop de afaceri (%)\" în Detalii fiscale.",
  expense_deductibility: "Cheltuieli: alegeți „Deductibilitate impozit pe venit\".",
  vat_registration: "Setări → Profil fiscal: înregistrarea în scopuri de TVA.",
  regime: "Setări → Profil fiscal: regimul fiscal.",
  verified_registration_form: "Setări → Profil fiscal: verificați datele de înregistrare.",
};

/** The place to fix one blocking input, or null when there is nothing specific to say. */
export function missingInputHint(key: string): string | null {
  const base = key.split(":")[0] ?? key;
  if (MISSING_INPUT_HINTS[base]) return MISSING_INPUT_HINTS[base]!;
  if (base in MISSING_INPUT_LABELS) {
    return "Setări → Informații fiscale pe an: răspundeți la informația fiscală.";
  }
  return null;
}

export function deductibilityLabel(value: string | null | undefined): string {
  if (!value) return "—";
  return DEDUCTIBILITY_LABELS[value] ?? value;
}

export function expenseCategoryLabel(value: string | null | undefined): string {
  if (!value) return "—";
  return EXPENSE_CATEGORY_LABELS[value] ?? value;
}

/**
 * Older expense lines stored the category code as the description (before the form
 * had a description field).  Such a value is not a description, so it is replaced
 * by the default document description — the category itself is shown in its own
 * column, never inside "Descriere".
 */
export const DEFAULT_EXPENSE_DESCRIPTION = "Achiziție";

export function expenseDescriptionLabel(description: string | null | undefined): string {
  const text = (description ?? "").trim();
  if (text && !(text in EXPENSE_CATEGORY_LABELS)) return text;
  return DEFAULT_EXPENSE_DESCRIPTION;
}
