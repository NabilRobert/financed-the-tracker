#!/usr/bin/env bash
# Every 15 minutes: carries out confirmed deletions, then copies the deleted ids out of the
# database and off the server (M1, PR7, T27).
#
# A restore must not bring back anyone who left, and the likeliest reason to restore is
# losing the server, list and all. So the list also goes to the backup bucket, under
# tracker-deletions/, which the 14-day pruning in backup_tracker.sh never reaches. It
# holds person ids and nothing else. Run by cron as the owner account, which reads the list
# through the folder's group, with the same TRACKER_BACKUP_ENV as the backup. A failed run
# pings the heartbeat's /fail, so it alerts.
set -euo pipefail
umask 077

source "$(dirname "$0")/settings.sh"
OPS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LIST_ON_SERVER="$OPS_DIR/deletions/deleted-ids.txt"  # Mounted at /deletions in the cli container.

start_heartbeat "$(require DELETIONS_HEALTHCHECK_URL)"
configure_s3

COMPOSE=(docker compose -f "$OPS_DIR/compose.yaml" --profile tools run --rm cli)
# Deletes everyone who confirmed `hapus semua` (M1). More than two waiting is a hold: it
# deletes none and fails, so the heartbeat alerts, and nothing below runs until Arthur acts.
"${COMPOSE[@]}" deletions-run
"${COMPOSE[@]}" deletions-export /deletions/deleted-ids.txt

# Merge with the bucket's copy before uploading, so the upload can only add ids. A server
# rebuilt from an old backup has a short list, and the bucket may hold deletions it never
# saw. If the bucket can't be listed or read, the script stops rather than upload a list
# that could be shorter.
REMOTE_DIR=":s3:$S3_BUCKET/tracker-deletions"
listing="$(rclone lsf "$REMOTE_DIR" --files-only --include deleted-ids.txt)"
in_bucket=""
if [ -n "$listing" ]; then in_bucket="$(rclone cat "$REMOTE_DIR/deleted-ids.txt" | sort -un)"; fi
on_server="$(cat "$LIST_ON_SERVER")"
merged="$(printf '%s\n%s\n' "$in_bucket" "$on_server" | sed '/^$/d' | sort -un)"
if [ "$merged" != "$in_bucket" ]; then
  printf '%s\n' "$merged" | rclone rcat "$REMOTE_DIR/deleted-ids.txt"
fi

ping_hc ""
