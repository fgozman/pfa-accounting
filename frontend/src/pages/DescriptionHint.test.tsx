import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ExpensesPage } from "./Expenses";
import { IncomePage } from "./Income";

const workspace = { id: "ws-1", name: "PFA Demo", cui: "90000001", role: "DEFAULT" as const };

const savedDocument = {
  id: "d-1",
  status: "finalized",
  kind: "purchase_invoice",
  issue_date: "2026-01-01",
  currency: "RON",
  total_amount: "508.250000",
  total_vat: "0.000000",
  vat_treatment: "standard",
  deductibility: "fully_deductible",
  business_use_percent: "100",
  expense_category: "general",
};

describe("description of foreign-currency documents", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ items: [] }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("explains the convention under the description on both pages", async () => {
    const income = render(<IncomePage language="ro" workspace={workspace} />);
    expect(await screen.findByText(/Factură externă/)).toBeInTheDocument();
    income.unmount();

    render(<ExpensesPage language="ro" workspace={workspace} />);
    expect(await screen.findByText(/Factură externă/)).toBeInTheDocument();
  });

  it("records the description on the expense line", async () => {
    const posts: { body: Record<string, unknown> }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "POST" && url.endsWith("/documents")) {
          posts.push({ body: JSON.parse(String(init.body)) as Record<string, unknown> });
          return new Response(JSON.stringify(savedDocument), {
            status: 201,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.includes("/finalize")) {
          return new Response(JSON.stringify(savedDocument), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(<ExpensesPage language="ro" workspace={workspace} />);
    await screen.findByText("Cheltuieli înregistrate");

    const note = "Factura 123/2026 — 100 EUR × 5,0825 = 508,25 RON";
    await userEvent.type(screen.getByLabelText("Furnizor"), "Furnizor test");
    await userEvent.type(screen.getByLabelText("Sumă (RON, fără TVA)"), "508.25");
    await userEvent.clear(screen.getByLabelText("Descriere"));
    await userEvent.type(screen.getByLabelText("Descriere"), note);
    // a taxable document must carry its rate
    await userEvent.type(screen.getByLabelText("Cotă TVA (%)"), "21");
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.body.lines).toEqual([expect.objectContaining({ description: note })]);
  });
});
