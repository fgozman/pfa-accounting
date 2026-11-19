"""OAuth scopes: the contract shared by the authorization server and the API.

The authorization server grants these scopes; the resource API (and its
documentation) demands them.  Keeping them here, instead of in either side,
means neither side imports the other just to agree on a string.
"""

from __future__ import annotations

SCOPE_READ = "pfa.read"
SCOPE_WRITE = "pfa.write"
SCOPE_MANAGE = "pfa.manage"

#: every scope a client may be registered for, in display order
SUPPORTED_SCOPES = ("openid", "profile", SCOPE_READ, SCOPE_WRITE, SCOPE_MANAGE)

__all__ = ["SCOPE_MANAGE", "SCOPE_READ", "SCOPE_WRITE", "SUPPORTED_SCOPES"]
