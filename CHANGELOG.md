# Changelog

## 0.1.0 – 2026-11-02

First version.

- Mac app (Python, local web UI on 127.0.0.1) with Android and iPhone status panels, Start/Cancel,
  step list, progress bar, plain-language prompts, log and end-of-run summary.
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
- Unit tests for manifests, checksums, conversion, parsers, `.ab` extraction, and an end-to-end
  job test against a fake adb.
