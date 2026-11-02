#!/bin/bash
# Development: build the native Mac app and run it against this checkout's Python code.
#   ./run.sh           build mac-app/ and open the app (its engine is .venv/bin/python -m vibertransfer engine)
#   ./run.sh --setup   create .venv and install pymobiledevice3 (needed for the iPhone step)
#   ./run.sh --check   list which external tools are installed
# For an installable app use ./build-dmg.sh instead.
set -e
cd "$(dirname "$0")"

if [ "$1" = "--setup" ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
  echo "Setup done. Start the app with ./run.sh"
  exit 0
fi

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
check swift "xcode-select --install"
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
$PY -c "import pymobiledevice3" 2>/dev/null && echo "  ok       pymobiledevice3" \
  || echo "  MISSING  pymobiledevice3   ->  ./run.sh --setup"
[ "$1" = "--check" ] && exit 0

ICON=""
if $PY -c "import PIL" 2>/dev/null; then
  rm -rf build/dev/icon.iconset && mkdir -p build/dev
  $PY packaging/make_icon.py build/dev/icon.iconset && iconutil -c icns build/dev/icon.iconset -o build/dev/AppIcon.icns
  ICON=build/dev/AppIcon.icns
fi
mac-app/build-app.sh "$(uname -m)" build/dev "" "$ICON"
# Started from the terminal so the engine uses this checkout; Ctrl+C or ⌘Q quits.
VT_PROJECT_ROOT="$PWD" exec "build/dev/Viber Transfer.app/Contents/MacOS/ViberTransfer"
