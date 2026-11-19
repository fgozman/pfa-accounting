import type { ReactNode } from "react";

import { dictionary, type Language } from "../i18n";

const STATUS_TONE: Record<string, string> = {
  required: "warn",
  needs_information: "warn",
  overdue: "danger",
  rejected: "danger",
  submission_unknown: "danger",
  due: "warn",
  accepted: "ok",
  approved: "ok",
  exported: "ok",
  not_required: "muted",
  draft: "muted",
};

export function StatusBadge({
  status,
  language,
  label,
}: {
  status: string;
  language: Language;
  label?: string;
}) {
  const dict = dictionary(language) as { status: Record<string, string> };
  const text = label ?? dict.status[status] ?? status;
  const tone = STATUS_TONE[status] ?? "muted";
  return (
    // colour is never the only indicator: the text is always present
    <span className={`badge badge--${tone}`} data-status={status}>
      {text}
    </span>
  );
}

export function ErrorNote({ error }: { error: { title: string; correlation_id?: string } }) {
  return (
    <p className="error" role="alert">
      {error.title}
      {error.correlation_id ? <small> (cod: {error.correlation_id})</small> : null}
    </p>
  );
}

export function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="section">
      <h2>{title}</h2>
      {description ? <p className="muted">{description}</p> : null}
      {children}
    </section>
  );
}
