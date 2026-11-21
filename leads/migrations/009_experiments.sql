-- Stage 6: pilot experiment (scored vs random unscored list) and call outcomes.
create table if not exists experiments (
  id           uuid primary key,
  name         text not null unique,
  status       text not null default 'locked' check (status in ('locked', 'closed')),
  seed         text not null,
  params       jsonb not null,       -- experiment.yaml values + compliance version at creation
  config_hash  text not null,
  created_at   timestamptz not null default now()
);

-- Frozen before any call: which lead is in which arm. A lead is in at most one arm.
create table if not exists experiment_assignments (
  experiment_id  uuid not null references experiments (id),
  business_id    uuid not null references businesses (id),
  arm            text not null check (arm in ('scored', 'control')),
  tier_at_assignment char(1),
  score_at_assignment numeric(5, 2),
  call_order     integer not null,   -- shuffled position on the blinded call list
  primary key (experiment_id, business_id),
  unique (experiment_id, call_order)
);

-- Assignments are the experiment's record: never edited or removed.
create or replace function assignments_are_frozen() returns trigger language plpgsql as $$
begin
  raise exception 'experiment assignments are frozen (attempted %)', tg_op;
end;
$$;
drop trigger if exists assignments_frozen on experiment_assignments;
create trigger assignments_frozen before update or delete on experiment_assignments
  for each row execute function assignments_are_frozen();

-- One row per call attempt, imported from the dialer/CRM.
create table if not exists outcomes (
  id             bigint generated always as identity primary key,
  experiment_id  uuid references experiments (id),
  business_id    uuid not null references businesses (id),
  called_at      timestamptz not null,
  caller         text not null default '',
  disposition    text not null check (disposition in (
                   'no_answer', 'voicemail', 'gatekeeper', 'decision_maker', 'callback',
                   'not_interested', 'has_answering_service', 'wrong_number',
                   'closed_or_not_business', 'do_not_call')),
  demo_booked    boolean not null default false,
  trial_started  boolean not null default false,
  paid           boolean not null default false,
  notes          text,
  source         text not null,
  imported_at    timestamptz not null default now(),
  unique (business_id, called_at)
);
create index if not exists outcomes_experiment_idx on outcomes (experiment_id, business_id);
