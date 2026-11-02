# Changelog

## 0.1.0 – 2026-11-02

First version.

- Native Mac app (SwiftUI, macOS 13+) with Android and iPhone status cards, Start/Cancel, step
  list, progress bar, plain-language prompts, a sheet for choices, log, end-of-run summary and a
  standard Settings window. It drives a Python engine (`vibertransfer engine`) over stdin/stdout
  JSON lines, with no network socket; quitting cancels a running transfer and cleans up first.
- Android helper APK (Kotlin, no dependencies): asks for root via Magisk/SuperSU, copies Viber's
  app data and media folders to a private staging folder, writes SHA-256 checksums, and reports
  progress via `status.json`/logcat.
- adb pull into `~/Documents/ViberTransfer/backups/<timestamp>/`, verified file by file against the
  phone's checksums, with the staging copy removed afterwards.
- Fallback without root: checks Viber's `allowBackup` flag and the Android 12+ rules, then runs
  `adb backup` and safely unpacks the `.ab` file, or stops with an explanation.
- Converter interface with a Viber SQLite → per-conversation JSON/TXT converter that logs the
  schema it finds and fails with a clear message when it isn't as expected.
- `manifest.json` with device info, Viber version, counts and SHA-256 of every file;
  `python3 -m vibertransfer verify` / `convert` commands.
- iPhone step: copies the readable export into the Documents folder of a chosen File Sharing app
  via pymobiledevice3 and checks file sizes on the phone. Viber's own container is never a target.
- macOS installer: `./build-dmg.sh [--arch arm64|x86_64]` builds a self-contained
  "Viber Transfer.app" (the SwiftUI app plus a PyInstaller-built engine with Python,
  pymobiledevice3 and the helper APK inside) and
  packs it into `dist/ViberTransfer-<version>-<arch>.dmg`. The engine logs to
  `~/Library/Logs/ViberTransfer/engine.log` and has a `selftest` command that the build runs on
  every app it produces. Optional Developer ID signing and notarization.
- Unit tests for manifests, checksums, conversion, parsers, `.ab` extraction, and an end-to-end
  job test against a fake adb, and engine-protocol tests.
