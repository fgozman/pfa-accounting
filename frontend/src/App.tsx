import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, apiFetch } from "./api/client";
import { authController } from "./auth/AuthController";
import { Layout } from "./components/Layout";
import { dictionary, type Language } from "./i18n";
import { CallbackPage } from "./pages/Callback";
import { DeclarationsPage } from "./pages/Declarations";
import { ExpensesPage } from "./pages/Expenses";
import { SettlementsPage } from "./pages/Settlements";
import { HomePage } from "./pages/Home";
import { IncomePage } from "./pages/Income";
import { AcceptInvitationPage } from "./pages/AcceptInvitation";
import { AuthorizationOriginNotice } from "./pages/AuthorizationOriginNotice";
import { LoginPage } from "./pages/Login";
import { PasswordResetPage } from "./pages/PasswordReset";
import { SettingsPage } from "./pages/Settings";
import { SignupPage } from "./pages/Signup";
import { VerifyEmailPage } from "./pages/VerifyEmail";
import { WorkspaceSetupPage } from "./pages/WorkspaceSetup";

export interface Workspace {
  id: string;
  name: string;
  cui: string | null;
  role: "ADMIN" | "DEFAULT" | null;
}

export type PageKey =
  | "home"
  | "income"
  | "expenses"
  | "settlements"
  | "declarations"
  | "settings";

const ROUTES: Record<string, PageKey> = {
  "/": "home",
  "/venituri": "income",
  "/cheltuieli": "expenses",
  "/incasari-plati": "settlements",
  "/declaratii": "declarations",
  "/setari": "settings",
};

/** Tab-scoped: the workspace you were last in, so a reload stays there. */
const WORKSPACE_KEY = "pfa.workspace";

export function App() {
  const [language, setLanguage] = useState<Language>("ro");
  const [session, setSession] = useState(authController.currentSession);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  // the workspace you were in is tab-scoped state: a reload must not silently move you
  // to another workspace (which also lost the document you were editing)
  const [workspaceId, setWorkspaceId] = useState<string | null>(() =>
    sessionStorage.getItem(WORKSPACE_KEY),
  );
  // distinguishes "still loading" from "loaded and empty": an account with no PFA
  // must be guided to create one instead of waiting forever
  const [workspacesLoaded, setWorkspacesLoaded] = useState(false);
  const [path, setPath] = useState(() => window.location.pathname);
  const tokenParam: string | undefined =
    new URLSearchParams(window.location.search).get("token") ?? undefined;
  const [bootError, setBootError] = useState<string | null>(null);

  useEffect(() => authController.subscribe(setSession), []);

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback((next: string) => {
    window.history.pushState({}, "", next);
    // routing keys on the path name; query parameters are read by the target page
    setPath(new URL(next, window.location.origin).pathname);
  }, []);

  // restore the session after a page reload: the access token is memory-only, so the
  // tab-scoped refresh token re-establishes the session (or clears it when refused)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (window.location.pathname === "/auth/callback") return;
      const restored = await authController.loadSession();
      if (cancelled) return;
      if (!restored) setWorkspaces([]);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadWorkspaces = useCallback(async () => {
    try {
      const body = await apiFetch<{ items: Workspace[] }>("/api/v1/workspaces");
      setWorkspaces(body.items);
      // an explicit workspace switch is always required; never guess silently
      setWorkspaceId((current) =>
        current && body.items.some((item) => item.id === current)
          ? current
          : (body.items[0]?.id ?? null),
      );
      setBootError(null);
    } catch (error) {
      setBootError(
        error instanceof ApiError ? error.problem.title : "Spațiile de lucru nu au putut fi încărcate.",
      );
    } finally {
      setWorkspacesLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (!session) {
      // The session is restored asynchronously, so "no session yet" is not a logout:
      // clearing the workspace here wiped the remembered one on every reload.
      setWorkspaces([]);
      setWorkspacesLoaded(false);
      return;
    }
    void loadWorkspaces();
  }, [session, loadWorkspaces]);

  const workspace = useMemo(
    () => workspaces.find((item) => item.id === workspaceId) ?? null,
    [workspaces, workspaceId],
  );

  const dict = dictionary(language);

  if (path === "/auth/callback") {
    return <CallbackPage navigate={navigate} />;
  }
  // `/oauth/authorize` belongs to the authorization service. Reaching it here
  // means the SPA origin was used by mistake: say so instead of looping back to
  // the login form.
  if (path === "/oauth/authorize") {
    return <AuthorizationOriginNotice navigate={navigate} />;
  }
  if (path === "/cont/inscriere") {
    return <SignupPage navigate={navigate} />;
  }
  // Activation and reset links point at the SPA, so they must render without a
  // session; the token in the URL is the proof.
  if (path === "/verify-email") {
    return <VerifyEmailPage navigate={navigate} />;
  }
  if (path === "/password-reset") {
    return <PasswordResetPage navigate={navigate} />;
  }
  if (path === "/password-reset/confirm") {
    return <PasswordResetPage navigate={navigate} token={tokenParam} />;
  }
  if (path === "/invitations/accept") {
    return (
      <AcceptInvitationPage navigate={navigate} token={tokenParam} signedIn={Boolean(session)} />
    );
  }

  if (!session) {
    return <LoginPage language={language} />;
  }

  const page = ROUTES[path] ?? "home";

  return (
    <Layout
      language={language}
      onLanguageChange={setLanguage}
      workspaces={workspaces}
      workspace={workspace}
      onWorkspaceChange={(id) => {
        sessionStorage.setItem(WORKSPACE_KEY, id);
        setWorkspaceId(id);
        // switching PFA clears cached tenant data
      }}
      page={page}
      navigate={navigate}
      onLogout={() => {
        // an explicit logout starts from a clean slate
        sessionStorage.removeItem(WORKSPACE_KEY);
        void authController.logout();
      }}
    >
      {bootError ? <p className="error">{bootError}</p> : null}
      {!workspace && workspacesLoaded ? (
        <WorkspaceSetupPage
          language={language}
          onCreated={(createdId) => {
            // reload the list so the new workspaces appear in the switcher
            void loadWorkspaces().then(() => {
              sessionStorage.setItem(WORKSPACE_KEY, createdId);
              setWorkspaceId(createdId);
            });
          }}
        />
      ) : !workspace ? (
        <p className="muted">{dict.labels.loading}</p>
      ) : page === "home" ? (
        <HomePage language={language} workspace={workspace} navigate={navigate} />
      ) : page === "income" ? (
        <IncomePage language={language} workspace={workspace} />
      ) : page === "expenses" ? (
        <ExpensesPage language={language} workspace={workspace} />
      ) : page === "settlements" ? (
        <SettlementsPage workspace={workspace} />
      ) : page === "declarations" ? (
        <DeclarationsPage language={language} workspace={workspace} />
      ) : (
        <SettingsPage language={language} workspace={workspace} />
      )}
    </Layout>
  );
}
