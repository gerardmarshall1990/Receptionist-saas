#!/usr/bin/env bash
# Stage 7 scale-out: crawl continuously (best first) while signals, scores, the gate and the
# export are refreshed every few minutes. Resumable: stop and restart at any time.
#
#   LEADS_DATABASE_URL=... scripts/pipeline.sh [minutes_between_refreshes] [min_free_gb]
#
# Crawl workers stop when the queue is empty or free disk drops below min_free_gb; a final
# refresh then runs. Pages are written to disk before a site is marked done, so extraction
# can safely run while the crawl continues.
set -euo pipefail
cd "$(dirname "$0")/.."

MINUTES="${1:-20}"
MIN_FREE_GB="${2:-6}"
WORKERS="${CRAWL_WORKERS:-2}"

# In the Docker image `leads` is on PATH; locally it runs through uv.
LEADS="${LEADS_CMD:-uv run leads}"
DATA_DIR="${LEADS_DATA_DIR:-data}"
mkdir -p "$DATA_DIR"
free_gb() { df -BG --output=avail "$DATA_DIR" | tail -1 | tr -dc '0-9'; }
pending() {
  psql "$LEADS_DATABASE_URL" -Atc \
    "select count(*) from crawl_sites where status = 'pending' or (status = 'error' and attempts < 3)"
}

crawl_worker() {
  while (( $(free_gb) >= MIN_FREE_GB )) && (( $(pending) > 0 )); do
    $LEADS crawl run --max-sites 100000 --minutes 30 || sleep 30
  done
  echo "crawl worker stopping: $(pending) sites left, $(free_gb) GB free"
}

refresh() {
  echo "refresh at $(date -u +%H:%M): $(pending) sites left, $(free_gb) GB free"
  $LEADS crawl extract          # incremental: only sites finished since the last pass
  $LEADS score run
  $LEADS compliance check
  $LEADS export run | grep -E '^(export|files:|  (pass|blocked|candidates) )' || true
}

pg_isready -q || { echo "postgres is down; start it and re-run"; exit 1; }
pids=()
for ((w = 1; w <= WORKERS; w++)); do crawl_worker & pids+=($!); done
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT

running() { for p in "${pids[@]}"; do kill -0 "$p" 2>/dev/null && return 0; done; return 1; }
while running; do
  sleep $((MINUTES * 60))
  refresh
done
refresh
echo "done: $(psql "$LEADS_DATABASE_URL" -Atc "select count(*) from businesses where compliance_status = 'pass'") leads pass the gate"
