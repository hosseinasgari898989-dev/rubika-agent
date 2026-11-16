#!/usr/bin/env sh
# Retry the complete Termux runtime package install, not just the index refresh.
# Only Python is required inside this metadata-only container; Git auth stays on the host.
set -eu

attempt=1
last_status=1
while [ "$attempt" -le 3 ]; do
  printf 'TERMUX_PKG_INSTALL_ATTEMPT=%s/3\n' "$attempt"
  if timeout 900s pkg install -y python; then
    echo "TERMUX_PKG_INSTALL_STATUS=success"
    exit 0
  else
    last_status=$?
    printf 'TERMUX_PKG_INSTALL_ATTEMPT_FAILED=%s/3\n' "$attempt" >&2
  fi

  if [ "$attempt" -lt 3 ]; then
    case "$attempt" in
      1) delay=2 ;;
      2) delay=4 ;;
    esac
    printf 'TERMUX_PKG_INSTALL_RETRY_IN_SECONDS=%s\n' "$delay"
    sleep "$delay"
  fi
  attempt=$((attempt + 1))
done

echo "TERMUX_ERROR_CODE=TERMUX_PKG_INSTALL_FAILED" >&2
exit "$last_status"
