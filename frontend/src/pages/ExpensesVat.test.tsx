import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ExpensesPage } from "./Expenses";

const workspace = { id: "ws-1", name: "PFA Demo", cui: "90000001", role: "DEFAULT" as const };

const savedDocument = {
  id: "d-1",
  status: "finalized",
  description: "Achiziție",
  issue_date: "2026-09-10",
  currency: "RON",
  total_amount: "1210.000000",
  total_vat: "210.000000",
  deductibility: "fully_deductible",
  business_use_percent: "100",
  expense_category: "general",
  intracommunity_operation_type: "S",
  vat_treatment: "reverse_charge",
};

describe("intra-community operation type on expenses", () => {
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

  it("asks for the L/T/A/P/S/R type on a reverse-charge expense and sends it", async () => {
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

    // not asked for a plain domestic expense
    expect(screen.queryByLabelText("Tip operațiune intracomunitară")).toBeNull();
    expect(screen.getByLabelText("Furnizor")).toHaveValue("");
    expect(screen.getByLabelText("Descriere")).toHaveValue("");
    expect(screen.getByLabelText("Sumă (RON, fără TVA)")).toHaveValue("");

    await userEvent.type(screen.getByLabelText("Furnizor"), "Furnizor test");
    await userEvent.type(screen.getByLabelText("Descriere"), "Servicii");
    await userEvent.type(screen.getByLabelText("Sumă (RON, fără TVA)"), "2000");
    await userEvent.selectOptions(screen.getByLabelText("Tratament TVA"), "reverse_charge");
    await userEvent.selectOptions(screen.getByLabelText("Țara furnizorului"), "DE");
    await userEvent.type(screen.getByLabelText(/Cod fiscal furnizor/), "123456789");
    await userEvent.selectOptions(
      screen.getByLabelText("Tip operațiune intracomunitară"),
      "S",
    );
    // D301 cannot be validated without the reason for the reverse charge
    await userEvent.type(
      screen.getByLabelText("Justificarea taxării inverse"),
      "achiziție intracomunitară de servicii, art. 331",
    );
    // the invoice carries no VAT, but the self-assessment rate is evidence the D301 needs
    await userEvent.type(screen.getByLabelText("Cotă de autotaxare (%)"), "21");
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.body).toMatchObject({
      vat_treatment: "reverse_charge",
      intracommunity_operation_type: "S",
      supplier_country: "DE",
      supplier_vat_id: "123456789",
      reverse_charge_justification: "achiziție intracomunitară de servicii, art. 331",
      lines: [expect.objectContaining({ vat_rate: "21", vat_treatment: "reverse_charge" })],
    });
  });

  it("brings back the reverse-charge justification and clean numbers when editing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const body = /\/documents\/[0-9a-f-]+$/.test(url)
          ? {
              ...savedDocument,
              status: "finalized",
              reverse_charge_justification: "achiziție intracomunitară de servicii, art. 331",
              business_use_percent: "100.000000",
              lines: [{ description: "Servicii", unit_price: "2000.000000", vat_rate: "21.000000" }],
            }
          : { items: [savedDocument] };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(<ExpensesPage language="ro" workspace={workspace} />);
    const table = within(await screen.findByRole("table"));
    await userEvent.click(table.getByRole("button", { name: "Editează" }));

    // the evidence entered before must come back, not an empty field
    expect(await screen.findByLabelText("Justificarea taxării inverse")).toHaveValue(
      "achiziție intracomunitară de servicii, art. 331",
    );
    // and the numbers are shown as typed, not with six decimals
    expect(screen.getByLabelText(/Sumă/)).toHaveValue("2000");
    expect(screen.getByLabelText("Pondere utilizare în scop de afaceri (%)")).toHaveValue("100");
  });

  it("defaults the business-use share to 100 for a fully deductible expense", async () => {
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

    const share = screen.getByLabelText("Pondere utilizare în scop de afaceri (%)");
    expect(share).toHaveValue(""); // not stated until the expense is classified

    await userEvent.selectOptions(
      screen.getByLabelText("Deductibilitate impozit pe venit"),
      "fully_deductible",
    );
    // the default is filled in and visible, not applied behind the user's back
    expect(share).toHaveValue("100");

    await userEvent.type(screen.getByLabelText("Furnizor"), "Furnizor test");
    await userEvent.type(screen.getByLabelText("Descriere"), "Servicii");
    await userEvent.type(screen.getByLabelText("Sumă (RON, fără TVA)"), "2000");
    await userEvent.type(screen.getByLabelText("Cotă TVA (%)"), "21");
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.body.business_use_percent).toBe("100");
    expect(posts[0]!.body.deductibility).toBe("fully_deductible");
  });

  it("never overwrites a share the user already stated", async () => {
    render(<ExpensesPage language="ro" workspace={workspace} />);
    await screen.findByText("Cheltuieli înregistrate");

    await userEvent.type(screen.getByLabelText("Pondere utilizare în scop de afaceri (%)"), "60");
    await userEvent.selectOptions(
      screen.getByLabelText("Deductibilitate impozit pe venit"),
      "fully_deductible",
    );

    expect(screen.getByLabelText("Pondere utilizare în scop de afaceri (%)")).toHaveValue("60");
  });

  it("refuses a reverse-charge expense with no justification", async () => {
    const posts: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "POST" && url.endsWith("/documents")) posts.push(url);
        return new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(<ExpensesPage language="ro" workspace={workspace} />);
    await screen.findByText("Cheltuieli înregistrate");

    await userEvent.selectOptions(screen.getByLabelText("Tratament TVA"), "reverse_charge");
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Justificarea taxării inverse/);
    expect(posts).toHaveLength(0);
  });

  it("does not invent the tax classification of an expense", async () => {
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

    // the form used to ship "vehicle", "partially deductible", 50% and a purpose
    expect(screen.getByLabelText("Categorie")).toHaveValue("general");
    expect(screen.getByLabelText("Deductibilitate impozit pe venit")).toHaveValue("undetermined");
    expect(screen.getByLabelText("Pondere utilizare în scop de afaceri (%)")).toHaveValue("");

    await userEvent.type(screen.getByLabelText("Furnizor"), "Furnizor test");
    await userEvent.type(screen.getByLabelText("Descriere"), "Servicii");
    await userEvent.type(screen.getByLabelText("Sumă (RON, fără TVA)"), "2000");
    await userEvent.type(screen.getByLabelText("Cotă TVA (%)"), "21");
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    // an unstated share is sent as null, never as a number the user did not choose
    expect(posts[0]!.body.business_use_percent).toBeNull();
    expect(posts[0]!.body.deductibility).toBe("undetermined");
    expect(posts[0]!.body.expense_category).toBe("general");
  });

  it("refuses a taxable expense that has no VAT rate", async () => {
    const posts: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "POST" && url.endsWith("/documents")) posts.push(url);
        return new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(<ExpensesPage language="ro" workspace={workspace} />);
    await screen.findByText("Cheltuieli înregistrate");

    // the default treatment is taxable and the rate is empty
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Cota de pe document/);
    expect(posts).toHaveLength(0);
  });

  it("records a purchase from an individual, so no partner is demanded for it", async () => {
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

    await userEvent.type(screen.getByLabelText("Serie (opțională)"), "FR");
    await userEvent.type(screen.getByLabelText("Număr"), "9911");
    await userEvent.selectOptions(screen.getByLabelText("Tip furnizor"), "consumer");
    // nothing to fill in for a private individual
    expect(screen.queryByLabelText("Furnizor", { exact: true })).toBeNull();
    await userEvent.type(screen.getByLabelText("Cotă TVA (%)"), "21");
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.body.series).toBe("FR");
    expect(posts[0]!.body.number).toBe("9911");
    expect(posts[0]!.body.partner_kind).toBe("consumer");
    // the default placeholder name is never sent as a real partner
    expect(posts[0]!.body.supplier_name_raw).toBeNull();
  });
});
