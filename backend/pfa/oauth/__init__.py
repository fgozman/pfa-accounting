"""The OAuth 2.0 authorization server, packaged so it can be moved to its own service.

Everything the authorization server owns lives in this package:

========================  ==================================================
``models``                the ``oauth_*`` tables (no foreign key leaves them)
``repo``                  every SQL statement against those tables
``flows``                 clients, authorization transactions, codes, token
                          endpoint, RFC 7009 revocation
``sessions``              login-session lifecycle (status, revoke, logout)
``introspection``         validate an access token and its session
``keys``                  the signing-key store
``pages``                 server-rendered login and error pages
``routes``                HTTP endpoints (``/oauth/*``, login POST, JWKS, logout)
``ports``                 what the server needs from its host
========================  ==================================================

Rules (enforced by ``tests/test_architecture.py``)
--------------------------------------------------
* Modules here import only each other and shared infrastructure: ``pfa.config``,
  ``pfa.errors``, ``pfa.clock``, ``pfa.db``, ``pfa.security``, ``pfa.repo.query``
  and ``pfa.models.base``.  Never the application's services, repositories, models
  or HTTP layer.
* Users are known only as an opaque subject id returned by the
  :class:`~pfa.oauth.ports.IdentityProvider`; no table here references ``users``.
* The application never imports this package.  It talks to the authorization
  server through :mod:`pfa.clients.authorization_server`; :mod:`pfa.wiring`
  connects both sides in-process.

Moving the server out therefore means: take this package (plus the shared
infrastructure), take the ``oauth_*`` tables, implement the two ports over HTTP
(identity: a credential-check endpoint of the application; audit: a local log),
and replace the application's in-process client with an RFC 7662 introspection
client.
"""
