import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FiscalRegimeForm } from "./FiscalRegimeForm";

describe("FiscalRegimeForm", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("submits the selected tax regime and VAT status together", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "updated" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onSaved = vi.fn();

    render(
      <FiscalRegimeForm
        workspaceId="ws-1"
        profile={{ tax_regimes: [], vat_registrations: [] }}
        onSaved={onSaved}
        onError={() => {}}
      />,
    );

    await userEvent.click(screen.getByRole("radio", { name: "Normă de venit" }));
    await userEvent.type(screen.getByLabelText("Venitul normat (RON/an)"), "45000");
    await userEvent.selectOptions(screen.getByLabelText("Statut"), "article_316");
    await userEvent.click(screen.getByRole("button", { name: "Salvează regimul fiscal" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toContain("/api/v1/workspaces/ws-1/fiscal-profile");
    const body = JSON.parse(String(init.body));
    expect(body.tax_regimes[0]).toMatchObject({ kind: "norm", norm_amount: "45000" });
    expect(body.vat_registrations[0]).toMatchObject({
      kind: "article_316",
      reporting_frequency: "monthly",
    });
    expect(body.year_facts).toEqual(
      expect.arrayContaining([expect.objectContaining({ fact_key: "regime", value: "norm" })]),
    );
  });

  it("prefills from the currently open periods", () => {
    render(
      <FiscalRegimeForm
        workspaceId="ws-1"
        profile={{
          tax_regimes: [
            {
              id: "r-1",
              kind: "norm",
              income_year: new Date().getFullYear(),
              effective_from: "2025-01-01",
              effective_to: null,
              norm_county: "Cluj",
              norm_locality: "Cluj-Napoca",
              norm_activity: "6201",
              norm_amount: "45000.000000",
            },
          ],
          vat_registrations: [
            {
              id: "v-1",
              kind: "article_316",
              vat_code: "RO90000009",
              effective_from: "2025-01-01",
              effective_to: null,
              reporting_frequency: "quarterly",
            },
          ],
        }}
        onSaved={() => {}}
        onError={() => {}}
      />,
    );

    expect(screen.getByRole("radio", { name: "Normă de venit" })).toBeChecked();
    expect(screen.getByLabelText("Venitul normat (RON/an)")).toHaveValue("45000");
    expect(screen.getByLabelText("Statut")).toHaveValue("article_316");
    expect(screen.getByLabelText("Perioada de raportare")).toHaveValue("quarterly");
  });
});
