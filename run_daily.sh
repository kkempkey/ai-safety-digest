#!/bin/bash
# Daily digest runner — invoked by launchd at 07:30 and 08:15.
# `digest run` is idempotent per day, so the 08:15 retry exits immediately
# when the 07:30 run succeeded.
set -u
cd "$(dirname "$0")"
LOG="data/run-$(date +%Y-%m-%d).log"
{
  echo "=== run started $(date) ==="
  ./.venv/bin/python -m digest run
  status=$?
  echo "=== run finished $(date) exit=$status ==="
  exit $status
} >> "$LOG" 2>&1
