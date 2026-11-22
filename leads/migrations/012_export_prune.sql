-- Stage 7: export retention. Scheduled refreshes write a full export every few minutes; pruning
-- drops the per-lead items and data files of old CLI exports but keeps the header row (who, when,
-- counts, config hash) and manifest.json, so the audit log still lists every export.
alter table exports add column if not exists pruned_at timestamptz;
