#!/usr/bin/env bash
# Nightly encrypted backup of the tracker's database (PR4, PR6, T25, T45).
#
# Adapted from MCP-backbone's deploy/backup_bookkeeping.sh (commit 194e9f8), with three
# changes the tracker needs:
# - nothing readable touches the disk: pg_dump streams straight into age;
# - the age recipient comes from the environment, so rotating the key is a config change;
# - it never deletes anything in the bucket. Its key can't (s3-backup-key-policy.json), so
#   a taken-over server can't destroy the backups (L2). The bucket's lifecycle rule
#   (s3-lifecycle.json) expires old dumps instead, inside the notice's "14 hari".
# The private key never comes near the server.
#
# Run by cron as the owner account. Every secret comes from TRACKER_BACKUP_ENV at run time.
# It also runs the nightly purge of expired records, so retention shares the heartbeat.
# WAHA's session files are deliberately not backed up: they are the WhatsApp account (T35).
set -euo pipefail
umask 077

source "$(dirname "$0")/settings.sh"
OPS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="tracker"

start_heartbeat "$(grep '^BACKUP_HEALTHCHECK_URL=' "$ENV_FILE" | cut -d= -f2- || true)"

# Validate everything before dumping anything. The URL carries no password: pg_dump reads
# it from a passfile, because any local user can read a command line in `ps` (L1).
DB_URL="$(require BACKUP_DATABASE_URL)"
case "$DB_URL" in
  *://*:*@* | *password=*) echo "backup: BACKUP_DATABASE_URL holds a password: move it to BACKUP_PGPASSFILE" >&2; exit 1 ;;
esac
PGPASSFILE="$(require BACKUP_PGPASSFILE)"
export PGPASSFILE
AGE_RECIPIENT="$(require AGE_RECIPIENT)"
configure_s3
REMOTE=":s3:$S3_BUCKET/$PREFIX"
NAME="$PREFIX-$(date -u +%Y%m%dT%H%M%SZ).dump.age"

# pipefail makes a pg_dump failure fail the whole line, so a truncated dump is never uploaded.
pg_dump "$DB_URL" --format=custom | age -r "$AGE_RECIPIENT" | rclone rcat "$REMOTE/$NAME"

# Retention (PR6) runs here, under this heartbeat, so a purge that stops running alerts.
# After the backup, so a failing purge never costs a night's backup; the script still fails.
docker compose -f "$OPS_DIR/compose.yaml" --profile tools run --rm cli purge

ping_hc ""
