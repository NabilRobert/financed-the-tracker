#!/usr/bin/env bash
# Every 15 minutes: copies deleted ids out of the database, then off the server (PR7, T27).
#
# A restore must not bring back anyone who left, and the likeliest reason to restore is
# losing the server, list and all. So the list also goes to the backup bucket, under
# tracker-deletions/, which the 14-day pruning in backup_tracker.sh never reaches. It
# holds person ids and nothing else. Run by cron as the owner account, with the same
# TRACKER_BACKUP_ENV as the backup. A failed run pings the heartbeat's /fail, so it alerts.
set -euo pipefail
umask 077

source "$(dirname "$0")/settings.sh"
OPS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LIST_ON_SERVER="$OPS_DIR/deletions/deleted-ids.txt"  # Mounted at /deletions in the cli container.

start_heartbeat "$(require DELETIONS_HEALTHCHECK_URL)"
configure_s3

docker compose -f "$OPS_DIR/compose.yaml" --profile tools run --rm cli deletions-export /deletions/deleted-ids.txt
# --checksum: upload only when the ids have changed, not on every run.
rclone copyto --checksum "$LIST_ON_SERVER" ":s3:$S3_BUCKET/tracker-deletions/deleted-ids.txt"

ping_hc ""
