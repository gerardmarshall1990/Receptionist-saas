-- Stage 0: only the two tables every later stage depends on.
-- businesses, phones, emails, signals, scores, reasons, outcomes arrive in the
-- stage that first writes them (spec section 3).

-- One row per job run. Every stage records what it cost and which config it used.
create table runs (
  id            uuid primary key,
  stage         text not null,
  status        text not null check (status in ('running', 'succeeded', 'failed', 'stopped_budget')),
  dry_run       boolean not null,
  config_hash   text not null,
  budget_usd    numeric(12, 4) not null check (budget_usd >= 0),
  cost_usd      numeric(12, 4) not null default 0 check (cost_usd >= 0),
  counts        jsonb not null default '{}'::jsonb,
  params        jsonb not null default '{}'::jsonb,
  error         text,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz
);
create index runs_stage_started_idx on runs (stage, started_at desc);

-- Permanent opt-outs. Never deleted by any job; the export gate checks it.
-- A row may match on phone, email, domain, or business id (any one is enough).
create table suppression (
  id            bigint generated always as identity primary key,
  match_type    text not null check (match_type in ('phone_e164', 'email', 'domain', 'business_id')),
  match_value   text not null,
  reason        text not null,
  source        text not null,
  created_at    timestamptz not null default now(),
  unique (match_type, match_value)
);

-- Enforce "permanent": block deletes and updates at the database level.
create function suppression_is_permanent() returns trigger language plpgsql as $$
begin
  raise exception 'suppression rows are permanent (attempted %)', tg_op;
end;
$$;
create trigger suppression_permanent before delete or update on suppression
  for each row execute function suppression_is_permanent();
