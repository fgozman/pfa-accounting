import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FiscalIdentityForm } from "./FiscalIdentityForm";

describe("FiscalIdentityForm", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("falls back to the workspace name and CUI when no profile row exists yet", () => {
    render(
      <FiscalIdentityForm
        workspaceId="ws-1"
        defaultName="PFA Demo"
        defaultCui="90000001"
        profile={null}
        onSaved={() => {}}
        onError={() => {}}
      />,
    );

    expect(screen.getByLabelText("Denumire PFA")).toHaveValue("PFA Demo");
    expect(screen.getByLabelText("CUI / CIF")).toHaveValue("90000001");
  });

  it("saves the identification data", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "updated" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onSaved = vi.fn();

    render(
      <FiscalIdentityForm
        workspaceId="ws-1"
        defaultName="PFA Demo"
        defaultCui="90000001"
        profile={null}
        onSaved={onSaved}
        onError={() => {}}
      />,
    );

    await userEvent.clear(screen.getByLabelText("CUI / CIF"));
    await userEvent.type(screen.getByLabelText("CUI / CIF"), "90000002");
    await userEvent.type(screen.getByLabelText("Județ"), "Cluj");
    await userEvent.click(screen.getByRole("button", { name: "Salvează datele de identificare" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toContain("/api/v1/workspaces/ws-1/fiscal-profile");
    expect(JSON.parse(String(init.body))).toMatchObject({
      legal_name: "PFA Demo",
      cui: "90000002",
      county: "Cluj",
    });
  });
});
