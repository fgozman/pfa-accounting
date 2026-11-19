# Vendored Swagger UI

`swagger-ui-bundle.js`, `swagger-ui.css`, the favicons, `LICENSE` and `NOTICE` are
copied **verbatim** from the `swagger-ui-dist` npm package (Apache-2.0), pinned at the
version in `frontend/package.json`. `VENDORED.txt` records the version and the SHA-256
of every file; `npm run vendor:swagger-ui` refreshes them.

They are vendored on purpose:

* the API documentation then loads **no third-party script** from a CDN, which
  matches the production CSP (`script-src 'self'`) instead of requiring an
  exception for `cdn.jsdelivr.net`;
* the documentation keeps working without internet access, which matters for an
  application that also runs in restricted/on-premise environments.

The page FastAPI serves by default initialises Swagger UI with an inline
`<script>`, which a policy without `'unsafe-inline'` blocks (a blank page). The
page in `pfa/api/docs.py` therefore loads external scripts only.
