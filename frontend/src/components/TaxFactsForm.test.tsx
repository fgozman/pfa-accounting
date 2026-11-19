import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaxFactsForm } from "./TaxFactsForm";

const catalogue = {
  income_year: 2026,
  supported_years: [2025, 2026, 2027],
  items: [
    {
      fact_key: "regime",
      income_year: 2026,
      value_kind: "text",
      value: null,
      answered: false,
      label_ro: "Regimul fiscal al anului",
      group: "Regim fiscal",
      unit_ro: null,
      help_ro: null,
      choices: [
        { value: "real", label: "Sistem real" },
        { value: "norm", label: "Normă de venit" },
      ],
      required_for: ["D212"],
      applies_when: [],
    },
    {
      fact_key: "norm_amount",
      income_year: 2026,
      value_kind: "decimal",
      value: null,
      answered: false,
      label_ro: "Venitul normat anual",
      group: "Regim fiscal",
      unit_ro: "lei",
      help_ro: null,
      choices: [],
      required_for: [],
      applies_when: [{ fact_key: "regime", values: ["norm"] }],
    },
    {
      fact_key: "cas_election",
      income_year: 2026,
      value_kind: "boolean",
      value: null,
      answered: false,
      label_ro: "Opțiunea CAS (pensie)",
      group: "Contribuții",
      unit_ro: null,
      help_ro: "Ați optat pentru plata contribuției?",
      choices: [],
      required_for: ["D212"],
      applies_when: [],
    },
    {
      fact_key: "prior_losses",
      income_year: 2026,
      value_kind: "decimal",
      value: null,
      answered: false,
      label_ro: "Pierderi reportate",
      group: "Alte situații",
      unit_ro: "lei",
      help_ro: null,
      choices: [],
      required_for: [],
      applies_when: [],
    },
    {
      fact_key: "pension_status",
      income_year: 2026,
      value_kind: "text",
      value: null,
      answered: false,
      label_ro: "Statutul de pensionar",
      group: "Alte situații",
      unit_ro: null,
      help_ro: null,
      choices: [
        { value: "not_pensioner", label: "Nu sunt pensionar" },
        { value: "pensioner", label: "Sunt pensionar" },
      ],
      required_for: ["D212"],
      applies_when: [],
    },
  ],
};

function stub(response: unknown, status = 200) {
  // a Response body can only be read once, so each call needs its own
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(response), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
    ),
  );
}

describe("TaxFactsForm", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders a control per fact from the backend catalogue", async () => {
    stub(catalogue);
    render(<TaxFactsForm workspaceId="ws-1" onSaved={() => {}} onError={() => {}} />);

    // the labels come from the API, so the form cannot drift from the declarations
    expect(await screen.findByLabelText("Opțiunea CAS (pensie)")).toBeInTheDocument();
    expect(screen.getByLabelText("Pierderi reportate (lei)")).toBeInTheDocument();
    expect(screen.getByLabelText("Statutul de pensionar")).toBeInTheDocument();
    // an unanswered fact that blocks a declaration says so; the optional one does not
    expect(screen.getAllByText(/Fără răspuns/).length).toBe(3);
    expect(screen.getAllByText(/blochează D212/).length).toBe(3);
  });

  it("sends typed answers and keeps unanswered facts unknown", async () => {
    stub(catalogue);
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    const onSaved = vi.fn();
    const errors: unknown[] = [];
    render(
      <TaxFactsForm workspaceId="ws-1" onSaved={onSaved} onError={(e) => errors.push(e)} />,
    );

    await userEvent.selectOptions(await screen.findByLabelText("Opțiunea CAS (pensie)"), "false");
    await userEvent.type(screen.getByLabelText("Pierderi reportate (lei)"), "1200");
    await userEvent.selectOptions(
      screen.getByLabelText("Statutul de pensionar"),
      "not_pensioner",
    );
    await userEvent.click(screen.getByRole("button", { name: "Salvează informațiile fiscale" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(errors).toEqual([]);
    const [url, init] = fetchMock.mock.calls.at(-1)!;
    expect(String(url)).toContain("/api/v1/workspaces/ws-1/tax-facts");
    const body = JSON.parse(String(init.body));
    expect(body.income_year).toBe(2026);
    expect(body.answers).toEqual(
      expect.arrayContaining([
        { fact_key: "cas_election", value: false },
        { fact_key: "prior_losses", value: "1200" },
        { fact_key: "pension_status", value: "not_pensioner" },
      ]),
    );
    // a hidden norm field is not answered, so it is sent as unknown, not as a number
    expect(body.answers).toEqual(
      expect.arrayContaining([{ fact_key: "norm_amount", value: null }]),
    );
  });

  it("shows a norm-only field only while the regime is the norm", async () => {
    stub(catalogue);
    render(<TaxFactsForm workspaceId="ws-1" onSaved={() => {}} onError={() => {}} />);

    // the regime is not answered yet, so a norm field cannot apply
    expect(await screen.findByLabelText("Regimul fiscal al anului")).toBeInTheDocument();
    expect(screen.queryByLabelText("Venitul normat anual (lei)")).toBeNull();

    await userEvent.selectOptions(screen.getByLabelText("Regimul fiscal al anului"), "norm");
    expect(screen.getByLabelText("Venitul normat anual (lei)")).toBeInTheDocument();

    // and it disappears again, unanswered, when the regime changes back
    await userEvent.selectOptions(screen.getByLabelText("Regimul fiscal al anului"), "real");
    expect(screen.queryByLabelText("Venitul normat anual (lei)")).toBeNull();
  });

  it("reports what is still unanswered after saving", async () => {
    const answered = {
      ...catalogue,
      items: catalogue.items.map((item) =>
        item.fact_key === "cas_election" ? { ...item, value: false, answered: true } : item,
      ),
    };
    stub(answered);
    const onSaved = vi.fn();
    render(<TaxFactsForm workspaceId="ws-1" onSaved={onSaved} onError={() => {}} />);

    await userEvent.click(
      await screen.findByRole("button", { name: "Salvează informațiile fiscale" }),
    );

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    // the optional fact is not reported as a blocker
    expect(String(onSaved.mock.calls[0]![0])).toContain("Statutul de pensionar");
    expect(String(onSaved.mock.calls[0]![0])).not.toContain("Pierderi reportate");
  });
});
