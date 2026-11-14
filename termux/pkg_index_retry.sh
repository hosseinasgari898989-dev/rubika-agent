#!/usr/bin/env sh
# Retry only the Termux package index refresh. Package installs and discovery
# are deliberately left to the caller and keep their existing fail-closed path.
set -eu

attempt=1
last_status=1
while [ "$attempt" -le 3 ]; do
  printf 'TERMUX_PKG_INDEX_FETCH_ATTEMPT=%s/3\n' "$attempt"
  if timeout 180s pkg update -y; then
    echo "TERMUX_PKG_INDEX_FETCH_STATUS=success"
    exit 0
  else
    last_status=$?
    printf 'TERMUX_PKG_INDEX_FETCH_ATTEMPT_FAILED=%s/3\n' "$attempt" >&2
  fi

  if [ "$attempt" -lt 3 ]; then
    case "$attempt" in
      1) delay=2 ;;
      2) delay=4 ;;
    esac
    printf 'TERMUX_PKG_INDEX_FETCH_RETRY_IN_SECONDS=%s\n' "$delay"
    sleep "$delay"
  fi
  attempt=$((attempt + 1))
done

echo "TERMUX_ERROR_CODE=TERMUX_PKG_INDEX_FETCH_FAILED" >&2
exit "$last_status"
