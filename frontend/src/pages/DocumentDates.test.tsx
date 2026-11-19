import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { todayIso } from "../format";
import { ExpensesPage } from "./Expenses";
import { IncomePage } from "./Income";

const workspace = { id: "ws-1", name: "PFA Demo", cui: "90000001", role: "DEFAULT" as const };

/**
 * Document dates must default to the day the document is entered, never to a
 * fixed research-baseline date.
 */
describe("document date defaults", () => {
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

  it("defaults 'Data emiterii' to the current date", async () => {
    render(<IncomePage language="ro" workspace={workspace} />);
    await screen.findByText("Facturi emise");
    expect(screen.getByLabelText("Data emiterii")).toHaveValue(todayIso());
  });

  it("defaults 'Data documentului' to the current date", async () => {
    render(<ExpensesPage language="ro" workspace={workspace} />);
    await screen.findByText("Cheltuieli înregistrate");
    expect(screen.getByLabelText("Data documentului")).toHaveValue(todayIso());
  });
});
