-- Lead explorer (Stage 1b): columns and indexes so list/filter/sort/search stay
-- fast at ~3M businesses, plus the `reasons` table from spec section 3 that the
-- UI reads ("why this lead") and Stage 2+ writes.

create extension if not exists pg_trgm;

-- Denormalized so the list can filter and show contact status without joins.
alter table businesses
  add column phone_status text not null default 'none'
    check (phone_status in ('none', 'invalid', 'unverified', 'likely', 'verified')),
  add column email_status text not null default 'none'
    check (email_status in ('none', 'source_listed', 'site_found', 'valid', 'risky', 'invalid')),
  add column score numeric(5, 2) check (score between 0 and 100),   -- Stage 4
  add column tier char(1) check (tier in ('A', 'B', 'C', 'D')),     -- Stage 4
  add column search_text text generated always as (
    lower(coalesce(name, '') || ' ' || coalesce(city, '') || ' ' || coalesce(domain, ''))
  ) stored;

-- Search: trigram match on name / city / domain.
create index businesses_search_trgm_idx on businesses using gin (search_text gin_trgm_ops);
-- Flags: "has any of" / "has all of".
create index businesses_flags_idx on businesses using gin (flags);
-- One index per sort order (keyset pagination: sort key + id as tie-breaker).
create index businesses_sort_score_idx on businesses ((coalesce(score, -1)), id);
create index businesses_sort_name_idx on businesses (name_key, id);
create index businesses_sort_place_idx on businesses ((coalesce(state, 'ZZ')), (coalesce(city, '')), id);
create index businesses_sort_locations_idx on businesses (location_count, id);
-- Filter + default sort together, so a rare state/category doesn't walk the whole sort index.
create index businesses_state_score_idx on businesses (state, (coalesce(score, -1)), id);
create index businesses_category_score_idx on businesses (category, (coalesce(score, -1)), id);
-- Common filter columns.
create index businesses_subcategory_idx on businesses (category, subcategory);
create index businesses_contact_idx on businesses (phone_status, email_status);
create index suppression_match_idx on suppression (match_type, match_value);
-- "Callable now" matches (category, tz) pairs computed in the app.
create index businesses_category_tz_idx on businesses (category, tz);

-- "Why this lead": one row per evidence-backed reason (spec section 3/5).
-- Every reason cites a stored signal and the snippet + URL it came from.
create table reasons (
  id           bigint generated always as identity primary key,
  business_id  uuid not null references businesses (id) on delete cascade,
  signal       text not null,
  dimension    text not null check (dimension in ('pain', 'value', 'reachability', 'fit')),
  evidence     text not null check (length(evidence) > 0),
  source_url   text,
  weight       numeric(6, 3) not null default 0,
  stage        text not null,
  created_at   timestamptz not null default now(),
  unique (business_id, signal, evidence)
);
create index reasons_business_idx on reasons (business_id);
