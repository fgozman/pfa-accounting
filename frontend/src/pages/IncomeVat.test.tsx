import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { IncomePage } from "./Income";

const workspace = { id: "ws-1", name: "PFA Demo", cui: "90000001", role: "DEFAULT" as const };

const savedDocument = {
  id: "d-1",
  series: "F",
  number: "1",
  status: "finalized",
  kind: "sales_invoice",
  issue_date: "2026-01-01",
  currency: "RON",
  total_amount: "1210.000000",
  total_vat: "210.000000",
  vat_treatment: "standard",
};

describe("VAT on income documents", () => {
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

  it("sends the VAT treatment and rate entered on the document", async () => {
    const posts: { url: string; body: Record<string, unknown> }[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === "POST" && url.endsWith("/documents")) {
        posts.push({ url, body: JSON.parse(String(init.body)) as Record<string, unknown> });
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
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<IncomePage language="ro" workspace={workspace} />);
    await screen.findByText("Facturi emise");

    // the series is optional and must not start with an invented prefix
    expect(screen.getByLabelText("Serie (opțională)")).toHaveValue("");
    await userEvent.type(screen.getByLabelText("Serie (opțională)"), "ABC");
    await userEvent.type(screen.getByLabelText("Număr"), "2026-0042");
    const rate = screen.getByLabelText("Cotă TVA (%)");
    await userEvent.type(rate, "21");
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.body.series).toBe("ABC");
    expect(posts[0]!.body.number).toBe("2026-0042");
    expect(posts[0]!.body.vat_treatment).toBe("standard");
    expect(posts[0]!.body.lines).toEqual([
      expect.objectContaining({ vat_rate: "21", vat_treatment: "standard" }),
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Adaugă alt document" }));
    expect(screen.getByLabelText("Serie (opțională)")).toHaveValue("");
  });

  it("saves a number without inventing a series and accepts a non-EU client", async () => {
    const posts: Record<string, unknown>[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST" && String(input).endsWith("/documents")) {
        posts.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return new Response(JSON.stringify({ ...savedDocument, series: null }), {
          status: 201, headers: { "Content-Type": "application/json" },
        });
      }
      if (String(input).includes("/finalize")) {
        return new Response(JSON.stringify({ ...savedDocument, series: null }), {
          status: 200, headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ items: [] }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    }));

    render(<IncomePage language="ro" workspace={workspace} />);
    await screen.findByText("Facturi emise");
    expect(screen.getByLabelText("Descriere")).toHaveValue("");
    expect(screen.getByLabelText("Sumă (RON, fără TVA)")).toHaveValue("");
    await userEvent.type(screen.getByLabelText("Număr"), "2026-7");
    await userEvent.selectOptions(screen.getByLabelText("Țara clientului"), "JP");
    await userEvent.type(screen.getByLabelText("Cotă TVA (%)"), "21");
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.series).toBeNull();
    expect(posts[0]!.supplier_country).toBe("JP");
    expect(screen.getByRole("status")).toHaveTextContent("Factura 1 a fost emisă");
    expect(screen.getByRole("status")).not.toHaveTextContent("null");
  });

  it("does not compute a rate for an exempt supply", async () => {
    render(<IncomePage language="ro" workspace={workspace} />);
    await screen.findByText("Facturi emise");

    expect(screen.getByLabelText("Cotă TVA (%)")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Tratament TVA"), "exempt");
    expect(screen.queryByLabelText("Cotă TVA (%)")).toBeNull();
  });

  it("records the client, so the partner can be classified in D394", async () => {
    const posts: { body: Record<string, unknown> }[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<IncomePage language="ro" workspace={workspace} />);
    await screen.findByText("Facturi emise");

    await userEvent.type(screen.getByLabelText("Client"), "ACME SRL");
    // the EU fields appear once a partner is named
    await userEvent.selectOptions(screen.getByLabelText("Țara clientului"), "DE");
    await userEvent.type(screen.getByLabelText(/Cod fiscal client/), "DE123456789");
    await userEvent.type(screen.getByLabelText("Cotă TVA (%)"), "21");
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.body.supplier_name_raw).toBe("ACME SRL");
    expect(posts[0]!.body.supplier_country).toBe("DE");
    expect(posts[0]!.body.supplier_vat_id).toBe("DE123456789");
  });

  it("lets a Romanian client be selected and its CUI recorded", async () => {
    const posts: { body: Record<string, unknown> }[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<IncomePage language="ro" workspace={workspace} />);
    await screen.findByText("Facturi emise");

    // Romania must be selectable: a domestic client is the normal case
    const country = screen.getByLabelText("Țara clientului");
    expect(within(country).getByRole("option", { name: /România \(RO\)/ })).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Client"), "Client Român SRL");
    await userEvent.selectOptions(country, "RO");
    await userEvent.type(screen.getByLabelText(/Cod fiscal client/), "RO12345678");
    await userEvent.type(screen.getByLabelText("Cotă TVA (%)"), "21");
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.body.supplier_country).toBe("RO");
    expect(posts[0]!.body.supplier_vat_id).toBe("RO12345678");
  });

  it("records a consumer sale, so no partner is demanded for it", async () => {
    const posts: { body: Record<string, unknown> }[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<IncomePage language="ro" workspace={workspace} />);
    await screen.findByText("Facturi emise");

    await userEvent.selectOptions(screen.getByLabelText("Tip client"), "consumer");
    // nothing to fill in for a final consumer
    expect(screen.queryByLabelText("Client")).toBeNull();
    await userEvent.type(screen.getByLabelText("Cotă TVA (%)"), "21");
    await userEvent.click(screen.getByRole("button", { name: "Verifică datele" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvează" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.body.partner_kind).toBe("consumer");
    expect(posts[0]!.body.supplier_name_raw).toBeNull();
  });
});
