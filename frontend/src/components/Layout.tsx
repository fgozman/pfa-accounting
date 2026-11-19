import type { ReactNode } from "react";

import type { PageKey, Workspace } from "../App";
import { dictionary, type Language } from "../i18n";

const NAV: { key: PageKey; path: string }[] = [
  { key: "home", path: "/" },
  { key: "income", path: "/venituri" },
  { key: "expenses", path: "/cheltuieli" },
  { key: "settlements", path: "/incasari-plati" },
  { key: "declarations", path: "/declaratii" },
  { key: "settings", path: "/setari" },
];

export function Layout({
  language,
  onLanguageChange,
  workspaces,
  workspace,
  onWorkspaceChange,
  page,
  navigate,
  onLogout,
  children,
}: {
  language: Language;
  onLanguageChange: (language: Language) => void;
  workspaces: Workspace[];
  workspace: Workspace | null;
  onWorkspaceChange: (id: string) => void;
  page: PageKey;
  navigate: (path: string) => void;
  onLogout: () => void;
  children: ReactNode;
}) {
  const dict = dictionary(language);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar__brand">
          <strong>{dict.appName}</strong>
        </div>

        <div className="topbar__workspace">
          <label htmlFor="workspace-switch">{dict.labels.activePfa}</label>
          <select
            id="workspace-switch"
            value={workspace?.id ?? ""}
            onChange={(event) => onWorkspaceChange(event.target.value)}
          >
            {workspaces.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
                {item.cui ? ` · CUI ${item.cui}` : ""}
              </option>
            ))}
          </select>
        </div>

        <div className="topbar__actions">
          <label htmlFor="language-switch" className="visually-hidden">
            Limba
          </label>
          <select
            id="language-switch"
            value={language}
            onChange={(event) => onLanguageChange(event.target.value as Language)}
          >
            <option value="ro">Română</option>
            <option value="en">English</option>
          </select>
          <button type="button" className="link" onClick={onLogout}>
            {dict.actions.logout}
          </button>
        </div>
      </header>

      <nav className="mainnav" aria-label="Navigare principală">
        <ul>
          {NAV.map((item) => (
            <li key={item.key}>
              <a
                href={item.path}
                aria-current={page === item.key ? "page" : undefined}
                onClick={(event) => {
                  event.preventDefault();
                  navigate(item.path);
                }}
              >
                {dict.nav[item.key]}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      <main className="content">{children}</main>

      <footer className="footer">
        <small>{dict.disclaimer.estimate}</small>
      </footer>
    </div>
  );
}
