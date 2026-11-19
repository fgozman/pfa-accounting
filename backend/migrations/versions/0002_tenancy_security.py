"""Tenancy security: roles, helper functions, RLS policies, append-only triggers.

Revision ID: 0002_tenancy_security
Revises: 0001_initial
Create Date: 2026-09-16

Security model installed here
-----------------------------
* ``app.current_workspace()`` / ``app.current_user()`` read transaction-local
  settings that are set only after a membership check and are cleared
  automatically at the transaction boundary.
* ``app.is_workspace_member()`` is SECURITY DEFINER so membership lookups do not
  recurse into the memberships RLS policy.
* Every workspace-owned table gets ``ENABLE ROW LEVEL SECURITY`` plus a
  ``workspace_id = app.current_workspace()`` policy.  The runtime role does not
  own tables and has no ``BYPASSRLS``, so the policies always apply.
* ``app.lease_jobs`` / ``app.lease_outbox`` expose identifiers only: workers
  enumerate work items without bypassing RLS for payloads.
* ``audit_events`` and accepted declarations/evidence are append-only.
"""

from __future__ import annotations

import os

from alembic import op
from sqlalchemy import text

revision = "0002_tenancy_security"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def runtime_role() -> str:
    return os.environ.get("PFA_APP_USER") or os.environ.get("POSTGRES_APP_USER") or "pfa_app"


FUNCTIONS = """
CREATE SCHEMA IF NOT EXISTS app;

CREATE OR REPLACE FUNCTION app.current_workspace() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('app.workspace_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION app.current_user() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('app.user_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION app.current_actor() RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT coalesce(nullif(current_setting('app.actor', true), ''), 'system')
$$;

-- SECURITY DEFINER: runs as the table owner, which bypasses RLS, so a policy on
-- memberships can consult memberships without infinite recursion.
CREATE OR REPLACE FUNCTION app.is_workspace_member(ws uuid, usr uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM memberships m
         WHERE m.workspace_id = ws AND m.user_id = usr AND m.status = 'active'
    )
$$;

-- Which workspaces may this user see for a given taxpayer?  Returns identifiers
-- only, so discovering an authorization does not expose any taxpayer data.
CREATE OR REPLACE FUNCTION app.authorized_workspaces_for_user(tp uuid, usr uuid)
RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT g.workspace_id
      FROM taxpayer_access_grants g
      JOIN memberships m
        ON m.workspace_id = g.workspace_id
       AND m.user_id = usr
       AND m.status = 'active'
     WHERE g.taxpayer_id = tp
       AND g.revoked_at IS NULL
$$;

-- An invitation must be readable by its intended recipient, who is authenticated
-- but not yet a member of any workspace for that invitation.  The helper returns
-- a boolean only, so the invitation stays invisible to everyone else.
CREATE OR REPLACE FUNCTION app.is_invitation_for_user(inv uuid, usr uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1
          FROM invitations i
          JOIN users u ON u.id = usr
         WHERE i.id = inv
           AND i.email_normalized = u.email_normalized
    )
$$;

-- A job or declaration reference is addressed by id, before tenant context is
-- known.  These helpers return the owning workspace so membership can be checked;
-- they expose no payload, no amount and no other column.
CREATE OR REPLACE FUNCTION app.job_workspace(job uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT workspace_id FROM jobs WHERE id = job
$$;

CREATE OR REPLACE FUNCTION app.declaration_workspace(decl uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT workspace_id FROM declarations WHERE id = decl
$$;

-- The ANAF callback arrives from an external redirect with no bearer token: the
-- one-use state secret is the proof.  Only the owning workspace is returned.
CREATE OR REPLACE FUNCTION app.anaf_connection_workspace(connection uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT workspace_id FROM anaf_connections WHERE id = connection
$$;

-- Workers need the set of tenants to process, but not their data.
CREATE OR REPLACE FUNCTION app.list_workspace_ids() RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT id FROM workspaces WHERE archived_at IS NULL
$$;

CREATE OR REPLACE FUNCTION app.lease_outbox(worker text, batch integer, lease_seconds integer)
RETURNS TABLE (
    id uuid,
    workspace_id uuid,
    event_type text,
    payload jsonb,
    attempts integer
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    -- reclaim leases whose worker died: the logical event stays single
    UPDATE outbox_events
       SET status = 'pending', locked_by = NULL, locked_until = NULL
     WHERE status = 'processing' AND locked_until IS NOT NULL AND locked_until < now();

    RETURN QUERY
    WITH leased AS (
        SELECT o.id
          FROM outbox_events o
         WHERE o.status = 'pending' AND o.available_at <= now()
         ORDER BY o.available_at
         LIMIT batch
         FOR UPDATE SKIP LOCKED
    )
    UPDATE outbox_events o
       SET status = 'processing',
           locked_by = worker,
           locked_until = now() + (lease_seconds || ' seconds')::interval,
           attempts = o.attempts + 1
      FROM leased
     WHERE o.id = leased.id
    RETURNING o.id, o.workspace_id, o.event_type::text, o.payload, o.attempts;
END
$$;

CREATE OR REPLACE FUNCTION app.lease_jobs(worker text, batch integer, lease_seconds integer)
RETURNS TABLE (
    id uuid,
    workspace_id uuid,
    kind text,
    payload jsonb,
    attempts integer,
    max_attempts integer
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    UPDATE jobs
       SET status = 'pending', leased_by = NULL, lease_expires_at = NULL
     WHERE status = 'leased' AND lease_expires_at IS NOT NULL AND lease_expires_at < now();

    RETURN QUERY
    WITH leased AS (
        SELECT j.id
          FROM jobs j
         WHERE j.status = 'pending' AND j.available_at <= now()
         ORDER BY j.priority, j.available_at
         LIMIT batch
         FOR UPDATE SKIP LOCKED
    )
    UPDATE jobs j
       SET status = 'leased',
           leased_by = worker,
           lease_expires_at = now() + (lease_seconds || ' seconds')::interval,
           started_at = coalesce(j.started_at, now()),
           attempts = j.attempts + 1
      FROM leased
     WHERE j.id = leased.id
    RETURNING j.id, j.workspace_id, j.kind::text, j.payload, j.attempts, j.max_attempts;
END
$$;
"""

