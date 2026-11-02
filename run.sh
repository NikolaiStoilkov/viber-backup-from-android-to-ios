#!/bin/bash
# Start Viber Transfer.
#   ./run.sh           start the app (opens http://127.0.0.1:8765 in your browser)
#   ./run.sh --setup   create .venv and install pymobiledevice3 (needed for the iPhone step)
#   ./run.sh --check   list which external tools are installed
set -e
cd "$(dirname "$0")"

if [ "$1" = "--setup" ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
  echo "Setup done. Start the app with ./run.sh"
  exit 0
fi

PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python

check() {
  if command -v "$1" >/dev/null 2>&1 || [ -x "/opt/homebrew/bin/$1" ] || [ -x "/usr/local/bin/$1" ]; then
    echo "  ok       $1"
  else
    echo "  MISSING  $1   ->  $2"
  fi
}
echo "Checking external tools:"
check adb "brew install --cask android-platform-tools"
check idevice_id "brew install libimobiledevice"
check ideviceinfo "brew install libimobiledevice"
check idevicepair "brew install libimobiledevice"
$PY -c "import pymobiledevice3" 2>/dev/null && echo "  ok       pymobiledevice3" \
  || echo "  MISSING  pymobiledevice3   ->  ./run.sh --setup"
[ "$1" = "--check" ] && exit 0

exec $PY -m vibertransfer "$@"
