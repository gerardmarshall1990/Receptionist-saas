CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE organizations (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  niche_slug    text NOT NULL,
  website       text,
  timezone      text NOT NULL DEFAULT 'America/New_York',
  entry_path    text NOT NULL CHECK (entry_path IN ('outbound', 'self_serve')),
  -- idempotency key from the caller (e.g. outbound lead id); retries must not create duplicates
  external_ref  text UNIQUE,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  email            text UNIQUE,
  phone            text UNIQUE,
  role             text NOT NULL DEFAULT 'owner' CHECK (role IN ('owner', 'staff')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  CHECK (email IS NOT NULL OR phone IS NOT NULL)
);
CREATE INDEX users_org_idx ON users (organization_id);

-- Passwordless login: short-lived one-time codes, stored hashed.
CREATE TABLE login_codes (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_hash    text NOT NULL,
  expires_at   timestamptz NOT NULL,
  attempts     int NOT NULL DEFAULT 0,
  consumed_at  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  token_hash       text NOT NULL UNIQUE,
  revoked_at       timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE receptionists (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id    uuid NOT NULL UNIQUE REFERENCES organizations(id) ON DELETE CASCADE,
  greeting           text NOT NULL,
  voice_id           text NOT NULL,
  business_hours     jsonb NOT NULL DEFAULT '{}',
  services           jsonb NOT NULL DEFAULT '[]',
  faqs               jsonb NOT NULL DEFAULT '[]',
  call_flow          jsonb NOT NULL DEFAULT '[]',
  transfer_rules     jsonb NOT NULL DEFAULT '{"enabled": false}',
  -- message_only until a calendar is connected, or when minutes run out
  mode               text NOT NULL DEFAULT 'message_only' CHECK (mode IN ('live', 'message_only')),
  forwarding_status  text NOT NULL DEFAULT 'pending' CHECK (forwarding_status IN ('pending', 'active')),
  voice_agent_id     text,
  updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE subscriptions (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id          uuid NOT NULL UNIQUE REFERENCES organizations(id) ON DELETE CASCADE,
  plan                     text NOT NULL DEFAULT 'starter' CHECK (plan IN ('starter', 'growth', 'pro', 'enterprise')),
  status                   text NOT NULL DEFAULT 'trialing',
  trial_ends_at            timestamptz,
  minutes_included         int NOT NULL,
  stripe_customer_id       text,
  stripe_subscription_id   text,
  created_at               timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE calls (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  caller_number    text,
  direction        text NOT NULL DEFAULT 'inbound' CHECK (direction IN ('inbound', 'test')),
  outcome          text,
  duration_sec     int,
  transcript       text,
  recording_url    text,
  started_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX calls_org_started_idx ON calls (organization_id, started_at DESC);

CREATE TABLE audit_log (
  id               bigserial PRIMARY KEY,
  organization_id  uuid REFERENCES organizations(id) ON DELETE SET NULL,
  actor_type       text NOT NULL CHECK (actor_type IN ('user', 'service', 'system')),
  actor_id         text,
  action           text NOT NULL,
  entity           text,
  entity_id        text,
  metadata         jsonb NOT NULL DEFAULT '{}',
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_org_idx ON audit_log (organization_id, created_at DESC);

-- Tenant isolation, defense in depth. App code scopes every query too; RLS makes a
-- forgotten WHERE clause return nothing instead of leaking another business's data.
-- Tenant requests set app.current_org (from the verified session, never from the client);
-- trusted server-side flows (signup, login lookup, webhooks) set app.system = 'on'.
CREATE FUNCTION app_org() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('app.current_org', true), '')::uuid $$;
CREATE FUNCTION app_is_system() RETURNS boolean LANGUAGE sql STABLE AS
  $$ SELECT coalesce(current_setting('app.system', true), '') = 'on' $$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['users', 'receptionists', 'subscriptions', 'calls', 'audit_log'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (organization_id = app_org() OR app_is_system()) WITH CHECK (organization_id = app_org() OR app_is_system())',
      t);
  END LOOP;
END $$;

ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON organizations
  USING (id = app_org() OR app_is_system()) WITH CHECK (id = app_org() OR app_is_system());
