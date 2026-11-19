/**
 * Development mailbox helper.
 *
 * The API exposes the local mail capture sink only outside development/test
 * environments it answers `404`, so callers treat a failure as "no local mailbox"
 * and fall back to asking the user to paste the link.
 */

import { apiFetch } from "./client";

export interface CapturedMessage {
  to: string | null;
  subject: string | null;
  template: string | null;
  created_at: string | null;
  link: string | null;
  token: string | null;
}

export interface Mailbox {
  items: CapturedMessage[];
  sink_directory: string;
  note: string;
}

export async function fetchDevMailbox(
  template: string,
  email?: string,
): Promise<Mailbox | null> {
  try {
    return await apiFetch<Mailbox>("/api/v1/dev/mailbox", {
      query: { template, email: email ?? undefined, limit: 5 },
    });
  } catch {
    // production (or a missing sink): no local mailbox available
    return null;
  }
}

/** Extract a verification/reset token from a pasted link or bare token. */
export function extractToken(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const match = trimmed.match(/[?&]token=([A-Za-z0-9_\-]+)/);
  if (match) return match[1] ?? null;
  if (/^[A-Za-z0-9_\-]{16,200}$/.test(trimmed)) return trimmed;
  return null;
}
