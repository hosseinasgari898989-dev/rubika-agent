#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

EXPECTED_BRANCH="agent-brain-mega-v1"
EXPECTED_REPO_SUFFIX="hosseinasgari898989-dev/rubika-agent"

if ! command -v pkg >/dev/null 2>&1; then
  echo "ERROR: Run this setup script in Termux."
  exit 2
fi

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$ROOT" ]; then
  echo "ERROR: Clone Rubika-Agent and run this script from inside that repository."
  exit 2
fi
cd "$ROOT"

BRANCH="$(git branch --show-current)"
if [ "$BRANCH" != "$EXPECTED_BRANCH" ]; then
  echo "ERROR: Current branch is '$BRANCH'."
  echo "Run: git switch $EXPECTED_BRANCH"
  exit 3
fi

ORIGIN="$(git remote get-url origin 2>/dev/null || true)"
case "$ORIGIN" in
  *"$EXPECTED_REPO_SUFFIX"*) ;;
  *)
    echo "ERROR: origin is not the expected Rubika-Agent repository."
    exit 4
    ;;
esac

echo "[1/4] Installing Termux tools..."
pkg update -y
pkg install -y python git gh ffmpeg

echo "[2/4] Preparing a lightweight virtual environment..."
if [ ! -x ".venv/bin/python" ]; then
  python -m venv .venv
fi
source ".venv/bin/activate"
python -m pip install --disable-pip-version-check requests yt-dlp==2026.08.19

echo "[3/4] Checking GitHub authentication (no token is printed)..."
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  gh auth setup-git
  echo "GitHub CLI authentication is available."
else
  echo "GitHub push requires a one-time login:"
  echo "  gh auth login"
  echo "  gh auth setup-git"
fi

echo "[4/4] Checking the free metadata-only collector..."
python -m compileall -q agent/discovery termux
python -m unittest discover -s tests -p 'test_termux_sources.py' -v
python -m unittest discover -s tests -p 'test_termux_instagram.py' -v
python -m unittest discover -s tests -p 'test_termux_daily_discovery.py' -v
python -m unittest discover -s tests -p 'test_phone_report_integration.py' -v

echo
echo "SETUP OK."
echo "No paid API, Apify token, login cookie, video download, or Rubika post is used."
echo "Authenticate GitHub once if needed, then the daily command is:"
echo "  bash termux/daily.sh"
