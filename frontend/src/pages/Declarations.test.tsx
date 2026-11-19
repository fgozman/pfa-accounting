import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeclarationsPage } from "./Declarations";

const workspace = { id: "ws-1", name: "PFA Demo", cui: "90000001", role: "DEFAULT" as const };

const declaration = {
  id: "d-1",
  form_code: "D300",
  form_name_ro: "Decontul de TVA",
  income_year: 2026,
  period_start: "2026-07-01",
  period_end: "2026-09-30",
  status: "draft",
  current_version: 0,
  rule_pack_code: "RO-VAT-2026.1",
  rule_pack_version: 1,
  estimate_only: true,
  approved_at: null,
  accepted_at: null,
  submission_available: false,
  submission_unavailable_reason: "Canal neactivat",
  calculated_values: {
    collected_total: "210.000000",
    deductible_total: "50.000000",
    balance_payable: "160.000000",
    nr_pag: 1,
    some_unknown_key: "12.000000",
  },
};

describe("DeclarationsPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    window.history.replaceState({}, "", "/declaratii");
  });

  it("opens the review panel when 'Deschide' is clicked", async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ items: [declaration] }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );

    render(<DeclarationsPage language="ro" workspace={workspace} />);

    await userEvent.click(await screen.findByRole("button", { name: "Deschide" }));

    expect(await screen.findByRole("heading", { level: 2, name: /2026/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generează" })).toBeInTheDocument();
    // the clicked row is marked and the panel is brought into view
    expect(screen.getByRole("button", { name: "Deschisă" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("shows calculated values with readable labels, not raw keys", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ items: [declaration] }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );

    render(<DeclarationsPage language="ro" workspace={workspace} />);
    await userEvent.click(await screen.findByRole("button", { name: "Deschide" }));

    expect(await screen.findByText("TVA de plată")).toBeInTheDocument();
    expect(screen.getByText("TVA colectată")).toBeInTheDocument();
    expect(screen.getByText("TVA deductibilă")).toBeInTheDocument();
    expect(screen.getByText("160,00 lei")).toBeInTheDocument();
    // counts are shown as integers, and unknown keys fall back to a humanised label
    expect(screen.getByText("Număr pagini")).toBeInTheDocument();
    expect(screen.getByText("Some unknown key")).toBeInTheDocument();
    expect(screen.queryByText("balance_payable")).toBeNull();
  });

  it("lists the blocking inputs when validation is refused", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/validate")) {
          return new Response(
            JSON.stringify({
              type: "about:blank",
              title:
                "Validarea este blocată: există informații obligatorii necunoscute sau " +
                "clasificări nedeterminate.",
              status: 409,
              code: "missing_required_information",
              correlation_id: "abc",
              details: { missing_inputs: ["prenume_declar", "cui", "adresa"] },
            }),
            { status: 409, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(JSON.stringify({ items: [declaration] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(<DeclarationsPage language="ro" workspace={workspace} />);
    await userEvent.click(await screen.findByRole("button", { name: "Deschide" }));
    await userEvent.click(await screen.findByRole("button", { name: "Validează" }));

    expect(await screen.findByText("Prenumele declarantului")).toBeInTheDocument();
    expect(screen.getByText("CUI / CIF valid")).toBeInTheDocument();
    expect(screen.getByText("Adresa domiciliului fiscal")).toBeInTheDocument();
  });

  it("shows why there is nothing to explain yet, instead of an empty panel", async () => {
    // a declaration that was never calculated has no trace at all
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ items: [{ ...declaration, trace: null }] }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );

    render(<DeclarationsPage language="ro" workspace={workspace} />);
    await userEvent.click(await screen.findByRole("button", { name: "Deschide" }));

    // the panel must say so, never just toggle an empty list
    expect(await screen.findByText(/Nu există explicații încă/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Arată explicațiile" })).toBeNull();
  });

  it("deletes the declaration after confirmation", async () => {
    const methods: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        methods.push(init?.method ?? "GET");
        if (init?.method === "DELETE") {
          return new Response(JSON.stringify({ status: "deleted" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({ items: [declaration] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    vi.stubGlobal("confirm", vi.fn(() => true));

    render(<DeclarationsPage language="ro" workspace={workspace} />);
    await userEvent.click(await screen.findByRole("button", { name: "Deschide" }));
    await userEvent.click(screen.getByRole("button", { name: "Șterge declarația" }));

    await waitFor(() => expect(methods).toContain("DELETE"));
    expect(await screen.findByText("Declarația a fost ștearsă.")).toBeInTheDocument();
  });
});
