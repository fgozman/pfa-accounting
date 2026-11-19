import { useState } from "react";

import type { Workspace } from "../App";
import { ApiError, apiFetch } from "../api/client";
import { useApi } from "../api/useApi";
import { ErrorNote, Section, StatusBadge } from "../components/StatusBadge";
import {
  FiscalRegimeForm,
  REGIME_LABELS,
  VAT_LABELS,
  type RegimeRow,
  type VatRow,
} from "../components/FiscalRegimeForm";
import { FiscalIdentityForm, type IdentityProfile } from "../components/FiscalIdentityForm";
import { TaxFactsForm } from "../components/TaxFactsForm";
import { formatLongDate } from "../format";
import { dictionary, type Language } from "../i18n";

interface Member {
  user_id: string;
  email: string | null;
  role: "ADMIN" | "DEFAULT";
  status: string;
  joined_at: string;
}

interface Invitation {
  id: string;
  email: string;
  role: string;
  status: string;
  expires_at: string;
}

interface FiscalProfile {
  profile: IdentityProfile | null;
  vat_registrations: VatRow[];
  tax_regimes: RegimeRow[];
  year_facts: { fact_key: string; income_year: number; value: unknown; answered: boolean }[];
}

/**
 * Settings: fiscal profile, ANAF connection, security notes and — for ADMIN only —
 * user management.  The application has exactly two roles; ADMIN adds user
 * management and nothing else.
 */
