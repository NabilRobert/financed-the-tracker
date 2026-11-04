#!/usr/bin/env bash
# Nightly encrypted backup of the tracker's database, kept 14 days (PR4, PR6, T25).
#
# Adapted from MCP-backbone's deploy/backup_bookkeeping.sh (commit 194e9f8), with two
# changes the tracker needs:
# - nothing readable touches the disk: pg_dump streams straight into age;
# - the age recipient comes from the environment, so rotating the key is a config change.
# The private key never comes near the server. The consent notice says "14 hari": change
# both together.
#
# Run by cron as the owner account. Every secret comes from TRACKER_BACKUP_ENV at run time.
# Only files named tracker-*.dump.age in tracker/ are pruned, so the deletion list in
# tracker-deletions/ (export_deletions.sh) is never touched.
# WAHA's session files are deliberately not backed up: they are the WhatsApp account (T35).
set -euo pipefail
umask 077

source "$(dirname "$0")/settings.sh"
KEEP=14
PREFIX="tracker"

start_heartbeat "$(grep '^BACKUP_HEALTHCHECK_URL=' "$ENV_FILE" | cut -d= -f2- || true)"

# Validate everything before dumping anything.
DB_URL="$(require TRACKER_MIGRATOR_DATABASE_URL | sed -E 's#^postgresql\+[a-z]+://#postgresql://#')"
AGE_RECIPIENT="$(require AGE_RECIPIENT)"
configure_s3
REMOTE=":s3:$S3_BUCKET/$PREFIX"
NAME="$PREFIX-$(date -u +%Y%m%dT%H%M%SZ).dump.age"

# pipefail makes a pg_dump failure fail the whole line, so a truncated dump is never uploaded.
pg_dump "$DB_URL" --format=custom | age -r "$AGE_RECIPIENT" | rclone rcat "$REMOTE/$NAME"

# Prune only files this script made, and only after a successful upload.
rclone lsf "$REMOTE" --files-only --include "$PREFIX-*.dump.age" \
  | sort -r | tail -n +$((KEEP + 1)) \
  | xargs -r -I{} rclone deletefile "$REMOTE/{}"

ping_hc ""
