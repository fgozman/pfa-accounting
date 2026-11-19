"""The application's HTTP routers.

Each module adapts HTTP to one functional area and calls that area's services
only.  Handlers never open database sessions, run queries, or import repositories
or table models: units of work come from :mod:`pfa.api.deps`, everything else
from services (enforced by ``tests/test_architecture.py``).  Routers never import
each other.

The OAuth 2.0 authorization server is **not** part of this package: its routes
live in :mod:`pfa.oauth.routes` and are mounted by the composition root
(:mod:`pfa.main`).  The application authenticates requests through
:mod:`pfa.clients.authorization_server` only.
"""

from __future__ import annotations

from fastapi import APIRouter

from pfa.api.routes import accounting, accounts, anaf, compliance, dev, me, workspaces

#: account lifecycle (registration, verification, password reset) and business API
APPLICATION_ROUTERS: tuple[APIRouter, ...] = (
    accounts.router,
    me.router,
    workspaces.router,
    accounting.router,
    compliance.router,
    anaf.router,
)

#: development-only mailbox (404 outside development/test)
DEVELOPMENT_ROUTERS: tuple[APIRouter, ...] = (dev.router,)

__all__ = ["APPLICATION_ROUTERS", "DEVELOPMENT_ROUTERS"]
