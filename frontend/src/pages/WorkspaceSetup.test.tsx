import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkspaceSetupPage } from "./WorkspaceSetup";

/**
 * Onboarding empty state.
 *
 * A freshly registered account owns no PFA. Without this page the dashboard had
 * nothing to render and showed an endless "Se încarcă…", which is an empty state
 * that never resolves — the opposite of the required behaviour.
 */
describe("WorkspaceSetupPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("offers a single next step and explains what happens next", () => {
    render(<WorkspaceSetupPage onCreated={() => {}} />);
    expect(screen.getByRole("heading", { name: "Configurați PFA" })).toBeInTheDocument();
    expect(screen.getByLabelText("Denumirea PFA")).toBeRequired();
    expect(screen.getByRole("button", { name: "Creează spațiul de lucru" })).toBeEnabled();
    // the consequences of sharing are stated up front
    expect(screen.getByText(/Ambele roluri văd toate documentele/)).toBeInTheDocument();
  });

  it("creates the workspace and hands the new id back", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "ws-new", name: "PFA Nou", cui: "90000123" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onCreated = vi.fn();

    render(<WorkspaceSetupPage onCreated={onCreated} />);
    await userEvent.type(screen.getByLabelText("Denumirea PFA"), "PFA Nou");
    await userEvent.type(screen.getByLabelText("CUI / CIF"), "90000123");
    await userEvent.click(screen.getByRole("button", { name: "Creează spațiul de lucru" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("ws-new"));
    const [, init] = fetchMock.mock.calls[0]!;
    expect(JSON.parse(String(init.body))).toEqual({ name: "PFA Nou", cui: "90000123" });
  });

  it("keeps the typed data and shows the error beside the field on failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Datele trimise nu sunt valide.",
            status: 422,
            code: "validation_failed",
            correlation_id: "abc",
            field_errors: { name: "min_length" },
          }),
          { status: 422, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    render(<WorkspaceSetupPage onCreated={() => {}} />);
    await userEvent.type(screen.getByLabelText("Denumirea PFA"), "PF");
    await userEvent.click(screen.getByRole("button", { name: "Creează spațiul de lucru" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/nu sunt valide/);
    expect(screen.getByText(/cel puțin 2 caractere/)).toBeInTheDocument();
    // input is not lost
    expect(screen.getByLabelText("Denumirea PFA")).toHaveValue("PF");
  });
});
