#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

EXPECTED_BRANCH="agent-brain-mega-v1"
EXPECTED_REPO_SUFFIX="hosseinasgari898989-dev/rubika-agent"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

if ! command -v pkg >/dev/null 2>&1; then
  echo "ERROR: Run this command in Termux."
  exit 2
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "ERROR: Run the one-time setup first:"
  echo "  bash termux/bootstrap.sh"
  exit 2
fi

BRANCH="$(git branch --show-current)"
if [ "$BRANCH" != "$EXPECTED_BRANCH" ]; then
  echo "ERROR: Current branch is '$BRANCH'; expected '$EXPECTED_BRANCH'."
  echo "No files were changed or pushed."
  exit 3
fi

ORIGIN="$(git remote get-url origin 2>/dev/null || true)"
case "$ORIGIN" in
  *"$EXPECTED_REPO_SUFFIX"*) ;;
  *)
    echo "ERROR: origin is not the expected Rubika-Agent repository."
    echo "No files were changed or pushed."
    exit 4
    ;;
esac

echo "[1/2] Updating the experimental branch (fast-forward only)..."
if ! git pull --ff-only origin "$EXPECTED_BRANCH"; then
  echo "ERROR: Could not update safely. Fix GitHub authentication or local Git changes, then retry."
  exit 5
fi

echo "[2/2] Discovering public metadata on this phone..."
source ".venv/bin/activate"
python -m termux.daily_discovery --push
