#!/bin/bash
# Assemble "Viber Transfer.app" from the SwiftPM build. Called by ../run.sh and ../build-dmg.sh.
#
#   mac-app/build-app.sh <arm64|x86_64> <output dir> [engine dir] [icon.icns]
#
# engine dir: the PyInstaller output (ViberTransferEngine + _internal/), copied to
#             Contents/Resources/engine. Leave it out for development builds, where the app
#             runs the checkout's Python instead (VT_PROJECT_ROOT).
# CODESIGN_IDENTITY (env): Developer ID to sign with; otherwise the app is signed ad hoc.
set -euo pipefail
ARCH="$1"
OUT="$2"
ENGINE="${3:-}"
ICON="${4:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$HERE/../vibertransfer/__init__.py")"

BUILD_ARGS=(-c release --package-path "$HERE" --triple "$ARCH-apple-macosx13.0" --scratch-path "$HERE/.build/$ARCH")
swift build "${BUILD_ARGS[@]}"
BIN="$(swift build "${BUILD_ARGS[@]}" --show-bin-path)/ViberTransfer"

APP="$OUT/Viber Transfer.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/ViberTransfer"
if [ -n "$ICON" ]; then cp "$ICON" "$APP/Contents/Resources/AppIcon.icns"; fi
if [ -n "$ENGINE" ]; then ditto "$ENGINE" "$APP/Contents/Resources/engine"; fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleDisplayName</key><string>Viber Transfer</string>
  <key>CFBundleExecutable</key><string>ViberTransfer</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>com.vibertransfer.mac</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>Viber Transfer</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>Personal backup tool. No network access.</string>
</dict>
</plist>
PLIST

if [ -n "${CODESIGN_IDENTITY:-}" ]; then
  # Inner code first: every Mach-O file of the bundled engine, then the app itself.
  ENT="$HERE/../packaging/engine.entitlements"
  if [ -n "$ENGINE" ]; then
    find "$APP/Contents/Resources/engine" -type f -print0 | while IFS= read -r -d '' f; do
      if file -b "$f" | grep -q Mach-O; then
        codesign --force --options runtime --timestamp --entitlements "$ENT" -s "$CODESIGN_IDENTITY" "$f"
      fi
    done
  fi
  codesign --force --options runtime --timestamp -s "$CODESIGN_IDENTITY" "$APP"
else
  codesign --force -s - "$APP"
fi
echo "Assembled $APP"
