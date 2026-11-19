-- When each request was SENT (after the pacing pause). With fetched_at (response
-- fully read) this lets the report measure the real idle gap between two requests
-- to one site: requested_at(next) - fetched_at(previous) >= per_site_delay_s.
alter table pages add column if not exists requested_at timestamptz;
