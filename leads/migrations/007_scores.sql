-- Stage 4: scores (spec section 3). One row per business per current model.
-- `components` keeps every point added or removed, so any score can be explained.
create table if not exists scores (
  business_id    uuid primary key references businesses (id) on delete cascade,
  pain           numeric(5, 2) not null,
  value          numeric(5, 2) not null,
  reachability   numeric(5, 2) not null,
  fit            numeric(5, 2) not null,
  total          numeric(5, 2) not null,
  tier           char(1) not null check (tier in ('A', 'B', 'C', 'D')),
  dead_reason    text,
  components     jsonb not null,
  model_version  text not null,
  scored_at      timestamptz not null default now()
);
create index if not exists scores_tier_total_idx on scores (tier, total desc);
