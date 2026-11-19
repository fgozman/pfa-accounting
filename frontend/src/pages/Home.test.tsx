import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HomePage } from "./Home";

const workspace = { id: "ws-1", name: "PFA Demo", cui: "90000001", role: "DEFAULT" as const };

const obligations = {
  items: [
    {
      id: "ob-2",
      form_code: "D300",
      form_name_ro: "Decontul de TVA",
      period_start: "2025-10-01",
      period_end: "2025-10-31",
      declaration_id: null,
      applicability: "required",
      applicability_reason: "Înregistrare normală în scopuri de TVA.",
      filing_due_date: "2026-10-25",
      payment_due_date: "2026-10-25",
      deadline_provenance: "D300 v1",
      days_remaining: 39,
      missing_inputs: [],
      resolution_status: "open",
      unsupported_reason: "Canal neactivat",
      capabilities: { generate: false, submit: false, unavailable_reason: "Canal neactivat" },
    },
    {
      id: "ob-1",
      form_code: "D212",
      form_name_ro: "Declarația unică",
      period_start: "2025-01-01",
      period_end: "2025-12-31",
      declaration_id: null,
      applicability: "required",
      applicability_reason: "Există activitate în anul de venit.",
      filing_due_date: "2026-05-25",
      payment_due_date: "2026-05-25",
      deadline_provenance: "D212 v1",
      days_remaining: -114,
      missing_inputs: [],
      resolution_status: "open",
      unsupported_reason: "Schema oficială nu este disponibilă",
      capabilities: { generate: false, submit: false, unavailable_reason: "Schema nedisponibilă" },
    },
    {
      id: "ob-3",
      form_code: "D394",
      form_name_ro: "Declarația informativă",
      period_start: "2025-10-01",
      period_end: "2025-10-31",
      declaration_id: null,
      applicability: "needs_information",
      applicability_reason: "Clasificarea contrapărților lipsește.",
      filing_due_date: null,
      payment_due_date: null,
      deadline_provenance: "Termen de verificat",
      days_remaining: null,
      missing_inputs: ["counterparty_classification"],
      resolution_status: "open",
      unsupported_reason: null,
      capabilities: { generate: true, submit: false, unavailable_reason: null },
    },
    {
      id: "ob-4",
      form_code: "EFACTURA",
      form_name_ro: "RO e-Factura",
      period_start: "2025-10-01",
      period_end: "2025-10-01",
      declaration_id: null,
      triggering_evidence: ["doc-efactura-1"],
      applicability: "required",
      applicability_reason: "Factură emisă: transmitere prin RO e-Factura.",
      filing_due_date: "2026-12-10",
      payment_due_date: null,
      deadline_provenance: "EFACTURA v1",
      days_remaining: 10,
      missing_inputs: [],
      resolution_status: "open",
      unsupported_reason: null,
      capabilities: { generate: false, submit: true, unavailable_reason: null },
    },
  ],
  unassessed_forms: [
    { form_code: "D112", name_ro: "Declarația de salarizare", reason: "Modul de salarizare neimplementat" },
  ],
};

const notifications = {
  items: [
    {
      id: "n-1",
      obligation_id: "ob-1",
      stage: "overdue",
      title_ro: "D212 — restanță",
      body_ro:
        "Declarația unică (D212), perioada 01.01.2025–31.12.2025 — depunere până la 25.05.2026 inclusiv — restanță de 114 zile.",
      due_on: "2026-05-25",
      days_remaining: -114,
      overdue: true,
      read: false,
      next_step_kind: "prepare_declaration",
    },
    {
      id: "n-2",
      obligation_id: "ob-3",
      stage: "detected",
      title_ro: "D394 — termen de verificat",
      body_ro: "Informații lipsă: partenerul tranzacției (client sau furnizor).",
      due_on: null,
      days_remaining: null,
      overdue: false,
      read: false,
      next_step_kind: "resolve_missing_information",
    },
    {
      id: "n-3",
      obligation_id: "ob-4",
      stage: "reminder",
      title_ro: "EFACTURA — termen apropiat",
      body_ro: "Factura emisă trebuie transmisă prin RO e-Factura.",
      due_on: "2026-12-10",
      days_remaining: 10,
      overdue: false,
      read: false,
      next_step_kind: "transmit_invoice",
    },
  ],
};

const dashboard = {
  income_year: 2026,
  issued_revenue: "10000.000000",
  collected_revenue: "4000.000000",
  open_obligations: [],
};

