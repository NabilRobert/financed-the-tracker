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
# WAHA's session files are deliberately not backed up: they are the WhatsApp account (T35).
set -euo pipefail
umask 077

ENV_FILE="${TRACKER_BACKUP_ENV:?set TRACKER_BACKUP_ENV to the backup settings file}"
KEEP=14
PREFIX="tracker"

require() {
  local value
  value="$(grep "^$1=" "$ENV_FILE" | cut -d= -f2- || true)"
  if [ -z "$value" ]; then echo "backup: $1 is missing from $ENV_FILE" >&2; exit 1; fi
  printf '%s' "$value"
}

HC_URL="$(grep '^BACKUP_HEALTHCHECK_URL=' "$ENV_FILE" | cut -d= -f2- || true)"
ping_hc() {
  if [ -n "$HC_URL" ]; then curl -fsS -m 10 --retry 3 "$HC_URL$1" >/dev/null || true; fi
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then ping_hc /fail; fi' EXIT
ping_hc /start

# Validate everything before dumping anything.
DB_URL="$(require TRACKER_MIGRATOR_DATABASE_URL | sed -E 's#^postgresql\+[a-z]+://#postgresql://#')"
AGE_RECIPIENT="$(require AGE_RECIPIENT)"
S3_BUCKET="$(require S3_BUCKET)"
export RCLONE_S3_PROVIDER=AWS
RCLONE_S3_ACCESS_KEY_ID="$(require AWS_ACCESS_KEY_ID)"
RCLONE_S3_SECRET_ACCESS_KEY="$(require AWS_SECRET_ACCESS_KEY)"
RCLONE_S3_REGION="$(require S3_REGION)"
export RCLONE_S3_ACCESS_KEY_ID RCLONE_S3_SECRET_ACCESS_KEY RCLONE_S3_REGION
export RCLONE_S3_NO_CHECK_BUCKET=true  # The IAM policy has no CreateBucket.
REMOTE=":s3:$S3_BUCKET/$PREFIX"
NAME="$PREFIX-$(date -u +%Y%m%dT%H%M%SZ).dump.age"

# pipefail makes a pg_dump failure fail the whole line, so a truncated dump is never uploaded.
pg_dump "$DB_URL" --format=custom | age -r "$AGE_RECIPIENT" | rclone rcat "$REMOTE/$NAME"

# Prune only files this script made, and only after a successful upload.
rclone lsf "$REMOTE" --files-only --include "$PREFIX-*.dump.age" \
  | sort -r | tail -n +$((KEEP + 1)) \
  | xargs -r -I{} rclone deletefile "$REMOTE/{}"

ping_hc ""
