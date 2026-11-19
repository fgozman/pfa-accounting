import { expect, test } from "@playwright/test";

/**
 * Interactive API documentation.
 *
 * Regression guard: `/docs` used to answer `200` while rendering nothing, because
 * the Content-Security-Policy blocked the inline script that boots Swagger UI.
 * A status-code assertion would not have caught it, so this suite asserts the
 * rendered DOM and fails on any console error or failed request.
 */

const API = process.env.E2E_API_ORIGIN ?? "http://127.0.0.1:8010";

test.describe("api documentation", () => {
  test("swagger ui renders every operation and offers authorization", async ({ page }) => {
    const consoleErrors: string[] = [];
    const failedRequests: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => consoleErrors.push(error.message));
    page.on("requestfailed", (request) =>
      failedRequests.push(`${request.url()} :: ${request.failure()?.errorText}`),
    );

    await page.goto(`${API}/docs`, { waitUntil: "networkidle" });

    // the UI actually booted
    await expect(page.locator("#swagger-ui .info .title")).toContainText("PFA Accounting API");
    expect(await page.locator("#swagger-ui .opblock").count()).toBeGreaterThan(20);

    // the documentation is self-hosted: nothing is fetched from a third party
    const external = failedRequests.filter((entry) => !entry.startsWith(API));
    expect(consoleErrors).toEqual([]);
    expect(external).toEqual([]);

    // no request left this origin
    const requests: string[] = [];
    page.on("request", (request) => requests.push(request.url()));
    await page.reload({ waitUntil: "networkidle" });
    const offOrigin = requests.filter((url) => !url.startsWith(API));
    expect(offOrigin).toEqual([]);
  });

  test("authorize exposes both a pasted token and the real login", async ({ page }) => {
    await page.goto(`${API}/docs`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: /authorize/i }).first().click();

    const modal = page.locator(".modal-ux");
    await expect(modal).toBeVisible();

    // HTTPBearer: a field for a short-lived token
    await expect(modal.getByText(/HTTPBearer/).first()).toBeVisible();
    expect(await modal.locator('input[type="text"]').count()).toBeGreaterThan(0);

    // OAuth2: the real authorization-code + PKCE flow, with its scopes
    await expect(modal.getByText(/authorizationCode with PKCE/).first()).toBeVisible();
    await expect(modal.getByText("Documentație API PFA")).toBeVisible();
    await expect(modal.getByText("pfa.write")).toBeVisible();

    // one submit path per scheme: paste a token, or run the real login
    await expect(modal.getByRole("button", { name: "Apply credentials" })).toBeVisible();
    await expect(
      modal.getByRole("button", { name: "Apply given OAuth2 credentials" }),
    ).toBeVisible();
  });

  test("the openapi document is published", async ({ request }) => {
    const response = await request.get(`${API}/openapi.json`);
    expect(response.status()).toBe(200);
    const document = await response.json();
    expect(Object.keys(document.paths).length).toBeGreaterThan(40);
    expect(Object.keys(document.components.securitySchemes)).toEqual(
      expect.arrayContaining(["HTTPBearer", "OAuth2"]),
    );
  });
});
