-- Live Google reviews in the explorer (official Places API).
-- Google's policies allow storing the place ID indefinitely; review text, rating and
-- other Places content are NOT stored anywhere: they are fetched live and shown with
-- attribution. api_usage enforces a monthly call cap (default: the free tier).
alter table businesses add column if not exists google_place_id text;

create table if not exists api_usage (
  provider  text not null,
  month     date not null,
  calls     integer not null default 0 check (calls >= 0),
  primary key (provider, month)
);