APPEND_ONLY_TRIGGERS = """
CREATE OR REPLACE FUNCTION app.reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'append-only table: % may not be %',
        TG_TABLE_NAME, lower(TG_OP)
        USING ERRCODE = 'restrict_violation';
END
$$;

DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events;
CREATE TRIGGER audit_events_append_only
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

DROP TRIGGER IF EXISTS declaration_versions_append_only ON declaration_versions;
CREATE TRIGGER declaration_versions_append_only
    BEFORE DELETE ON declaration_versions
    FOR EACH ROW EXECUTE FUNCTION app.reject_mutation();

CREATE OR REPLACE FUNCTION app.protect_accepted_declarations() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'accepted' THEN
        RAISE EXCEPTION 'declaratiile acceptate sunt imutabile'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END
$$;

DROP TRIGGER IF EXISTS declarations_no_delete ON declarations;
CREATE TRIGGER declarations_no_delete
    BEFORE DELETE ON declarations
    FOR EACH ROW EXECUTE FUNCTION app.protect_accepted_declarations();

CREATE OR REPLACE FUNCTION app.protect_statutory_evidence() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.verified THEN
        RAISE EXCEPTION 'recipisele verificate nu pot fi sterse'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END
$$;

DROP TRIGGER IF EXISTS receipts_no_delete ON receipts;
CREATE TRIGGER receipts_no_delete
    BEFORE DELETE ON receipts
    FOR EACH ROW EXECUTE FUNCTION app.protect_statutory_evidence();
"""


