#!/usr/bin/env bash
# Refuses a target only a parent of the newest timeline holds (backup_info.py check-fork).
# Both the live restore and the drill follow that timeline, so both would stop at the fork.
#
#   scripts/ops/backup_check_fork.sh <target> <info json> <command that runs pgbackrest>
set -euo pipefail

target="$1"
info="$2"
shift 2
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"

archive_id="$(python3 "$SCRIPTS/backup_info.py" archive-id <<< "$info")"
[ -n "$archive_id" ] || exit 0
listing="$("$@" --stanza=main --output=json --filter='\.history$' repo-ls "archive/main/$archive_id")"
newest="$(python3 "$SCRIPTS/backup_info.py" newest-history "$listing")"
[ -n "$newest" ] || exit 0
"$@" --stanza=main repo-get "archive/main/$archive_id/$newest" \
  | python3 "$SCRIPTS/backup_info.py" check-fork "$target" "$listing"
