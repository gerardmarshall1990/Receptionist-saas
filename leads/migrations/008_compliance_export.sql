-- Stage 5: compliance gate + export audit (spec sections 7 and 10).
-- businesses.compliance_* is the latest gate result, for browsing only: every export
-- re-runs the gate (and re-reads suppression) at write time.
alter table businesses add column if not exists compliance_status text
  check (compliance_status in ('pass', 'blocked'));
alter table businesses add column if not exists compliance_blocks text[] not null default '{}';
alter table businesses add column if not exists compliance_checked_at timestamptz;
create index if not exists businesses_compliance_score_idx
  on businesses (compliance_status, score desc nulls last, id);

create table if not exists exports (
  id                  uuid primary key,
  run_id              uuid references runs (id),
  origin              text not null check (origin in ('cli', 'explorer')),
  params              jsonb not null,
  config_hash         text not null,
  compliance_version  integer not null,
  n_candidates        integer not null default 0,
  n_passed            integer not null default 0,
  n_blocked           integer not null default 0,
  path                text,
  created_at          timestamptz not null default now()
);

-- One row per candidate: what was exported (row = the exact contract record) or why not.
create table if not exists export_items (
  export_id    uuid not null references exports (id) on delete cascade,
  business_id  uuid not null references businesses (id),
  status       text not null check (status in ('pass', 'blocked')),
  blocks       text[] not null default '{}',
  warnings     text[] not null default '{}',
  row          jsonb,
  primary key (export_id, business_id),
  check ((status = 'pass') = (row is not null))
);
create index if not exists export_items_business_idx on export_items (business_id);
