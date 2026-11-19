import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExpensesPage } from "./Expenses";
import { IncomePage } from "./Income";

const workspace = { id: "ws-1", name: "PFA Demo", cui: "90000001", role: "DEFAULT" as const };

const row = {
  id: "d-1",
  kind: "sales_invoice",
  status: "finalized",
  series: "F",
  number: "1",
  description: "Consultanță, Suport",
  issue_date: "2026-03-10",
  currency: "RON",
  total_amount: "1500.000000",
  total_vat: "210.000000",
  vat_treatment: "standard",
  deductibility: "fully_deductible",
  business_use_percent: "100",
  expense_category: "general",
};

function stub(items: unknown[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ items }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    ),
  );
}

describe("document lists show what each document is about", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the description column in 'Facturi emise'", async () => {
    stub([row]);
    render(<IncomePage language="ro" workspace={workspace} />);

    const table = within(await screen.findByRole("table"));
    expect(await screen.findByText("Consultanță, Suport")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Descriere" })).toBeInTheDocument();
    // the VAT value is shown next to the gross total
    expect(table.getByRole("columnheader", { name: "TVA" })).toBeInTheDocument();
    expect(table.getByText("1.500,00 lei")).toBeInTheDocument();
    expect(table.getByText("210,00 lei")).toBeInTheDocument();
    // the status is a label, never the raw code
    expect(table.getByText("finalizată")).toBeInTheDocument();
    // an issued document can be edited (the previous state is kept) and deleted
    expect(table.getByRole("button", { name: "Editează" })).toBeInTheDocument();
    expect(table.getByRole("button", { name: "Șterge" })).toBeInTheDocument();
  });

  it("edits an issued document without finalizing it again", async () => {
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, method: init?.method ?? "GET" });
        // the list endpoint returns { items }, the detail endpoint returns one document
        const isDetail = /\/documents\/[0-9a-f-]+$/.test(url) && (init?.method ?? "GET") === "GET";
        const body = isDetail
          ? { ...row, lines: [{ description: "Consultanță", unit_price: "1500", vat_rate: "14" }] }
          : /\/documents(\?|$)/.test(url)
            ? { items: [row] }
            : row;
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(<IncomePage language="ro" workspace={workspace} />);
    const table = within(await screen.findByRole("table"));
    await userEvent.click(table.getByRole("button", { name: "Editează" }));

    expect(await screen.findByText(/Se editează un document emis/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));
    await waitFor(() => expect(calls.some((call) => call.method === "PATCH")).toBe(true));
    // an issued document is already locked: it must not be finalized again
    expect(calls.some((call) => call.url.includes("/finalize"))).toBe(false);
  });

  it("deletes a document from the list after confirmation", async () => {
    const methods: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        methods.push(init?.method ?? "GET");
        return new Response(JSON.stringify({ items: [row] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    vi.stubGlobal("confirm", vi.fn(() => true));

    render(<IncomePage language="ro" workspace={workspace} />);
    const table = within(await screen.findByRole("table"));
    await userEvent.click(table.getByRole("button", { name: "Șterge" }));

    await waitFor(() => expect(methods).toContain("DELETE"));
  });

  it("resumes an interrupted edit after a reload", async () => {
    // the tab remembers which document was being edited, and for which workspace
    sessionStorage.setItem(
      "pfa.edit.income",
      JSON.stringify({ workspaceId: workspace.id, id: "d-1" }),
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const body = /\/documents\/[0-9a-f-]+$/.test(url)
          ? { ...row, lines: [{ description: "Ciornă în lucru", unit_price: "1500", vat_rate: "14" }] }
          : { items: [row] };
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(<IncomePage language="ro" workspace={workspace} />);

    expect(await screen.findByText(/Se editează un document emis/)).toBeInTheDocument();
    expect(await screen.findByLabelText("Descriere")).toHaveValue("Ciornă în lucru");
    sessionStorage.clear();
  });

  it("does not resume an edit that belongs to another workspace", async () => {
    sessionStorage.setItem(
      "pfa.edit.income",
      JSON.stringify({ workspaceId: "other-workspace", id: "d-1" }),
    );
    stub([row]);

    render(<IncomePage language="ro" workspace={workspace} />);
    await screen.findByRole("table");

    // restoring a document from another workspace only produces a 404
    expect(screen.queryByText(/Se editează un document emis/)).toBeNull();
    sessionStorage.clear();
  });

  it("records a receipt for the document's total, not for the net amount", async () => {
    const payments: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "POST" && url.includes("/payments")) {
          payments.push(String(init.body));
          return new Response(JSON.stringify({ id: "p-1" }), {
            status: 201,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (url.includes("/settlement")) {
          return new Response(
            JSON.stringify({ settled: "0", outstanding: "0", paid_in_full: false }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        const body = url.includes("/finalize")
          ? { ...row, lines: [] }
          : /\/documents(\?|$)/.test(url)
            ? { items: [] }
            : row;
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(<IncomePage language="ro" workspace={workspace} />);
    await screen.findByText("Facturi emise");
    await userEvent.type(screen.getByLabelText("Număr", { exact: true }), "1");
    await userEvent.type(screen.getByLabelText("Cotă TVA (%)"), "21");
    await userEvent.click(screen.getByRole("radio", { name: "Da" }));
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));

    await waitFor(() => expect(payments).toHaveLength(1));
    // the fixture's total is the gross amount; the net typed in the form is 1000
    expect(JSON.parse(payments[0]!)).toMatchObject({
      amount: "1500.000000",
      allocations: [{ amount: "1500.000000" }],
    });
  });

  it("never records a second receipt while editing a document", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, method: init?.method ?? "GET", body: String(init?.body ?? "") });
        const body = /\/documents\/[0-9a-f-]+$/.test(url)
          ? { ...row, lines: [{ description: "Consultanță", unit_price: "1500", vat_rate: "14" }] }
          : /\/documents(\?|$)/.test(url)
            ? { items: [row] }
            : row;
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(<IncomePage language="ro" workspace={workspace} />);
    const table = within(await screen.findByRole("table"));
    await userEvent.click(table.getByRole("button", { name: "Editează" }));
    await screen.findByText(/Se editează un document emis/);

    // the receipt question belongs to the add flow: a correction must not allocate a
    // second receipt to the same document (the API refuses it as over-allocation)
    expect(screen.queryByText("Ai încasat?")).toBeNull();
    expect(screen.getByText(/Încasările se înregistrează separat/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));
    await waitFor(() => expect(calls.some((call) => call.method === "PATCH")).toBe(true));
    expect(calls.some((call) => call.url.includes("/payments"))).toBe(false);
  });

  it("offers to edit the existing document when a create is refused as a duplicate", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "POST" && /\/documents(\?|$)/.test(url)) {
          return new Response(
            JSON.stringify({
              type: "about:blank",
              title: "Documentul pare a fi deja înregistrat (duplicat detectat).",
              status: 409,
              code: "conflict",
              correlation_id: "c",
              details: { duplicates: [{ id: "existing-1", number: "1", issue_date: "2026-03-10" }] },
            }),
            { status: 409, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(<IncomePage language="ro" workspace={workspace} />);
    await screen.findByText("Facturi emise");
    await userEvent.type(screen.getByLabelText("Număr", { exact: true }), "1");
    await userEvent.type(screen.getByLabelText("Cotă TVA (%)"), "21");
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));

    // the refusal names the document it matched, and the user can go and correct it
    expect(await screen.findByText(/Documentul există deja/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deschide-l pentru editare" })).toBeInTheDocument();
  });

  it("shows labelled deductibility and usage in 'Cheltuieli înregistrate'", async () => {
    stub([{ ...row, kind: "purchase_invoice" }]);
    render(<ExpensesPage language="ro" workspace={workspace} />);

    const table = within(await screen.findByRole("table"));
    expect(await screen.findByText("Consultanță, Suport")).toBeInTheDocument();
    expect(table.getByText("Cheltuieli generale")).toBeInTheDocument();
    expect(table.getByRole("columnheader", { name: "TVA" })).toBeInTheDocument();
    expect(table.getByText("210,00 lei")).toBeInTheDocument();
    expect(table.getByText("Integral deductibilă")).toBeInTheDocument();
    expect(table.getByText("100%")).toBeInTheDocument();
    expect(table.queryByText("fully_deductible")).toBeNull();
  });

  it("shows the default description for legacy lines that stored the category code", async () => {
    stub([
      {
        ...row,
        kind: "purchase_invoice",
        description: "vehicle",
        expense_category: "vehicle",
        deductibility: "partially_deductible",
        business_use_percent: "50.000000",
      },
    ]);
    render(<ExpensesPage language="ro" workspace={workspace} />);

    const table = within(await screen.findByRole("table"));
    // the category is not a description: it gets its own column
    expect(table.getByText("Achiziție")).toBeInTheDocument();
    expect(table.getByText("Vehicul")).toBeInTheDocument();
    expect(table.getByText("Parțial deductibilă")).toBeInTheDocument();
    expect(table.getByText("50%")).toBeInTheDocument();
    expect(table.queryByText("vehicle")).toBeNull();
    expect(table.queryByText("partially_deductible")).toBeNull();
  });
});
