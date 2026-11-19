-- Stage 1: businesses and their contact points (spec section 3).
-- Only kept (not excluded, not duplicate) records are loaded here. The full
-- extract, with exclusion reasons, stays in Parquet under data/.

create table businesses (
  id                    uuid primary key default gen_random_uuid(),
  source                text not null,
  source_id             text not null,
  source_release        text not null,
  name                  text not null,
  name_key              text not null,
  category              text not null,
  subcategory           text not null,
  source_category_code  text not null,
  country               char(2) not null,
  state                 text,
  city                  text,
  address               text,
  postcode              text,
  lat                   double precision,
  lng                   double precision,
  tz                    text,
  phone                 text,            -- primary E.164; unverified until Stage 3
  website               text,
  domain                text,
  staff_estimate        integer,         -- Stage 2
  is_chain              boolean not null default false,
  location_count        integer not null default 1 check (location_count >= 1),
  flags                 text[] not null default '{}',
  source_confidence     double precision,
  source_datasets       text[] not null default '{}',  -- datasets that contributed the record
  licenses              text[] not null check (cardinality(licenses) > 0),
  attribution           text not null,
  merged_source_ids     text[] not null default '{}',
  in_pilot_sample       boolean not null default false,
  first_seen_at         timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  crawled_at            timestamptz,
  unique (source, source_id)
);
create index businesses_category_state_idx on businesses (category, state);
create index businesses_domain_idx on businesses (domain) where domain is not null;
create index businesses_phone_idx on businesses (phone) where phone is not null;
create index businesses_pilot_idx on businesses (category) where in_pilot_sample;

create table phones (
  business_id     uuid not null references businesses (id) on delete cascade,
  e164            text not null,
  sources         text[] not null,
  -- Independent sources that list this number (Overture vs. website vs. lookup).
  -- 1 at Stage 1; the crawl and lookups raise it.
  agree_count     integer not null default 1 check (agree_count >= 1),
  valid_format    boolean not null,
  number_type_guess text,               -- libphonenumber range guess, not a lookup
  line_type       text check (line_type in ('landline', 'mobile', 'voip', 'unknown')),
  carrier         text,
  flags           text[] not null default '{}',
  status          text not null default 'unverified'
                  check (status in ('verified', 'likely', 'unverified', 'invalid')),
  verified_at     timestamptz,
  primary key (business_id, e164)
);
create index phones_e164_idx on phones (e164);

-- One table for every email we know of, with where it came from. Only
-- origin = 'business_site' (found on the business's own site, Stage 2) can
-- ever be exported; source-listed addresses are leads for the crawler to
-- confirm, never contacted as-is.
create table emails (
  business_id         uuid not null references businesses (id) on delete cascade,
  email               text not null,
  origin              text not null check (origin in ('business_site', 'source_listed')),
  source_url          text,
  syntax_ok           boolean not null,
  is_role_based       boolean not null,
  is_freemail         boolean not null,
  domain_matches_site boolean not null,
  flags               text[] not null default '{}',
  mx_ok               boolean,
  deliverability      text check (deliverability in ('valid', 'risky', 'catch_all', 'invalid', 'unknown')),
  verified_at         timestamptz,
  primary key (business_id, email, origin)
);
