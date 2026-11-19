import { expect, test, type Page } from "@playwright/test";

/**
 * The sign-in journey, in a real browser.
 *
 * Three defects made this fail before, and each assertion below exists because a
 * status code could not detect it:
 *
 * 1. the SPA had no authorization origin configured, so "Autentificare" navigated
 *    to `<spa>/oauth/authorize` and the login form appeared again;
 * 2. `form-action 'self'` blocked the login form submission, because Chromium
 *    applies `form-action` to the redirect target too (the SPA callback on another
 *    origin);
 * 3. the dev server proxied the whole `/auth/` prefix, so the registered callback
 *    URI `/auth/callback` was answered by the API with a JSON 404 instead of the SPA.
 */

const API = process.env.E2E_API_ORIGIN ?? "http://127.0.0.1:8010";
const SPA = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5180";

async function registerVerifiedUser(page: Page, email: string, password: string) {
  const registered = await page.request.post(`${API}/auth/register`, {
    data: { email, password },
  });
  expect(registered.status()).toBe(202);

  // Development captures the activation message locally instead of sending it.
  // The worker delivers it asynchronously, so poll for *this* recipient rather
  // than assuming immediacy (or picking up somebody else's token).
  let token = "";
  for (let attempt = 0; attempt < 40 && !token; attempt += 1) {
    const inbox = await page.request.get(
      `${API}/api/v1/dev/mailbox?template=verification&email=${encodeURIComponent(email)}`,
    );
    const items = ((await inbox.json()).items ?? []) as { to: string; token: string }[];
    token = items.find((item) => item.to === email)?.token ?? "";
    if (!token) await new Promise((resolve) => setTimeout(resolve, 500));
  }
  expect(token, `no activation link captured for ${email}`).not.toBe("");
  const verified = await page.request.post(`${API}/auth/verify-email`, { data: { token } });
  expect(verified.ok()).toBeTruthy();
}

test.describe("sign in", () => {
  test("the login button reaches the authorization service and returns to the app", async ({
    page,
  }) => {
    const email = `signin-${Date.now()}@example.com`;
    const password = "Correct-Horse-Battery-9";
    await registerVerifiedUser(page, email, password);

    const consoleErrors: string[] = [];
    const cookieResponses: string[] = [];
    page.on("pageerror", (error) => consoleErrors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("response", (response) => {
      const setCookie = response.headers()["set-cookie"];
      const resourceType = response.request().resourceType();
      if (setCookie && (resourceType === "document" || /\/oauth\/|\/auth\//.test(response.url()))) {
        cookieResponses.push(`${response.url()} -> ${setCookie}`);
      }
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Autentificare" }).click();
    await page.waitForLoadState("networkidle");

    // 1. the click leaves the SPA origin and lands on the authorization service
    expect(page.url().startsWith(`${API}/oauth/authorize`)).toBeTruthy();
    expect(new URL(page.url()).origin).not.toBe(new URL(SPA).origin);
    await expect(page.getByLabel("Adresă de email")).toBeVisible();
    await expect(page.getByLabel("Parolă")).toBeVisible();

    // 2. credentials are submitted on the authorization origin and accepted
    await page.getByLabel("Adresă de email").fill(email);
    await page.getByLabel("Parolă").fill(password);
    await page.getByRole("button", { name: "Autentificare" }).click();

    // 3. the callback is served by the SPA (not the API) and completes the exchange
    await page.waitForURL("**/auth/callback**", { timeout: 20_000 });
    await page.waitForURL(`${SPA}/`, { timeout: 20_000 });

    // 4. a fresh account has no PFA yet: the onboarding empty state appears
    //    instead of an endless "Se încarcă…"
    await expect(page.getByRole("heading", { name: "Configurați PFA" })).toBeVisible();
    await page.getByLabel("Denumirea PFA").fill(`PFA E2E ${Date.now()}`);
    await page.getByLabel("CUI / CIF").fill("90000123");
    await page.getByRole("button", { name: "Creează spațiul de lucru" }).click();

    // 5. the dashboard is now usable and names the active PFA on every page
    await expect(page.getByRole("heading", { name: "Acasă" })).toBeVisible();
    expect(await page.locator("#workspace-switch").inputValue()).not.toBe("");
    expect(page.url()).not.toContain("code=");
    expect(page.url()).not.toContain("state=");
    expect(consoleErrors).toEqual([]);
    expect(cookieResponses).toEqual([]);
  });

  test("the callback path is served by the SPA, not proxied to the API", async ({ request }) => {
    const response = await request.get(`${SPA}/auth/callback?code=irrelevant&state=irrelevant`);
    expect(response.status()).toBe(200);
    expect(response.headers()["content-type"]).toContain("text/html");
    const body = await response.text();
    expect(body).toContain("<div id=\"root\">");
    // a JSON problem document here would mean the static host swallowed the path
    expect(body).not.toContain("docs.example.ro/problems");
  });

  test("a wrong password stays on the authorization origin and explains itself", async ({
    page,
  }) => {
    const email = `wrongpass-${Date.now()}@example.com`;
    await registerVerifiedUser(page, email, "Correct-Horse-Battery-9");

    await page.goto("/");
    await page.getByRole("button", { name: "Autentificare" }).click();
    await page.waitForLoadState("networkidle");
    await page.getByLabel("Adresă de email").fill(email);
    await page.getByLabel("Parolă").fill("definitely-not-the-password");
    await page.getByRole("button", { name: "Autentificare" }).click();
    await page.waitForLoadState("networkidle");

    // still on the authorization origin (canonical URL, reload-safe), never the
    // SPA callback, and the failure is explained in the page
    expect(new URL(page.url()).origin).toBe(new URL(API).origin);
    expect(page.url()).toContain("/oauth/authorize");
    expect(page.url()).not.toContain("code=");
    await expect(page.locator(".error")).toContainText(/Datele de autentificare/);
  });

  test("an unsolicited callback is rejected instead of signing in", async ({ page }) => {
    await page.goto(`${SPA}/auth/callback?code=forged&state=forged`);
    await expect(page.locator(".error")).toBeVisible();
    await expect(page.locator(".error")).toContainText(/nu a fost inițiată de aplicație/i);
    // still not authenticated
    await expect(page.getByRole("heading", { name: "Acasă" })).toHaveCount(0);
  });
});
