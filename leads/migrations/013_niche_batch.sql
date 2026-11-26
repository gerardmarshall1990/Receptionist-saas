-- Tier 1 niche pilot: batches of newly added niches, a "pending" compliance status for them,
-- and protected exports (never pruned).
alter table businesses add column if not exists niche_batch text;
create index if not exists businesses_niche_batch_idx on businesses (niche_batch) where niche_batch is not null;

-- pending = passed every current gate check, but DNC / line type / country rules are not checked
-- yet for this batch. Never exported as a pass.
alter table businesses drop constraint if exists businesses_compliance_status_check;
alter table businesses add constraint businesses_compliance_status_check
  check (compliance_status in ('pass', 'blocked', 'pending'));
alter table export_items drop constraint if exists export_items_status_check;
alter table export_items add constraint export_items_status_check
  check (status in ('pass', 'blocked', 'pending'));
alter table export_items drop constraint if exists export_items_check;
alter table export_items add constraint export_items_row_check
  -- pass rows carry the exported record, blocked rows never do; pending rows carry it only in a
  -- merged export (`export merge`), not in `export run`.
  check ((status = 'pass' and row is not null) or (status = 'blocked' and row is null)
         or status = 'pending');

alter table exports add column if not exists protected boolean not null default false;
