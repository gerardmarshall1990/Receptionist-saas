#!/usr/bin/env bash
# Stage 7 scale-out loop: crawl best-first in rounds, then refresh signals, scores, gate and export.
# Resumable: safe to stop and restart at any time (the crawl queue and extraction are idempotent).
#
#   LEADS_DATABASE_URL=... scripts/scale_loop.sh [rounds] [minutes_per_round] [min_free_gb]
#
# Stops when the queue is empty, after `rounds` rounds, or when free disk drops below min_free_gb.
set -euo pipefail
cd "$(dirname "$0")/.."

ROUNDS="${1:-100}"
MINUTES="${2:-20}"
MIN_FREE_GB="${3:-6}"

# In the Docker image `leads` is on PATH; locally it runs through uv.
LEADS="${LEADS_CMD:-uv run leads}"
DATA_DIR="${LEADS_DATA_DIR:-data}"
mkdir -p "$DATA_DIR"
free_gb() { df -BG --output=avail "$DATA_DIR" | tail -1 | tr -dc '0-9'; }
pending() {
  psql "$LEADS_DATABASE_URL" -Atc \
    "select count(*) from crawl_sites where status = 'pending' or (status = 'error' and attempts < 3)"
}

for ((i = 1; i <= ROUNDS; i++)); do
  pg_isready -q || { echo "postgres is down; start it and re-run"; exit 1; }
  if (( $(free_gb) < MIN_FREE_GB )); then
    echo "round $i: only $(free_gb) GB free (< $MIN_FREE_GB); stopping before the disk fills"
    break
  fi
  left=$(pending)
  if (( left == 0 )); then echo "queue empty"; break; fi
  echo "round $i: $left sites left, $(free_gb) GB free, $(date -u +%H:%M)"
  # Two workers in parallel (claims use SKIP LOCKED; politeness is per site).
  $LEADS crawl run --max-sites 100000 --minutes "$MINUTES" &
  $LEADS crawl run --max-sites 100000 --minutes "$MINUTES" &
  wait
  $LEADS crawl extract          # incremental: only sites finished since the last pass
  $LEADS score run
  $LEADS compliance check
  $LEADS export run | grep -E '^(export|files:|  (pass|blocked|candidates) )' || true
done
echo "done: $(psql "$LEADS_DATABASE_URL" -Atc "select count(*) from businesses where compliance_status = 'pass'") leads pass the gate"
