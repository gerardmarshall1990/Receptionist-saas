-- Stage 2: polite crawl of business websites + rule-based signals.

-- The job queue: one row per website (several businesses can share one site;
-- it is crawled once). Workers claim rows with FOR UPDATE SKIP LOCKED.
create table crawl_sites (
  domain           text primary key,
  start_url        text not null,
  status           text not null default 'pending'
                   check (status in ('pending', 'running', 'done', 'blocked', 'robots_disallowed',
                                     'error', 'parked', 'offsite')),
  attempts         integer not null default 0,
  next_attempt_at  timestamptz not null default now(),
  locked_by        text,
  locked_at        timestamptz,
  pages_fetched    integer not null default 0,
  final_url        text,
  detail           text,
  run_id           uuid,
  enqueued_at      timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index crawl_sites_claim_idx on crawl_sites (status, next_attempt_at);

-- One row per fetched page. The HTML itself lives (gzipped) in Parquet under
-- data/crawl/, referenced by parquet_file, so signals can be re-extracted
-- without fetching again.
create table pages (
  id            bigint generated always as identity primary key,
  domain        text not null references crawl_sites (domain) on delete cascade,
  url           text not null,
  final_url     text not null,
  kind          text not null,             -- home, contact, booking, careers, about, services
  http_status   integer not null,
  content_type  text,
  bytes         integer not null,
  content_hash  text,
  fetched_at    timestamptz not null default now(),
  parquet_file  text,
  unique (domain, url)
);

-- Spec section 3: signals per business. Long format: one row per (business, signal).
-- value=false rows record "checked, not found" so absence is distinguishable from unknown.
create table signals (
  business_id        uuid not null references businesses (id) on delete cascade,
  signal             text not null,
  value              boolean not null,
  count              integer,
  evidence           text,
  source_url         text,
  extractor_version  text not null,
  detected_at        timestamptz not null default now(),
  primary key (business_id, signal)
);
create index signals_true_idx on signals (signal, business_id) where value;
