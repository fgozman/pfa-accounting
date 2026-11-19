"""Server-rendered pages of the authorization server: login form and error page.

Pure presentation: plain functions from values to HTML, no database, no services.
Every dynamic value is HTML-escaped, and error text shown on the login page comes
from :data:`LOGIN_ERROR_MESSAGES` (fixed codes), never from the request.
"""

from __future__ import annotations

import html
import uuid

from pfa.config import Settings, get_settings

#: Codes the login form may return to the authorization page with.  Fixed values
#: only: the error is never echoed back from the request, so nothing user-supplied
#: is reflected into the page.  Keys cover ``pfa.oauth.ports.LOGIN_REJECTION_REASONS``.
LOGIN_ERROR_MESSAGES = {
    "invalid_credentials": (
        "Datele de autentificare nu sunt corecte. Verificați adresa de email și parola."
    ),
    "email_not_verified": (
        "Contul nu este verificat. Deschideți linkul de activare primit prin email, apoi "
        "reîncercați. Dacă linkul a expirat, puteți cere unul nou."
    ),
    "too_many_attempts": (
        "Prea multe încercări de autentificare. Așteptați câteva momente și reîncercați."
    ),
    "origin_mismatch": "Cererea nu provine de pe originea autorizată.",
}

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Autentificare PFA</title>
<style>
 :root { color-scheme: light dark; }
 body { font-family: system-ui, sans-serif; margin: 0; padding: 2rem; max-width: 34rem; }
 h1 { font-size: 1.35rem; }
 form { display: grid; gap: 0.75rem; margin-top: 1.5rem; }
 label { font-weight: 600; }
 input[type=email], input[type=password] { padding: 0.6rem; font-size: 1rem; width: 100%%; }
 button { padding: 0.7rem 1.1rem; font-size: 1rem; cursor: pointer; }
 .error { border-left: 4px solid #b00020; background: #fdecef; padding: 0.75rem; margin-top: 1rem; }
 .notice { border-left: 4px solid #444; padding: 0.75rem; margin-top: 1rem; }
 code { font-size: 0.85rem; }
</style>
</head>
<body>
<h1>Autentificare pentru aplicația PFA</h1>
<p class="notice">Sunteți pe pagina de autentificare a serviciului de autorizare.
Credențialele nu ajung niciodată în aplicația web. Această pagină nu folosește cookie-uri.</p>
%(error)s
%(form)s
<p><small>Fără cont? <a href="%(signup_url)s">Creați unul</a> și verificați adresa de email.</small></p>
</body>
</html>
"""

#: the credential form exists only for a validated authorization transaction; an
#: error page without one must not offer a form that posts to an empty transaction id
LOGIN_FORM = """<form method="post" action="/auth/authorization-transactions/%(tx)s/authenticate" autocomplete="on">
  <input type="hidden" name="transaction_proof" value="%(proof)s">
  <label for="email">Adresă de email</label>
  <input id="email" name="email" type="email" required autocomplete="username" autofocus>
  <label for="password">Parolă</label>
  <input id="password" name="password" type="password" required autocomplete="current-password"
         minlength="12">
  <button type="submit">Autentificare</button>
</form>"""

RESTART_HINT = (
    '<p><a href="%(spa_url)s">Reveniți la aplicație</a> și reluați autentificarea de acolo.</p>'
)


def signup_url(settings: Settings) -> str:
    """Absolute SPA URL for sign-up.

    The login page is served by the authorization origin, so SPA paths must be
    absolute: a relative ``/cont/inscriere`` would be requested from this service
    and answered with a 404.
    """
    return f"{settings.spa_origin.rstrip('/')}/cont/inscriere"


def login_page(
    transaction_id: uuid.UUID | None,
    proof: str,
    error_code: str | None = None,
    *,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    message = LOGIN_ERROR_MESSAGES.get(error_code or "", "")
    error_html = f'<div class="error">{html.escape(message)}</div>' if message else ""
    if error_code == "email_not_verified":
        resend_url = f"{settings.spa_origin.rstrip('/')}/verify-email?resend=1"
        error_html += (
            '<p class="notice">Linkul de activare poate fi retrimis: '
            f'<a href="{html.escape(resend_url)}">cereți un link nou de verificare</a>.</p>'
        )
    if transaction_id is None:
        return error_page(message or "Sesiunea de autorizare lipsește.", settings=settings)
    form = LOGIN_FORM % {
        "tx": html.escape(str(transaction_id)),
        "proof": html.escape(proof) if proof else "",
    }
    return LOGIN_PAGE % {
        "error": error_html,
        "form": form,
        "signup_url": html.escape(signup_url(settings)),
    }


def error_page(message: str, *, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return LOGIN_PAGE % {
        "error": f'<div class="error">{html.escape(message)}</div>',
        "form": RESTART_HINT % {"spa_url": html.escape(settings.spa_origin.rstrip("/") + "/")},
        "signup_url": html.escape(signup_url(settings)),
    }
