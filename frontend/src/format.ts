/** Romanian formatting helpers. Dates and amounts are never formatted ad hoc. */

const monthNames = [
  "ianuarie",
  "februarie",
  "martie",
  "aprilie",
  "mai",
  "iunie",
  "iulie",
  "august",
  "septembrie",
  "octombrie",
  "noiembrie",
  "decembrie",
];

/** "25 mai 2026" — the human-readable date used across the interface. */
export function formatLongDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [year, month, day] = iso.slice(0, 10).split("-").map((part) => Number(part));
  if (!year || !month || !day) return "—";
  return `${day} ${monthNames[month - 1] ?? ""} ${year}`;
}

/**
 * "100.000000" -> "100", "1500.500000" -> "1500.5".
 *
 * Amounts and percentages are stored with six decimals; a form field must show the number
 * the user typed, not the storage precision.
 */
export function trimDecimal(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  if (!text.includes(".")) return text;
  return text.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

/** "25.05.2026" — the compact form used in tables and deadlines. */
export function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [year, month, day] = iso.slice(0, 10).split("-");
  return `${day}.${month}.${year}`;
}

/**
 * The fiscal period an obligation covers: "septembrie 2026" for a calendar month,
 * "T3 2026" for a calendar quarter, "anul 2026" for a whole year, otherwise the exact
 * range.  A monthly decont is identified by the period it declares, never by its
 * deadline — September's D300 is due on 25 October, so a due date alone is ambiguous.
 */
export function formatPeriod(
  periodStart: string | null | undefined,
  periodEnd: string | null | undefined,
): string {
  if (!periodStart || !periodEnd) return "—";
  const start = periodStart.slice(0, 10);
  const end = periodEnd.slice(0, 10);
  if (start === end) return formatLongDate(start);
  const [sy, sm, sd] = start.split("-").map(Number);
  const [ey, em, ed] = end.split("-").map(Number);
  if (!sy || !sm || !ey || !em) return `${formatShortDate(start)} – ${formatShortDate(end)}`;
  const lastDay = new Date(Date.UTC(ey, em, 0)).getUTCDate();
  const wholeMonths = sy === ey && sd === 1 && ed === lastDay;
  if (wholeMonths && sm === em) return `${monthNames[sm - 1]} ${sy}`;
  if (wholeMonths && (sm - 1) % 3 === 0 && em === sm + 2) return `T${Math.ceil(sm / 3)} ${sy}`;
  if (wholeMonths && sm === 1 && em === 12) return `anul ${sy}`;
  return `${formatShortDate(start)} – ${formatShortDate(end)}`;
}

/**
 * Today as `YYYY-MM-DD`, in the browser's local time zone.
 *
 * Used as the default for document and payment dates.  The local calendar fields
 * are read directly: `toISOString()` would use UTC and could shift the date by one
 * day for users east or west of Greenwich.
 */
export function todayIso(now: Date = new Date()): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * Exact decimal formatting without ever going through a binary float.
 * "1234.5" -> "1.234,50 lei"
 */
export function formatMoney(value: string | number | null | undefined, currency = "RON"): string {
  if (value === null || value === undefined || value === "") return "—";
  const raw = typeof value === "number" ? value.toFixed(2) : String(value);
  const negative = raw.startsWith("-");
  const [integerPart = "0", fractionPart = ""] = raw.replace("-", "").split(".");
  const decimals = (fractionPart + "00").slice(0, 2);
  const grouped = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const suffix = currency === "RON" ? " lei" : ` ${currency}`;
  return `${negative ? "-" : ""}${grouped},${decimals}${suffix}`;
}

/** Days remaining / overdue, phrased the way the notification contract requires. */
export function formatDeadlineDistance(days: number | null | undefined): string {
  if (days === null || days === undefined) return "Termen de verificat";
  if (days === 0) return "scadență astăzi";
  if (days === 1) return "1 zi rămasă";
  if (days > 1) return `${days} zile rămase`;
  if (days === -1) return "1 zi de restanță";
  return `${Math.abs(days)} zile de restanță`;
}

export function formatPercent(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const trimmed = String(value).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
  return `${trimmed.replace(".", ",")}%`;
}
