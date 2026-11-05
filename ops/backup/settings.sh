# Shared by backup_tracker.sh and export_deletions.sh: settings, the heartbeat and S3.
# Sourced, never run. Every secret comes from TRACKER_BACKUP_ENV at run time.

ENV_FILE="${TRACKER_BACKUP_ENV:?set TRACKER_BACKUP_ENV to the backup settings file}"

# Prints one setting, or stops the script if it's missing.
require() {
  local value
  value="$(grep "^$1=" "$ENV_FILE" | cut -d= -f2- || true)"
  if [ -z "$value" ]; then echo "$(basename "$0"): $1 is missing from $ENV_FILE" >&2; exit 1; fi
  printf '%s' "$value"
}

# Pings the heartbeat at a path ("", /start or /fail), with an optional message for its log.
ping_hc() {
  if [ -z "$HC_URL" ]; then return; fi
  if [ -n "${2:-}" ]; then
    curl -fsS -m 10 --retry 3 --data-raw "$2" "$HC_URL$1" >/dev/null || true
  else
    curl -fsS -m 10 --retry 3 "$HC_URL$1" >/dev/null || true
  fi
}

# Pings /start now and /fail if the script exits with an error; the script pings "" when done.
start_heartbeat() {
  HC_URL="$1"
  trap 'rc=$?; if [ "$rc" -ne 0 ]; then ping_hc /fail; fi' EXIT
  ping_hc /start
}

# Points rclone at the backup bucket. Sets S3_BUCKET.
configure_s3() {
  S3_BUCKET="$(require S3_BUCKET)"
  export RCLONE_S3_PROVIDER=AWS
  RCLONE_S3_ACCESS_KEY_ID="$(require AWS_ACCESS_KEY_ID)"
  RCLONE_S3_SECRET_ACCESS_KEY="$(require AWS_SECRET_ACCESS_KEY)"
  RCLONE_S3_REGION="$(require S3_REGION)"
  export RCLONE_S3_ACCESS_KEY_ID RCLONE_S3_SECRET_ACCESS_KEY RCLONE_S3_REGION
  export RCLONE_S3_NO_CHECK_BUCKET=true  # The IAM policy has no CreateBucket.
}