export function SettingsPage({ language, workspace }: { language: Language; workspace: Workspace }) {
  const dict = dictionary(language);
  const isAdmin = workspace.role === "ADMIN";
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"ADMIN" | "DEFAULT">("DEFAULT");

  const profile = useApi<FiscalProfile>(`/api/v1/workspaces/${workspace.id}/fiscal-profile`);
  const members = useApi<{ items: Member[] }>(`/api/v1/workspaces/${workspace.id}/members`);
  const invitations = useApi<{ items: Invitation[] }>(
    isAdmin ? `/api/v1/workspaces/${workspace.id}/invitations` : null,
  );
  const anaf = useApi<{ connections: { id: string; status: string }[] }>(
    `/api/v1/workspaces/${workspace.id}/anaf/status`,
  );

  return (
    <div className="stack">
      <h1>{dict.nav.settings}</h1>
      <p className="muted">
        {dict.labels.activePfa}: <strong>{workspace.name}</strong> · rol: {workspace.role}
      </p>

      <Section title="Profil fiscal" description="Datele versionate ale PFA, pe ani de venit.">
        {profile.loading ? <p>{dict.labels.loading}</p> : null}
        {profile.error ? <ErrorNote error={profile.error.problem} /> : null}
        {profile.data ? (
          <>
            <dl className="summary">
              <div>
                <dt>Denumire</dt>
                <dd>{profile.data.profile?.legal_name || workspace.name}</dd>
              </div>
              <div>
                <dt>CUI</dt>
                <dd>{profile.data.profile?.cui || workspace.cui || "—"}</dd>
              </div>
              <div>
                <dt>Regim fiscal</dt>
                <dd>
                  {profile.data.tax_regimes.length > 0
                    ? profile.data.tax_regimes
                        .map(
                          (regime) =>
                            `${REGIME_LABELS[regime.kind] ?? regime.kind} (${regime.income_year})`,
                        )
                        .join(", ")
                    : "nespecificat"}
                </dd>
              </div>
              <div>
                <dt>Înregistrare TVA</dt>
                <dd>
                  {profile.data.vat_registrations.length > 0
                    ? profile.data.vat_registrations
                        .map(
                          (item) =>
                            `${VAT_LABELS[item.kind] ?? item.kind} din ${formatLongDate(item.effective_from)}`,
                        )
                        .join(", ")
                    : "neînregistrat"}
                </dd>
              </div>
            </dl>

            <FiscalIdentityForm
              workspaceId={workspace.id}
              defaultName={workspace.name}
              defaultCui={workspace.cui ?? ""}
              profile={profile.data.profile}
              onSaved={(message) => {
                setNotice(message);
                profile.reload();
              }}
              onError={(caught) => setError(caught as Error)}
            />

            <FiscalRegimeForm
              workspaceId={workspace.id}
              profile={profile.data}
              onSaved={(message) => {
                setNotice(message);
                profile.reload();
              }}
              onError={(caught) => setError(caught as Error)}
            />

          </>
        ) : null}
      </Section>

      <Section
        title="Informații fiscale pe an"
        description="Răspunsurile pe care le cer declarațiile (D212 și celelalte)."
      >
        <p className="muted">
          O informație fără răspuns blochează declarațiile care au nevoie de ea și apare ca
          „Informații lipsă". Lăsați gol pentru „nu știu": valoarea rămâne necunoscută, nu devine
          zero sau „nu".
        </p>
        <TaxFactsForm
          workspaceId={workspace.id}
          onSaved={(message) => {
            setNotice(message);
            profile.reload();
          }}
          onError={(caught) => setError(caught as Error)}
        />
      </Section>

      <Section title="Conexiune ANAF" description="Autorizarea ANAF este separată de autentificarea în aplicație.">
        {anaf.data && anaf.data.connections.length === 0 ? (
          <p className="muted">Nu există nicio conexiune ANAF activă.</p>
        ) : null}
        <ul>
          {(anaf.data?.connections ?? []).map((connection) => (
            <li key={connection.id}>
              Conexiune <StatusBadge status={connection.status} language={language} />
            </li>
          ))}
        </ul>
        <button
          type="button"
          onClick={async () => {
            try {
              const response = await apiFetch<{ authorization_url: string }>(
                `/api/v1/workspaces/${workspace.id}/anaf/connect`,
                { method: "POST" },
              );
              window.location.assign(response.authorization_url);
            } catch (caught) {
              setError(caught as Error);
            }
          }}
        >
          Conectează ANAF
        </button>
        <p className="muted">
          Nu solicităm și nu stocăm parola SPV. Seriile și certificatele rămân sub controlul
          dumneavoastră.
        </p>
      </Section>

      <Section title="Membri" description="Ambele roluri văd toate documentele spațiului de lucru.">
        {members.error ? <ErrorNote error={members.error.problem} /> : null}
        <table>
          <thead>
            <tr>
              <th scope="col">Email</th>
              <th scope="col">Rol</th>
              <th scope="col">{dict.labels.status}</th>
              {isAdmin ? <th scope="col">Acțiuni</th> : null}
            </tr>
          </thead>
          <tbody>
            {(members.data?.items ?? []).map((member) => (
              <tr key={member.user_id}>
                <th scope="row">{member.email}</th>
                <td>{member.role}</td>
                <td>{member.status}</td>
                {isAdmin ? (
                  <td>
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          await apiFetch(
                            `/api/v1/workspaces/${workspace.id}/members/${member.user_id}`,
                            {
                              method: "PATCH",
                              body: { role: member.role === "ADMIN" ? "DEFAULT" : "ADMIN" },
                            },
                          );
                          members.reload();
                        } catch (caught) {
                          setError(caught as Error);
                        }
                      }}
                    >
                      {member.role === "ADMIN" ? "Retrogradează" : "Numеște administrator"}
                    </button>
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
        {!isAdmin ? (
          <p className="muted">
            Gestionarea utilizatorilor este disponibilă doar administratorilor. Toate celelalte
            operațiuni sunt identice pentru ambele roluri.
          </p>
        ) : null}
      </Section>

      {isAdmin ? (
        <Section title="Invitații" description="Invitațiile sunt legate de adresă, o singură folosire, valabile 7 zile.">
          <div className="field">
            <label htmlFor="invite-email">Adresă de email</label>
            <input
              id="invite-email"
              type="email"
              value={inviteEmail}
              onChange={(event) => setInviteEmail(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="invite-role">Rol</label>
            <select
              id="invite-role"
              value={inviteRole}
              onChange={(event) => setInviteRole(event.target.value as "ADMIN" | "DEFAULT")}
            >
              <option value="DEFAULT">DEFAULT</option>
              <option value="ADMIN">ADMIN</option>
            </select>
          </div>
          <button
            type="button"
            onClick={async () => {
              try {
                await apiFetch(`/api/v1/workspaces/${workspace.id}/invitations`, {
                  method: "POST",
                  body: { email: inviteEmail, role: inviteRole },
                });
                setInviteEmail("");
                invitations.reload();
                setNotice("Invitația a fost trimisă.");
              } catch (caught) {
                setError(caught as Error);
              }
            }}
          >
            Trimite invitația
          </button>
          <table>
            <thead>
              <tr>
                <th scope="col">Email</th>
                <th scope="col">Rol</th>
                <th scope="col">{dict.labels.status}</th>
                <th scope="col">Expiră</th>
                <th scope="col" />
              </tr>
            </thead>
            <tbody>
              {(invitations.data?.items ?? []).map((invitation) => (
                <tr key={invitation.id}>
                  <th scope="row">{invitation.email}</th>
                  <td>{invitation.role}</td>
                  <td>{invitation.status}</td>
                  <td>{formatLongDate(invitation.expires_at)}</td>
                  <td>
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          await apiFetch(
                            `/api/v1/workspaces/${workspace.id}/invitations/${invitation.id}/revoke`,
                            { method: "POST" },
                          );
                          invitations.reload();
                        } catch (caught) {
                          setError(caught as Error);
                        }
                      }}
                    >
                      Revocă
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      ) : null}

      <Section title="Securitate" description="Politica de stocare a tokenurilor.">
        <ul>
          <li>Tokenul de acces este păstrat doar în memoria browserului.</li>
          <li>
            Tokenul de reîmprospătare este păstrat în <code>sessionStorage</code>, izolat pe tab,
            ca reîncărcarea paginii să nu vă deconecteze; se șterge la închiderea tabului, iar
            repornirea browserului cere o nouă autentificare.
          </li>
          <li>Nu se folosesc cookie-uri pentru autentificare.</li>
          <li>
            Doar verificatorul PKCE și parametrul state sunt păstrate temporar în sessionStorage,
            exclusiv pentru durata redirecționării.
          </li>
          <li>
            Tokenul de reîmprospătare se rotește la fiecare folosire, iar refolosirea lui revocă
            întreaga sesiune.
          </li>
        </ul>
      </Section>

      {error ? <ErrorNote error={{ title: (error as Error).message }} /> : null}
      {notice ? (
        <p className="info" role="status">
          {notice}
        </p>
      ) : null}
    </div>
  );
}
