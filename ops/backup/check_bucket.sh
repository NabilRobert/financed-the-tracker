#!/usr/bin/env bash
# The restore drill's checks of the bucket (R18, R19, T45). Run on Arthur's machine with his
# own AWS login, never on the server: the server's key can't see old versions.
#
# A taken-over server can't delete a backup, but it can overwrite one, or overwrite the
# deletion list with a shorter one. Versioning keeps the old copies, so this:
# - writes every id that any copy of the deletion list ever held into one file. The list
#   only ever grows, so an id in any copy is a real deletion;
# - then fails, naming them, if any dump has two copies. Dump names carry the time they
#   were made, so a second copy means someone overwrote it.
#
# Usage: check_bucket.sh <bucket> <file for the merged list>
set -euo pipefail
umask 077

BUCKET="${1:?usage: check_bucket.sh <bucket> <file for the merged list>}"
MERGED_LIST="${2:?usage: check_bucket.sh <bucket> <file for the merged list>}"
LIST_KEY="tracker-deletions/deleted-ids.txt"

# Prints a text listing from `aws --output text` one value per line, without "None".
one_per_line() {
  tr '\t' '\n' | sed '/^None$/d;/^$/d'
}

list_version_ids="$(aws s3api list-object-versions --bucket "$BUCKET" --prefix "$LIST_KEY" \
  --query "Versions[?Key=='$LIST_KEY'].VersionId" --output text | one_per_line)"
if [ -z "$list_version_ids" ]; then
  echo "check_bucket: $BUCKET holds no copy of $LIST_KEY: find the list before restoring" >&2
  exit 1
fi

copies="$(mktemp -d)"
trap 'rm -rf "$copies"' EXIT
copy_number=0
for version_id in $list_version_ids; do
  copy_number=$((copy_number + 1))
  aws s3api get-object --bucket "$BUCKET" --key "$LIST_KEY" --version-id "$version_id" "$copies/$copy_number" >/dev/null
done
cat "$copies"/* | sed '/^$/d' | sort -un > "$MERGED_LIST"
echo "check_bucket: merged $copy_number copies of the deletion list into $MERGED_LIST"

# The trailing slash keeps tracker-deletions/ out of this listing.
overwritten="$(aws s3api list-object-versions --bucket "$BUCKET" --prefix tracker/ \
  --query 'Versions[].Key' --output text | one_per_line | sort | uniq -d)"
if [ -n "$overwritten" ]; then
  echo "check_bucket: these dumps were overwritten, so treat the server as compromised:" >&2
  echo "$overwritten" >&2
  exit 1
fi
echo "check_bucket: every dump has one copy"
