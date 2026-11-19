import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SettlementsPage } from "./Settlements";

const workspace = { id: "ws-1", name: "PFA Demo", cui: "90000001", role: "DEFAULT" as const };

const receipt = {
  id: "p-1",
  direction: "inflow",
  kind: "customer_receipt",
  method: "bank",
  paid_on: "2026-03-20",
  currency: "RON",
  amount: "4000.000000",
  amount_ron: "4000.000000",
  reference: "OP 1",
  note: null,
  allocations: [
    {
      document_id: "d-1",
      document: "F1",
      document_exists: true,
      issue_date: "2026-03-10",
      amount: "4000.000000",
      allocation_kind: "settlement",
    },
  ],
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

describe("SettlementsPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists receipts with the document they are applied to", async () => {
    stub([receipt]);
    render(<SettlementsPage workspace={workspace} />);

    const table = within(await screen.findByRole("table"));
    expect(table.getByText("Încasare")).toBeInTheDocument();
    expect(table.getByText("4.000,00 lei")).toBeInTheDocument();
    // the allocation names the document it pays
    expect(table.getByText(/F1 \(4.000,00 lei\)/)).toBeInTheDocument();
  });

  it("corrects a receipt and keeps its single allocation in step", async () => {
    const calls: { url: string; method: string; body: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, method: init?.method ?? "GET", body: String(init?.body ?? "") });
        const body =
          (init?.method ?? "GET") === "PATCH"
            ? { ...receipt, amount: "6000.000000", allocations: [{ ...receipt.allocations[0], amount: "6000.000000" }] }
            : { items: [receipt] };
        return new Response(JSON.stringify(body), {
          status: init?.method === "PATCH" ? 200 : 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(<SettlementsPage workspace={workspace} />);
    const table = within(await screen.findByRole("table"));
    await userEvent.click(table.getByRole("button", { name: "Editează" }));

    expect(await screen.findByText(/suma alocată urmează suma corectată/i)).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText('Sumă', { selector: '#settlement-amount' }));
    await userEvent.type(screen.getByLabelText('Sumă', { selector: '#settlement-amount' }), "6000");
    await userEvent.click(screen.getByRole("button", { name: "Salvează corectura" }));

    await waitFor(() => expect(calls.some((call) => call.method === "PATCH")).toBe(true));
    const patch = calls.find((call) => call.method === "PATCH")!;
    expect(JSON.parse(patch.body)).toMatchObject({ amount: "6000" });
  });

  it("tells the user that the document of a settlement was deleted", async () => {
    stub([
      {
        ...receipt,
        allocations: [
          {
            document_id: "d-gone",
            document: null,
            document_exists: false,
            issue_date: null,
            amount: "4000.000000",
            allocation_kind: "settlement",
          },
        ],
      },
    ]);
    render(<SettlementsPage workspace={workspace} />);

    const table = within(await screen.findByRole("table"));
    expect(table.getByText(/documentul a fost șters/)).toBeInTheDocument();
    expect(table.queryByText(/Nealocată/)).toBeNull();
  });

  it("calls a settlement with no allocations unallocated", async () => {
    stub([{ ...receipt, allocations: [] }]);
    render(<SettlementsPage workspace={workspace} />);

    const table = within(await screen.findByRole("table"));
    expect(table.getByText("Nealocată")).toBeInTheDocument();
  });

  it("names each movement type exactly once", async () => {
    stub([]);
    render(<SettlementsPage workspace={workspace} />);

    const options = await screen
      .findByRole("option", { name: /Aportul titularului/ })
      .then((o) => [o.textContent]);
    // „Încasare — Aportul titularului", never „Încasare — Încasare — …"
    expect(options[0]).toMatch(/^Încasare — Aportul titularului$/);
    expect(options[0]).not.toMatch(/Încasare — Încasare/);
  });

  it("removes a receipt after confirmation so the document is released", async () => {
    const methods: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        methods.push(init?.method ?? "GET");
        return new Response(JSON.stringify({ items: [receipt] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    vi.stubGlobal("confirm", vi.fn(() => true));

    render(<SettlementsPage workspace={workspace} />);
    const table = within(await screen.findByRole("table"));
    await userEvent.click(table.getByRole("button", { name: "Șterge" }));

    await waitFor(() => expect(methods).toContain("DELETE"));
  });
});
