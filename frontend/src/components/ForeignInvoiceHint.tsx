/**
 * Convention for foreign-currency invoices.
 *
 * The application records amounts in RON only (exchange-rate storage is modelled
 * but not wired yet), so the original currency amount and the rate used must stay
 * attached to the document — in the description, with the invoice file as evidence.
 */
export function ForeignInvoiceHint() {
  return (
    <small className="muted">
      Factură externă (altă valută)? Aplicația înregistrează în RON: notați în descriere suma
      originală și cursul folosit — de exemplu „Factura 123/2026 — 100 EUR × 5,0825 = 508,25 RON" —
      și atașați factura ca dovadă.
    </small>
  );
}
