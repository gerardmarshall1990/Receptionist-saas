# Deploying the leads engine on a lasting host

The cloud session that built this runs in a temporary container: Postgres stops on every restart, the disk
holds about the best 200k crawled sites, and anything not in git disappears when the container is reclaimed.
A small VM with this stack removes all three limits.

## What you need

| | Minimum | Why |
|---|---|---|
| VM | 4 vCPU, 16 GB RAM | crawler (2 workers x 32 sites), extraction batches, Postgres |
| Disk | 200 GB SSD | saved pages ~70 KB per site (~35 GB for 450k US sites), Postgres ~10 GB at 726k businesses |
| OS | Ubuntu 24.04 + Docker Engine with the compose plugin | |
| Network | outbound HTTPS; inbound only 443 through a TLS reverse proxy | the explorer is never exposed as plain HTTP |

Cost is the VM only: the pipeline uses open data and no paid APIs (`LEADS_DRY_RUN=true`, budget `0`).

## First start

```bash
git clone <this repo> && cd Receptionist-saas/leads
cp deploy/.env.example deploy/.env          # set long random POSTGRES_PASSWORD and LEADS_UI_TOKEN
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d db migrate
```

Build the universe (open Overture data, $0, about 5 minutes), then re-create the pilot experiment from its
committed record:

```bash
C=plumbing,hvac,electrical,roofing_restoration,dental,law_small,auto_repair
run() { docker compose -f deploy/docker-compose.yml --env-file deploy/.env run --rm migrate "$@"; }
run universe extract --categories $C
run universe build --categories $C
run universe load --all --categories $C
run experiment restore pilot1               # same arms and outcomes as experiments/pilot1/
run crawl enqueue --all                     # every website, best first
```

Then start the explorer and the crawl loop:

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d explorer crawler
docker compose -f deploy/docker-compose.yml logs -f crawler     # rounds, sites left, free disk, export counts
```

The crawler service runs `scripts/scale_loop.sh`, in 20-minute crawl rounds, each followed by:
1. incremental extraction
2. scoring
3. the compliance gate
4. a fresh export under the `lead-data` volume

It restarts by itself and resumes where it stopped.

## Explorer access

The explorer listens on `127.0.0.1:8765` and requires `LEADS_UI_TOKEN`. Put a TLS reverse proxy in front.
With Caddy, this `Caddyfile` is enough (replace the domain):

```
leads.example.com {
  reverse_proxy 127.0.0.1:8765
}
```

## Backups and the suppression list

Opt-outs are permanent and live in Postgres, so back the database up daily:

```bash
docker compose -f deploy/docker-compose.yml exec -T db pg_dump -U leads -Fc frontdesk_leads > leads-$(date +%F).dump
```

Commit `experiments/<name>/` after every outcomes import (the loop doesn't do this for you).

## Not tested here

This session has no Docker daemon. The compose file validates (`docker compose config`), but the image build
and the services haven't been run here. Expect small fixes on the first build.
