import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { extractToken } from "../api/devMailbox";
import { VerifyEmailPage } from "./VerifyEmail";
import { PasswordResetPage } from "./PasswordReset";

describe("activation and reset links", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState({}, "", "/verify-email");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("extracts the token from a pasted link or a bare token", () => {
    expect(extractToken("http://127.0.0.1:5173/verify-email?token=abc123DEF456ghi789jkl")).toBe(
      "abc123DEF456ghi789jkl",
    );
    expect(extractToken("  abc123DEF456ghi789jkl  ")).toBe("abc123DEF456ghi789jkl");
    expect(extractToken("nu este un link")).toBeNull();
    expect(extractToken("")).toBeNull();
  });

  it("consumes the token and confirms activation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "verified" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const navigate = vi.fn();

    render(<VerifyEmailPage navigate={navigate} token="token-from-link" />);

    expect(await screen.findByText(/Adresa de email a fost confirmată/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toContain("/auth/verify-email");
    expect(JSON.parse(String(init.body))).toEqual({ token: "token-from-link" });

    await userEvent.click(screen.getByRole("button", { name: "Autentificare" }));
    expect(navigate).toHaveBeenCalledWith("/");
  });

  it("explains a missing, expired or replayed token instead of failing silently", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Linkul de verificare a expirat.",
            status: 422,
            code: "validation_failed",
            correlation_id: "abc",
          }),
          { status: 422, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    render(<VerifyEmailPage navigate={() => {}} token="stale-token" />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/a expirat/);
    expect(screen.getByText(/valabile o singură dată/)).toBeInTheDocument();
  });

  it("treats /verify-email?resend=1 as a request-a-link screen, not a broken link", async () => {
    window.history.replaceState({}, "", "/verify-email?resend=1&email=prefill@example.test");
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<VerifyEmailPage navigate={() => {}} />);

    expect(await screen.findByText(/Introduceți adresa de email/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByLabelText("Adresă de email")).toHaveValue("prefill@example.test");
    expect(screen.getByRole("button", { name: "Trimite un link nou" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("asks for the whole link when the token is missing", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(<VerifyEmailPage navigate={() => {}} token={undefined} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/nu conține un token/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("removes the one-use token from the address bar after the exchange", async () => {
    window.history.replaceState({}, "", "/verify-email?token=oneUseToken1234567890&resend=1");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ status: "verified" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    render(<VerifyEmailPage navigate={() => {}} />);

    expect(await screen.findByText(/Adresa de email a fost confirmată/)).toBeInTheDocument();
    expect(window.location.search).not.toContain("token=");
    expect(window.location.search).toContain("resend=1");
  });

  it("offers a new activation link when the old one expired or was lost", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/auth/verify-email/resend")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              status: "accepted",
              message:
                "Dacă adresa corespunde unui cont neactivat, veți primi un link nou de verificare.",
            }),
            { status: 202, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      if (url.includes("/api/v1/dev/mailbox")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              items: [
                {
                  to: "cineva@example.test",
                  link: "http://127.0.0.1:5173/verify-email?token=brandNewToken123456789",
                  token: "brandNewToken123456789",
                },
              ],
              sink_directory: ".run/emails",
              note: "",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      return Promise.resolve(
        new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<VerifyEmailPage navigate={() => {}} token={undefined} />);
    // a link without a token is explained, and the resend form is offered
    expect(await screen.findByRole("alert")).toHaveTextContent(/nu conține un token/);

    await userEvent.type(screen.getByLabelText("Adresă de email"), "cineva@example.test");
    await userEvent.click(screen.getByRole("button", { name: "Trimite un link nou" }));

    // the answer is generic, and development surfaces the captured link
    expect(await screen.findByText(/veți primi un link nou de verificare/)).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Activează contul acum" }),
    ).toBeInTheDocument();

    const resendCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/auth/verify-email/resend"),
    );
    expect(resendCall).toBeTruthy();
    expect(JSON.parse(String(resendCall![1].body))).toEqual({ email: "cineva@example.test" });
  });

  it("gives the same generic answer for a password reset request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ status: "accepted", message: "Dacă adresa există, veți primi un link." }),
          { status: 202, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    render(<PasswordResetPage navigate={() => {}} />);
    await userEvent.type(screen.getByLabelText("Adresă de email"), "cineva@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Trimite linkul de resetare" }));

    expect(await screen.findByRole("status")).toHaveTextContent(/Dacă adresa există/);
  });
});
