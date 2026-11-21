-- 010 meant to add a best-first claim index, but its name collided with 004's
-- crawl_sites_claim_idx (status, next_attempt_at), so `if not exists` skipped it.
create index if not exists crawl_sites_priority_idx on crawl_sites (status, priority desc, domain);
