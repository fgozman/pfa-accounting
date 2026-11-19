import { describe, expect, it } from "vitest";

import {
  formatDeadlineDistance,
  formatLongDate,
  formatMoney,
  formatPercent,
  formatPeriod,
  formatShortDate,
  todayIso,
  trimDecimal,
} from "./format";

describe("Romanian formatting", () => {
  it("renders dates as '25 mai 2026'", () => {
    expect(formatLongDate("2026-05-25")).toBe("25 mai 2026");
    expect(formatShortDate("2026-05-25")).toBe("25.05.2026");
    expect(formatLongDate(null)).toBe("—");
  });

  it("defaults document dates to the current local day", () => {
    // local calendar fields, not UTC: 1 Jan 00:30 local must stay 1 January
    expect(todayIso(new Date(2026, 0, 1, 0, 30))).toBe("2026-01-01");
    expect(todayIso(new Date(2026, 11, 31, 23, 45))).toBe("2026-12-31");
    const today = new Date();
    expect(todayIso()).toBe(
      `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(
        today.getDate(),
      ).padStart(2, "0")}`,
    );
  });

  it("groups thousands with dots and uses a comma for decimals", () => {
    expect(formatMoney("1234.5")).toBe("1.234,50 lei");
    expect(formatMoney("24300")).toBe("24.300,00 lei");
    expect(formatMoney("-95.5")).toBe("-95,50 lei");
    expect(formatMoney("1000000")).toBe("1.000.000,00 lei");
  });

  it("never loses precision through a binary float", () => {
    // 0.1 + 0.2 in binary floating point would render as 0,30000000000000004
    expect(formatMoney("0.3")).toBe("0,30 lei");
    expect(formatMoney("0.1")).toBe("0,10 lei");
  });

  it("phrases deadline distances the way the notification contract requires", () => {
    expect(formatDeadlineDistance(0)).toBe("scadență astăzi");
    expect(formatDeadlineDistance(1)).toBe("1 zi rămasă");
    expect(formatDeadlineDistance(7)).toBe("7 zile rămase");
    expect(formatDeadlineDistance(-3)).toBe("3 zile de restanță");
    expect(formatDeadlineDistance(null)).toBe("Termen de verificat");
  });

  it("names the fiscal period, so a monthly decont is identifiable", () => {
    // September's D300 is due on 25 October: the period is the only identifier
    expect(formatPeriod("2026-09-01", "2026-09-30")).toBe("septembrie 2026");
    expect(formatPeriod("2026-02-01", "2026-02-28")).toBe("februarie 2026");
    expect(formatPeriod("2024-02-01", "2024-02-29")).toBe("februarie 2024");
    expect(formatPeriod("2026-07-01", "2026-09-30")).toBe("T3 2026");
    expect(formatPeriod("2026-01-01", "2026-12-31")).toBe("anul 2026");
    expect(formatPeriod("2026-09-17", "2026-09-17")).toBe("17 septembrie 2026");
    expect(formatPeriod("2026-09-17", "2026-09-20")).toBe("17.09.2026 – 20.09.2026");
    expect(formatPeriod(null, null)).toBe("—");
  });

  it("shows a stored amount without its storage precision", () => {
    expect(trimDecimal("100.000000")).toBe("100");
    expect(trimDecimal("1500.500000")).toBe("1500.5");
    expect(trimDecimal("0.000000")).toBe("0");
    expect(trimDecimal("21")).toBe("21");
    expect(trimDecimal(null)).toBe("");
  });

  it("renders percentages without trailing zeros", () => {
    expect(formatPercent("50.000000")).toBe("50%");
    expect(formatPercent("33.5")).toBe("33,5%");
    expect(formatPercent("100")).toBe("100%");
    expect(formatPercent(null)).toBe("—");
  });
});
