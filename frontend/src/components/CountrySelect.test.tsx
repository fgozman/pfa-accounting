import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CountrySelect } from "./CountrySelect";
import { PARTNER_COUNTRIES } from "../labels";

describe("partner country selector", () => {
  it("contains all 249 ISO countries plus the D390 exceptions without duplicates", () => {
    expect(PARTNER_COUNTRIES).toHaveLength(251);
    expect(new Set(PARTNER_COUNTRIES.map(({ code }) => code)).size).toBe(251);
    for (const code of ["RO", "GB", "US", "JP", "BR", "AU", "GR", "XI", "EL"]) {
      expect(PARTNER_COUNTRIES.some((country) => country.code === code)).toBe(true);
    }
  });

  it("lists countries directly in the dropdown and updates the selected code", async () => {
    const change = vi.fn();
    const { rerender } = render(
      <CountrySelect id="country" label="Țara clientului" value="RO" onChange={change} />,
    );
    const country = screen.getByLabelText("Țara clientului");
    expect(screen.queryByLabelText("Caută țara clientului")).toBeNull();
    expect(within(country).getByRole("option", { name: "Japonia (JP)" })).toBeInTheDocument();
    expect(within(country).getByRole("option", { name: "Germania (DE)" })).toBeInTheDocument();
    await userEvent.selectOptions(country, "JP");
    expect(change).toHaveBeenCalledWith("JP");
    rerender(<CountrySelect id="country" label="Țara clientului" value="JP" onChange={change} />);
    expect(country).toHaveValue("JP");
  });

  it("keeps an older stored code editable even if it is outside the standard list", () => {
    render(<CountrySelect id="country" label="Țara clientului" value="XK" onChange={vi.fn()} />);
    expect(screen.getByLabelText("Țara clientului")).toHaveValue("XK");
  });
});
