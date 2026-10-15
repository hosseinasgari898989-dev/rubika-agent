#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "\${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "\${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="\${HOME}/.config/rubika-agent"
CONFIG_FILE="\${CONFIG_DIR}/interactive.env"

if [[ ! -f "\${CONFIG_FILE}" ]]; then
  printf '%s\n' \
    "تنظیمات خصوصی پیدا نشد:" \
    "\${CONFIG_FILE}" \
    "را طبق docs/TERMUX_INTERACTIVE_AGENT.md بساز؛ توکن‌ها را در مخزن یا چت نگذار."
  exit 2
fi

# The file lives outside the repository and is created by the owner.
# Restrict access before loading it, and never echo its contents.
mkdir -p "\${CONFIG_DIR}"
chmod 700 "\${CONFIG_DIR}"
chmod 600 "\${CONFIG_FILE}"
set -a
# shellcheck disable=SC1090
source "\${CONFIG_FILE}"
set +a

cd "\${REPO_ROOT}"
exec python -m termux.interactive_agent --repo-root "\${REPO_ROOT}" "$@"