describe("HomePage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const body = url.includes("/documents/doc-efactura-1")
          ? { id: "doc-efactura-1", series: "SV", number: "42", issue_date: "2025-10-01" }
          : url.includes("/obligations")
          ? obligations
          : url.includes("/notifications")
            ? notifications
            : dashboard;
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });

  it("identifies the invoice to send to SPV in the reminder and deadlines", async () => {
    render(<HomePage language="ro" workspace={workspace} navigate={() => {}} />);
    await waitFor(() => expect(screen.getAllByText("Factura SV / 42 · emisă la 01.10.2025")).toHaveLength(2));
  });

  it("orders overdue items first and shows the absolute date plus days remaining", async () => {
    render(<HomePage language="ro" workspace={workspace} navigate={() => {}} />);
    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    // header + three obligations; the overdue D212 comes before the upcoming D300
    expect(within(rows[1]!).getAllByText(/D212/).length).toBeGreaterThan(0);
    expect(within(rows[1]!).getByText("25 mai 2026")).toBeInTheDocument();
    expect(within(rows[1]!).getByText(/114 zile de restanță/)).toBeInTheDocument();
  });

  it("identifies a monthly decont by its period, not by its due date", async () => {
    render(<HomePage language="ro" workspace={workspace} navigate={() => {}} />);
    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    const d300 = rows.find((row) => within(row).queryAllByText(/D300/).length > 0)!;
    // October 2025 is what this decont declares; it is merely due on 25 Oct 2026
    expect(within(d300).getAllByRole("cell")[0]).toHaveTextContent("octombrie 2025");
    // the deadline rule stays visible, it no longer stands in for the period
    expect(within(d300).getByText("D300 v1")).toBeInTheDocument();
    // a whole-year form reads as the year
    expect(within(table).getAllByText("anul 2025").length).toBeGreaterThan(0);
  });

  it("shows status as text, not only as colour", async () => {
    render(<HomePage language="ro" workspace={workspace} navigate={() => {}} />);
    expect(await screen.findAllByText("obligatorie")).not.toHaveLength(0);
    expect(await screen.findByText("necesită informații")).toBeInTheDocument();
  });

  it("never invents a deadline: unknown dates read 'Termen de verificat'", async () => {
    render(<HomePage language="ro" workspace={workspace} navigate={() => {}} />);
    expect((await screen.findAllByText("Termen de verificat")).length).toBeGreaterThan(0);
  });

  it("lists missing information by name and an explicit unsupported reason", async () => {
    render(<HomePage language="ro" workspace={workspace} navigate={() => {}} />);
    // the missing input is named, never shown as a storage key
    expect((await screen.findAllByText(/Partenerul tranzacției/)).length).toBeGreaterThan(0);
    expect(screen.queryByText(/counterparty_classification/)).toBeNull();
    expect((await screen.findAllByText(/Canal neactivat/)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/Schema/)).length).toBeGreaterThan(0);
  });

  it("keeps forms without automatic detection visible", async () => {
    render(<HomePage language="ro" workspace={workspace} navigate={() => {}} />);
    expect(await screen.findByText(/Modul de salarizare neimplementat/)).toBeInTheDocument();
  });

  it("labels estimated taxes as internal, not as an ANAF balance", async () => {
    render(<HomePage language="ro" workspace={workspace} navigate={() => {}} />);
    expect(await screen.findByText(/Nu sunt solduri confirmate de ANAF/)).toBeInTheDocument();
    expect(await screen.findByText("10.000,00 lei")).toBeInTheDocument();
    expect(await screen.findByText("4.000,00 lei")).toBeInTheDocument();
  });

  it("prepares the declaration before opening the review page", async () => {
    const prepared: { body: Record<string, unknown> }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "POST" && url.endsWith("/declarations")) {
          prepared.push({ body: JSON.parse(String(init.body)) as Record<string, unknown> });
          return new Response(JSON.stringify({ id: "decl-1" }), {
            status: 201,
            headers: { "Content-Type": "application/json" },
          });
        }
        const body = url.includes("/obligations")
          ? obligations
          : url.includes("/notifications")
            ? notifications
            : dashboard;
        return new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    const navigate = vi.fn();
    render(<HomePage language="ro" workspace={workspace} navigate={navigate} />);

    const button = (await screen.findAllByRole("button", { name: "Verifică și generează" }))[0]!;
    await userEvent.click(button);

    await waitFor(() => expect(prepared).toHaveLength(1));
    expect(prepared[0]!.body).toMatchObject({ form_code: "D212", income_year: 2025 });
    expect(navigate).toHaveBeenCalledWith("/declaratii?declaration=decl-1");
  });

  function stubTransmit(response: unknown) {
    const posts: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "POST") posts.push(url);
        const body = url.includes("/obligations")
          ? obligations
          : url.includes("/notifications")
            ? notifications
            : url.includes("/efactura/transmit/")
              ? response
              : dashboard;
        return new Response(JSON.stringify(body), {
          status: init?.method === "POST" ? 202 : 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    return posts;
  }

  it("transmits the invoice for an e-Factura obligation instead of preparing a declaration", async () => {
    const posts = stubTransmit({
      simulated: false,
      note: "Transmiterea prin RO e-Factura a fost înregistrată. Acceptarea este confirmată doar de recipisa ANAF.",
    });

    render(<HomePage language="ro" workspace={workspace} navigate={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Transmite prin e-Factura" }));

    await waitFor(() =>
      expect(posts.some((url) => url.includes("/efactura/transmit/"))).toBe(true),
    );
    expect(await screen.findByText(/Transmiterea prin RO e-Factura/)).toBeInTheDocument();
  });

  it("never claims a filing that did not happen", async () => {
    // the development driver is a fake ANAF service: the message must say so
    stubTransmit({
      simulated: true,
      note: "Transmitere SIMULATĂ: serviciul ANAF nu este configurat, deci fișierul nu a fost trimis nicăieri.",
    });

    render(<HomePage language="ro" workspace={workspace} navigate={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Transmite prin e-Factura" }));

    expect(await screen.findByText(/SIMULATĂ/)).toBeInTheDocument();
    expect(screen.queryByText(/a fost înregistrată/)).toBeNull();
  });

  it("offers one next-step action per notification", async () => {
    const navigate = vi.fn();
    render(<HomePage language="ro" workspace={workspace} navigate={navigate} />);
    const complete = await screen.findByRole("button", { name: "Completează informațiile" });
    await userEvent.click(complete);
    // the facts are answered in Setări, where the form lives
    expect(navigate).toHaveBeenCalledWith("/setari");
  });
});
