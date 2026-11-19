"""Show the newest captured development e-mails (``make mail``).

Development and tests do not send mail: the worker writes one JSON file per
message to the e-mail sink (``PFA_EMAIL_DIR``).  This helper prints the newest
ones so the activation or reset link is one command away.
"""

from __future__ import annotations

import argparse
import re
import sys

from pfa.config import get_settings
from pfa.services.email import sink_from_settings

LINK = re.compile(r"https?://\S+")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show captured development e-mails")
    parser.add_argument("--template", help="verification | password_reset | invitation | reminder")
    parser.add_argument("--email", help="only messages to this recipient")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)

    settings = get_settings()
    if settings.env not in {"development", "test"}:
        print("E-mail capture is a development/test affordance only.", file=sys.stderr)
        return 2

    sink = sink_from_settings(settings)
    messages = sink.messages(template=args.template)
    if args.email:
        wanted = args.email.strip().casefold()
        messages = [m for m in messages if str(m.get("to", "")).casefold() == wanted]
    messages = sorted(
        messages, key=lambda m: str(m.get("created_at", "")), reverse=True
    )[: max(args.limit, 1)]

    if not messages:
        print(f"No captured e-mail in {sink.directory}.")
        print(
            "Only register / reset / invite / resend flows for existing unverified\n"
            "accounts write here; a resend for an unknown or already-verified address\n"
            "is a deliberate no-op and produces nothing."
        )
        return 0

    print(f"{len(messages)} captured message(s) in {sink.directory}:")
    for message in messages:
        body = str(message.get("body", ""))
        link = LINK.search(body)
        print(f"\n  to:      {message.get('to')}")
        print(f"  subject: {message.get('subject')}")
        print(f"  when:    {message.get('created_at')}")
        print(f"  link:    {link.group(0) if link else '(no link)'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
