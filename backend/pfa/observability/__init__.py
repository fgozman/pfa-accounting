"""Observability shared by every process (API, OAuth server, worker).

* :mod:`.logs` — one JSON object per line on stdout in production (text in
  development), with redaction of secrets;
* :mod:`.context` — request/job context (correlation id, user, client) carried by
  every log record and by every audit row;
* :mod:`.access_log` — one structured ``http.request`` event per HTTP request.

Shared infrastructure: depends on neither the application nor the authorization
server (enforced by ``tests/test_architecture.py``).
"""
