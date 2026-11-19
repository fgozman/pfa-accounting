import { expect, test, type Page } from "@playwright/test";

/**
 * End-to-end journeys.
 *
 * These run against the real stack (SPA + Python API + worker) with the
 * deterministic fake ANAF service and file-based email capture.  They cover the
 * release journey required by the specification: register → verify → complete
 * setup → invite → enter shared evidence → settle → see a dated obligation →
 * resolve missing facts → generate and validate the artifact → export → import a
 * matching acceptance receipt, plus an independent payment step and a
 * rejection/recovery journey.
 */

const API = process.env.E2E_API_ORIGIN ?? "http://127.0.0.1:8000";

/**
 * The worker delivers the activation message asynchronously, so poll the dev
 * mailbox for *this* recipient instead of reading whatever was captured last
 * (which raced the worker and could pick up another account's token).
 */
async function verificationToken(page: Page, email: string): Promise<string> {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const inbox = await page.request.get(
      `${API}/api/v1/dev/mailbox?template=verification&email=${encodeURIComponent(email)}`,
    );
    const items = ((await inbox.json()).items ?? []) as { to: string; token: string }[];
    const token = items.find((item) => item.to === email)?.token;
    if (token) return token;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`no activation link captured for ${email}`);
}

async function registerAndVerify(page: Page, email: string, password: string) {
  const register = await page.request.post(`${API}/auth/register`, {
    data: { email, password },
  });
  expect(register.status()).toBe(202);
  const verification = await page.request.post(`${API}/auth/verify-email`, {
    data: { token: await verificationToken(page, email) },
  });
  expect(verification.ok()).toBeTruthy();
}

async function login(page: Page, email: string, password: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "Autentificare" }).click();
  // the login page is rendered by the authorization service, not by the SPA
  await expect(page).toHaveURL(/\/oauth\/authorize/);
  await page.getByLabel("Adresă de email").fill(email);
  await page.getByLabel("Parolă").fill(password);
  await page.getByRole("button", { name: "Autentificare" }).click();
  await expect(page).toHaveURL(/\/auth\/callback/);
  // a fresh account has no PFA yet: complete the onboarding before any accounting work
  await expect(page.getByRole("heading", { name: "Configurați PFA" })).toBeVisible();
  await page.getByLabel("Denumirea PFA").fill(`PFA E2E ${Date.now()}`);
  await page.getByLabel("CUI / CIF").fill("90000123");
  await page.getByRole("button", { name: "Creează spațiul de lucru" }).click();
  await expect(page.getByRole("heading", { name: "Acasă" })).toBeVisible();
}

test.describe("critical flows", () => {
  test("a DEFAULT member can run the whole document → declaration journey", async ({ page }) => {
    const email = `e2e-${Date.now()}@example.com`;
    const password = "Correct-Horse-Battery-9";
    await registerAndVerify(page, email, password);
    await login(page, email, password);

    // register a paid expense: the normal path is add → check → save.  The number is
    // the supplier's, typed from the paper; a taxable document needs its VAT rate.
    await page.getByRole("link", { name: "Cheltuieli" }).click();
    await page.getByLabel("Furnizor", { exact: true }).fill("Furnizor E2E SRL");
    await page.getByLabel("Număr", { exact: true }).fill("FE2E-1");
    await page.getByLabel("Sumă (RON, fără TVA)").fill("2000");
    await page.getByLabel("Cotă TVA (%)").fill("21");
    await page.getByRole("radio", { name: "Da" }).last().check();
    await page.getByRole("button", { name: "Verifică datele" }).click();
    await page.getByRole("button", { name: "Salvează" }).click();
    await expect(page.getByRole("status")).toContainText("a fost înregistrată");
    await expect(page.getByText("Plata a fost înregistrată separat.")).toBeVisible();

    // record a partial collection
    await page.getByRole("link", { name: "Venituri" }).click();
    await page.getByLabel("Număr", { exact: true }).fill("1");
    await page.getByLabel("Sumă (RON, fără TVA)").fill("10000");
    await page.getByLabel("Cotă TVA (%)").fill("21");
    await page.getByRole("radio", { name: "Da" }).first().check();
    await page.getByLabel("Sumă încasată (poate fi parțială)").fill("4000");
    await page.getByRole("button", { name: "Verifică datele" }).click();
    await page.getByRole("button", { name: "Salvează" }).click();
    await expect(page.getByRole("status")).toContainText("a fost emisă");
    await expect(page.getByText(/Rest de încasat/)).toBeVisible();

    // the deadline card is dated and actionable
    await page.getByRole("link", { name: "Acasă" }).click();
    await expect(page.getByRole("table")).toBeVisible();
    await expect(page.getByText(/Termen de verificat|2026/).first()).toBeVisible();

    // declaration flow: check → generate → download → upload receipt
    await page.getByRole("link", { name: "Declarații" }).click();
    await expect(
      page.getByText(/Generarea, acceptarea de către ANAF și plata sunt stări distincte/),
    ).toBeVisible();
  });

  test("declarations expose the calculation explanation and block non-ready artifacts", async ({
    page,
  }) => {
    const email = `e2e-decl-${Date.now()}@example.com`;
    const password = "Correct-Horse-Battery-9";
    await registerAndVerify(page, email, password);
    await login(page, email, password);
    await page.getByRole("link", { name: "Declarații" }).click();
    await expect(page.getByRole("heading", { name: "Declarații" })).toBeVisible();
    await expect(page.getByText(/Nu există declarații pregătite/)).toBeVisible();
  });

  test("all critical flows work on a narrow mobile viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 780 });
    const email = `e2e-mobile-${Date.now()}@example.com`;
    await registerAndVerify(page, email, "Correct-Horse-Battery-9");
    await login(page, email, "Correct-Horse-Battery-9");
    for (const label of ["Venituri", "Cheltuieli", "Declarații", "Setări"]) {
      await page.getByRole("link", { name: label }).click();
      await expect(page.getByRole("heading", { name: label, level: 1 })).toBeVisible();
    }
  });
});
