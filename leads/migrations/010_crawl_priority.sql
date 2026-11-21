-- Stage 7: crawl the most promising sites first (category value, usable phone, independent).
alter table crawl_sites add column if not exists priority real not null default 0;
create index if not exists crawl_sites_claim_idx on crawl_sites (status, priority desc, domain);

-- Incremental extraction: a site is re-extracted only when it was crawled again since, or the
-- extractor version changed.
alter table crawl_sites add column if not exists extracted_version text;
alter table crawl_sites add column if not exists extracted_at timestamptz;
