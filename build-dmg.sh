#!/bin/bash
# Build "Viber Transfer.app" (native SwiftUI app + bundled Python engine) and wrap it in a .dmg.
#
#   ./build-dmg.sh                  build for this Mac's architecture
#   ./build-dmg.sh --arch x86_64    Intel build (on Apple Silicon this runs under Rosetta)
#   ./build-dmg.sh --arch arm64     Apple Silicon build
#
# Output: dist/ViberTransfer-<version>-<arch>.dmg
#
# The app embeds its own Python, pymobiledevice3 and (if built) the helper APK, so the
# target Mac needs no Python. Building needs the Xcode Command Line Tools (swift). adb and libimobiledevice are still installed with Homebrew.
# One build per architecture: several pymobiledevice3 dependencies ship single-arch wheels.
#
# Optional, for distributing to other Macs without Gatekeeper warnings:
#   CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#   NOTARY_PROFILE=<profile created with `xcrun notarytool store-credentials`>
set -euo pipefail
cd "$(dirname "$0")"

ARCH="$(uname -m)"
while [ $# -gt 0 ]; do
  case "$1" in
    --arch) ARCH="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 2 ;;
  esac
done
case "$ARCH" in arm64|x86_64) ;; *) echo "--arch must be arm64 or x86_64"; exit 2 ;; esac
if ! arch "-$ARCH" /usr/bin/true 2>/dev/null; then
  echo "This Mac can't run $ARCH code. For x86_64 on Apple Silicon: softwareupdate --install-rosetta"
  exit 1
fi
RUN=(arch "-$ARCH")

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' vibertransfer/__init__.py)"
BUILD="build/$ARCH"
VENV="build/venv-$ARCH"
DMG="dist/ViberTransfer-$VERSION-$ARCH.dmg"
mkdir -p "$BUILD" dist

echo "==> Python environment ($ARCH)"
[ -x "$VENV/bin/python" ] || "${RUN[@]}" /usr/bin/python3 -m venv "$VENV"
"${RUN[@]}" "$VENV/bin/python" -m pip install -q --upgrade pip
# --prefer-binary: take the newest release that has a wheel for this arch instead of compiling an
# sdist (e.g. cryptography has no Intel wheel for every release, and a local Rust toolchain on an
# Apple Silicon Mac would then build arm64 code into the Intel app).
"${RUN[@]}" "$VENV/bin/python" -m pip install -q --prefer-binary -r requirements.txt -r requirements-build.txt
WRONG="$(find "$VENV/lib" \( -name '*.so' -o -name '*.dylib' \) -print0 | while IFS= read -r -d '' f; do
  lipo -archs "$f" 2>/dev/null | grep -qw "$ARCH" || echo "    $f ($(lipo -archs "$f" 2>/dev/null))"; done)"
if [ -n "$WRONG" ]; then
  echo "These native libraries have no $ARCH code, so the app would crash on launch:"
  echo "$WRONG"
  echo "Delete $VENV and run again; if it persists, pin an older version of that package."
  exit 1
fi

echo "==> Unit tests"
"${RUN[@]}" "$VENV/bin/python" -m unittest discover -s tests -q 2>&1 | tail -3

echo "==> Icon"
rm -rf "$BUILD/icon.iconset"
"${RUN[@]}" "$VENV/bin/python" packaging/make_icon.py "$BUILD/icon.iconset"
iconutil -c icns "$BUILD/icon.iconset" -o "$BUILD/ViberTransfer.icns"

echo "==> Helper APK"
APK=android-helper/app/build/outputs/apk/debug/app-debug.apk
if [ -f "$APK" ]; then
  cp "$APK" "$BUILD/viber-transfer-helper.apk"
  export VT_APK="$PWD/$BUILD/viber-transfer-helper.apk"
  echo "    bundling $APK"
else
  export VT_APK=""
  echo "    WARNING: $APK not found (run ./build-apk.sh first)."
  echo "    The app will be built without it and can only use the no-root fallback."
fi

echo "==> Building the engine (Python + pymobiledevice3)"
export VT_ARCH="$ARCH" VT_CODESIGN_IDENTITY="${CODESIGN_IDENTITY:-}"
rm -rf "$BUILD/dist"
"${RUN[@]}" "$VENV/bin/pyinstaller" --noconfirm --clean --log-level WARN \
  --distpath "$BUILD/dist" --workpath "$BUILD/work" packaging/ViberTransfer.spec

echo "==> Building the native app (SwiftUI)"
mac-app/build-app.sh "$ARCH" "$BUILD/dist" "$BUILD/dist/engine" "$BUILD/ViberTransfer.icns"
APP="$BUILD/dist/Viber Transfer.app"

echo "==> Self-test of the built app"
"${RUN[@]}" "$APP/Contents/Resources/engine/ViberTransferEngine" selftest
lipo -archs "$APP/Contents/MacOS/ViberTransfer" | grep -qw "$ARCH" || { echo "app binary is not $ARCH"; exit 1; }
codesign --verify --strict "$APP"
echo "    signature: $(codesign -dv "$APP" 2>&1 | grep -E '^Signature' || echo 'Signature=adhoc')"

echo "==> Disk image"
STAGE="$BUILD/dmg-root"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp packaging/dmg-readme.txt "$STAGE/Read Me First.txt"
hdiutil create -quiet -volname "Viber Transfer $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG"

if [ -n "${CODESIGN_IDENTITY:-}" ]; then
  codesign --force --timestamp -s "$CODESIGN_IDENTITY" "$DMG"
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    echo "==> Notarizing (this can take a few minutes)"
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
  fi
fi
hdiutil verify -quiet "$DMG"

echo
echo "Built $DMG ($(du -h "$DMG" | cut -f1))"
echo "sha256 $(shasum -a 256 "$DMG" | cut -d' ' -f1)"
