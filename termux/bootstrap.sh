#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

EXPECTED_BRANCH="agent-brain-mega-v1"
EXPECTED_REPO_SUFFIX="hosseinasgari898989-dev/rubika-agent"

if ! command -v pkg >/dev/null 2>&1; then
  echo "ERROR: This setup script must be run inside Termux."
  exit 2
fi

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$ROOT" ]; then
  echo "ERROR: Run this script from inside a cloned Rubika-Agent repository."
  exit 2
fi
cd "$ROOT"

BRANCH="$(git branch --show-current)"
if [ "$BRANCH" != "$EXPECTED_BRANCH" ]; then
  echo "ERROR: Current branch is '$BRANCH'."
  echo "Switch to '$EXPECTED_BRANCH' before continuing."
  exit 3
fi

ORIGIN="$(git remote get-url origin 2>/dev/null || true)"
case "$ORIGIN" in
  *"$EXPECTED_REPO_SUFFIX"*) ;;
  *)
    echo "ERROR: The configured origin is not the expected Rubika-Agent repository."
    exit 4
    ;;
esac

echo "[1/4] Installing Termux packages..."
pkg update -y
pkg install -y python git ffmpeg

echo "[2/4] Installing project Python dependencies..."
python -m pip install --disable-pip-version-check -r requirements.txt

echo "[3/4] Preparing private local token storage..."
mkdir -p "$HOME/.config/rubika-agent" "$HOME/.local/state/rubika-agent"
chmod 700 "$HOME/.config/rubika-agent" "$HOME/.local/state/rubika-agent"
TOKEN_FILE="$HOME/.config/rubika-agent/apify_token"
if [ -f "$TOKEN_FILE" ]; then
  chmod 600 "$TOKEN_FILE"
  echo "Existing private Apify token file protected."
else
  echo "No Apify token file found. Instagram will stay disabled until you configure it locally."
fi

echo "[4/4] Checking metadata adapters..."
python -m compileall -q agent/discovery termux
python -m unittest discover -s tests -p 'test_termux_sources.py' -v
python -m unittest discover -s tests -p 'test_apify_instagram.py' -v
python -m unittest discover -s tests -p 'test_termux_daily_discovery.py' -v

echo
echo "SETUP OK."
echo "One-time reminder: ensure GitHub push authentication works before using --push."
echo "Daily command from repository root:"
echo "  python -m termux.daily_discovery --push"
echo "This collects metadata only. It does not download videos or publish to Rubika."