def _standard_policy_tables(connection) -> list[tuple[str, str]]:
    """Tables that carry ``workspace_id`` and therefore get the standard policy.

    ``workspace_id`` may be nullable for a few tables (personal source records,
    global exchange rates, account-level audit); those use a NULL-tolerant
    variant so unscoped rows stay invisible inside a tenant transaction.
    """
    rows = connection.execute(
        text(
            """
            SELECT c.table_name, (c.is_nullable = 'YES') AS nullable
              FROM information_schema.columns c
              JOIN information_schema.tables t
                ON t.table_schema = c.table_schema AND t.table_name = c.table_name
             WHERE c.table_schema = 'public'
               AND c.column_name = 'workspace_id'
               AND t.table_type = 'BASE TABLE'
             ORDER BY c.table_name
            """
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


def _split_sql(sql: str) -> list[str]:
    """Split a SQL script into statements for drivers that prepare statements.

    Dollar-quoted function bodies, quoted strings and comments are respected, so
    ``$$ ... ; ... $$`` is treated as one statement.
    """
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if char == "-" and sql.startswith("--", index):
            end = sql.find("\n", index)
            end = length if end == -1 else end
            buffer.append(sql[index : end + 1])
            index = end + 1
            continue
        if char == "/" and sql.startswith("/*", index):
            end = sql.find("*/", index)
            end = length - 2 if end == -1 else end
            buffer.append(sql[index : end + 2])
            index = end + 2
            continue
        if char == "'":
            end = index + 1
            while end < length:
                if sql[end] == "'":
                    if end + 1 < length and sql[end + 1] == "'":
                        end += 2
                        continue
                    break
                end += 1
            buffer.append(sql[index : end + 1])
            index = end + 1
            continue
        if char == '"':
            end = sql.find('"', index + 1)
            end = length - 1 if end == -1 else end
            buffer.append(sql[index : end + 1])
            index = end + 1
            continue
        if char == "$":
            tag_end = sql.find("$", index + 1)
            if (tag_end != -1 and sql[index + 1 : tag_end].replace("_", "").isalnum()) or tag_end == index + 1:
                tag = sql[index : tag_end + 1]
                close = sql.find(tag, tag_end + 1)
                close = length if close == -1 else close + len(tag)
                buffer.append(sql[index:close])
                index = close
                continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def _execute_script(connection, sql: str) -> None:
    for statement in _split_sql(sql):
        connection.execute(text(statement))


def upgrade() -> None:
    connection = op.get_bind()
    role = runtime_role()

    _execute_script(connection, FUNCTIONS)
    _execute_script(connection, APPEND_ONLY_TRIGGERS)

    # Helper used by the taxpayers policy (must exist before the policy is parsed).
    connection.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION app.is_person_authorized(tp uuid, ws uuid) RETURNS boolean
            LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
                SELECT EXISTS (
                    SELECT 1 FROM taxpayer_access_grants g
                     WHERE g.taxpayer_id = tp AND g.workspace_id = ws AND g.revoked_at IS NULL
                )
            $$
            """
        )
    )

    # Grant DML to the runtime role, never ownership.
    connection.execute(
        text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    EXECUTE format('GRANT USAGE ON SCHEMA public, app TO {role}');
                    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}');
                    EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}');
                    EXECUTE format('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA app TO {role}');
                    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}');
                    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}');
                    -- the runtime role must not be able to disable the protections
                    EXECUTE format('REVOKE ALL ON SCHEMA app FROM PUBLIC');
                    EXECUTE format('GRANT USAGE ON SCHEMA app TO {role}');
                END IF;
            END
            $$
            """
        )
    )

    # Row-level security on every workspace-owned table.
    for table_name, nullable in _standard_policy_tables(connection):
        connection.execute(text(f'ALTER TABLE public."{table_name}" ENABLE ROW LEVEL SECURITY'))
        # The owner (migrations, DDL) is not the runtime path; keep RLS active for
        # the runtime role only.  FORCE would also apply policies to the owner.
        if table_name == "invitations":
            connection.execute(
                text(
                    """
                    CREATE POLICY invitations_scope ON invitations
                        USING (
                            workspace_id = app.current_workspace()
                            OR app.is_invitation_for_user(id, app.current_user())
                        )
                        WITH CHECK (true)
                    """
                )
            )
            continue
        if table_name in {"audit_events", "outbox_events"}:
            # These tables are written from contexts that have no tenant (account
            # e-mails, system events) but read only inside a tenant.
            connection.execute(
                text(
                    f"""
                    CREATE POLICY {table_name}_tenant_read ON "{table_name}"
                        FOR SELECT
                        USING (
                            (workspace_id IS NOT NULL AND workspace_id = app.current_workspace())
                            -- the worker that marks plumbing rows as done must be
                            -- able to see the row it is updating
                            OR app.current_actor() = 'worker'
                        )
                    """
                )
            )
            connection.execute(
                text(
                    f'CREATE POLICY {table_name}_append ON "{table_name}" '
                    "FOR INSERT WITH CHECK (true)"
                )
            )
            continue
        if table_name == "taxpayers":
            connection.execute(
                text(
                    """
                    CREATE POLICY taxpayers_scope ON taxpayers
                        USING (
                            workspace_id = app.current_workspace()
                            OR app.is_person_authorized(id, app.current_workspace())
                            OR (workspace_id IS NULL AND created_by_user_id = app.current_user())
                        )
                        WITH CHECK (
                            workspace_id = app.current_workspace()
                            OR (workspace_id IS NULL AND created_by_user_id = app.current_user())
                        )
                    """
                )
            )
            continue
        if nullable:
            # NULL workspace_id marks a record outside any workspace (personal
            # source, global rate, account-level row).  It is invisible to tenants.
            condition = (
                "workspace_id IS NOT NULL AND workspace_id = app.current_workspace()"
            )
            check = "workspace_id IS NULL OR workspace_id = app.current_workspace()"
        else:
            condition = "workspace_id = app.current_workspace()"
            check = condition
        connection.execute(
            text(
                f"""
                CREATE POLICY {table_name}_tenant ON "{table_name}"
                    USING ({condition})
                    WITH CHECK ({check})
                """
            )
        )

    # Background workers must be able to mark their own leased plumbing rows as
    # done.  They get UPDATE (never a tenant read) through the actor marker that
    # only ``Database.worker_session`` installs.
    connection.execute(
        text(
            """
            CREATE POLICY outbox_events_worker_update ON outbox_events
                FOR UPDATE
                USING (app.current_actor() = 'worker')
                WITH CHECK (true)
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE POLICY jobs_worker_update ON jobs
                FOR UPDATE
                USING (app.current_actor() = 'worker')
                WITH CHECK (true)
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE POLICY jobs_worker_read ON jobs
                FOR SELECT
                USING (app.current_actor() = 'worker')
            """
        )
    )

    # Workspaces and memberships are keyed by membership, not by tenant context:
    # looking up "which workspaces may I see" cannot itself require a tenant.
    connection.execute(text("ALTER TABLE public.workspaces ENABLE ROW LEVEL SECURITY"))
    connection.execute(
        text(
            """
            CREATE POLICY workspaces_membership ON workspaces
                USING (
                    id = app.current_workspace()
                    OR created_by_user_id = app.current_user()
                    OR app.is_workspace_member(id, app.current_user())
                )
                WITH CHECK (
                    created_by_user_id = app.current_user()
                    OR app.is_workspace_member(id, app.current_user())
                    OR id = app.current_workspace()
                )
            """
        )
    )
    connection.execute(text("ALTER TABLE public.memberships ENABLE ROW LEVEL SECURITY"))
    connection.execute(
        text(
            """
            CREATE POLICY memberships_scope ON memberships
                USING (
                    user_id = app.current_user()
                    OR workspace_id = app.current_workspace()
                    OR app.is_workspace_member(workspace_id, app.current_user())
                )
                WITH CHECK (true)
            """
        )
    )

    if role:
        connection.execute(
            text(f"GRANT EXECUTE ON FUNCTION app.is_person_authorized(uuid, uuid) TO {role}")
        )
        connection.execute(
            text(
                f"GRANT EXECUTE ON FUNCTION app.is_invitation_for_user(uuid, uuid) TO {role}"
            )
        )
        connection.execute(text(f"GRANT EXECUTE ON FUNCTION app.job_workspace(uuid) TO {role}"))
        connection.execute(
            text(f"GRANT EXECUTE ON FUNCTION app.declaration_workspace(uuid) TO {role}")
        )
        connection.execute(
            text(f"GRANT EXECUTE ON FUNCTION app.anaf_connection_workspace(uuid) TO {role}")
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table_name, _nullable in _standard_policy_tables(connection):
        connection.execute(
            text(f'DROP POLICY IF EXISTS {table_name}_tenant ON public."{table_name}"')
        )
        connection.execute(text(f'ALTER TABLE public."{table_name}" DISABLE ROW LEVEL SECURITY'))
    connection.execute(text("DROP POLICY IF EXISTS workspaces_membership ON workspaces"))
    connection.execute(text("DROP POLICY IF EXISTS memberships_scope ON memberships"))
    connection.execute(text("DROP SCHEMA IF EXISTS app CASCADE"))
