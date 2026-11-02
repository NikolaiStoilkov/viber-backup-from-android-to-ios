# Viber Transfer

A small Mac app that backs up Viber from your own Samsung/Android phone over USB, verifies the
copy, converts the chats into readable files and copies them to your iPhone.

**Read this first:** the app can back up everything Viber stores on a **rooted** Android phone,
but it **cannot make those chats appear inside Viber on the iPhone**. iOS doesn't let a Mac write
into another app's private storage, and Viber publishes no import format. On the iPhone you get
a browsable archive of your chats and attachments in the Files app. See [Limitations](#limitations).

---

## How it works

```
 Android phone (USB, adb)                  Mac                                   iPhone (USB)
 ┌──────────────────────────┐   adb pull   ┌──────────────────────────────┐  house_arrest/AFC  ┌──────────────────────┐
 │ Viber Transfer Helper    │ ───────────▶ │ ~/Documents/ViberTransfer/   │ ─────────────────▶ │ <file-sharing app>/  │
 │  su → copy.sh copies     │              │   backups/<timestamp>/       │                    │  Documents/          │
 │  /data/data/com.viber.voip│              │   manifest.json raw/ media/ │                    │  ViberTransfer-<ts>/ │
 │  + media → staging       │              │   chats/                     │                    │  chats/ media/       │
 └──────────────────────────┘              └──────────────────────────────┘                    └──────────────────────┘
```

Steps (all shown in the app's window, with a log and plain-language prompts):

1. **Check tools.** Finds `adb`, or prints the install command if it's missing.
2. **Connect Android phone.** `adb devices -l`. If the phone shows *unauthorized*, the app asks
   you to tap **Allow** on the phone.
3. **Root access.** Installs the helper APK (`adb install -r`), stops Viber (`am force-stop`, so
   its database isn't changing during the copy) and opens the helper. You tap **Start copy** on
   the phone, then **Grant** in the Magisk/SuperSU prompt.
4. **Copy on phone.** The helper runs [`copy.sh`](android-helper/app/src/main/res/raw/copy.sh) as
   root. It copies Viber's app data (everything except caches) and Viber's media folders into
   `/data/local/tmp/vibertransfer_staging`, computes SHA-256 for every file, and hands the folder
   to the adb user. Progress goes to `status.json`, which the Mac polls.
5. **Pull.** `adb pull` into the timestamped backup folder.
6. **Verify.** Every pulled file is checked against the checksums computed on the phone. Then the
   staging copy on the phone is deleted.
7. **Convert.** The SQLite schema is inspected and logged, then exported per conversation to
   `chats/`.
8. **Manifest.** `manifest.json` records device, Viber version, counts and SHA-256 of every file.
9. **Copy to iPhone.** Copies `chats/`, `media/` and `manifest.json` into the Documents folder of
   an iPhone app you choose (one with File Sharing), then checks every file's size on the phone.
10. **Summary.** What was backed up, sizes, where it was saved, and whether the iPhone step worked.

**No root?** The app checks whether Viber allows `adb backup` (the manifest's `allowBackup` flag,
plus Android 12+ rules). If it does, it runs `adb backup` and unpacks the `.ab` file. If not, it
stops and explains why. Current Viber versions opt out of backup, so in practice root is needed.

## Design choices

- **Python with a local web UI, not Swift/SwiftUI.** This was a deliberate change from the
  suggested Swift app. The iPhone side needs pymobiledevice3, which is a Python library, so a Swift
  app would still have to bundle and drive Python. Staying in Python means one language, only the
  standard library apart from that (`http.server`, `sqlite3`, `subprocess`), no Xcode project, and
  everything can be unit-tested without devices. The UI is a single page served on `127.0.0.1`
  only. Requests need a random per-launch token and a localhost `Host` header, so other websites
  can't drive the app.
- **pymobiledevice3 instead of ifuse.** `ifuse` needs macFUSE, a kernel extension that is painful
  to install on Apple Silicon. pymobiledevice3 talks to the same `house_arrest`/AFC services
  directly. libimobiledevice (`idevice_id`, `ideviceinfo`, `idevicepair`) is still used for
  detection and trust checks.
- **Staging in `/data/local/tmp`, not `/sdcard`.** Shared storage is readable by any app with
  storage permission. `/data/local/tmp` is readable only by adb and root, and on Android 11+ it
  also avoids the FUSE layer that hides other apps' folders.
- **The user taps "Start copy" on the phone.** The helper's screen can be opened over adb, but
  nothing is copied without a tap on the phone and a root grant.

## Project layout

```
run.sh                 start the app (./run.sh --setup installs pymobiledevice3 into .venv)
build-apk.sh           build the Android helper APK
requirements.txt       pymobiledevice3 (only needed for the iPhone step)
vibertransfer/
  __main__.py          CLI: start UI, `verify <folder>`, `convert <folder>`
  server.py            local HTTP server + JSON API
  ui.html              the single window (device panels, Start/Cancel, prompts, progress, log, summary)
  job.py               runs the steps in order; cancel + cleanup
  tools.py             find adb/libimobiledevice, install hints, settings, cancellable subprocess runner
  android.py           adb wrapper, output parsers, adb-backup (.ab) extraction
  backup.py            folder layout, SHA-256, manifest.json
  convert.py           Converter interface + Viber SQLite → JSON/TXT converter
  ios.py               iPhone detection and the copy to an app's Documents folder
android-helper/        Kotlin helper app (one screen + one foreground service, no dependencies)
  app/src/main/res/raw/copy.sh   the root copy script
tests/                 unit tests + a fake adb for an end-to-end job test
```

External tools: `adb` (Android platform-tools), `idevice_id` / `ideviceinfo` / `idevicepair`
(libimobiledevice), the `pymobiledevice3` Python package. To build the APK you also need JDK 17,
the Android SDK (platform 35) and Gradle, used once to create the wrapper.

## Prerequisites

### On the Mac

```bash
brew install --cask android-platform-tools
```

```bash
brew install libimobiledevice
```

```bash
./run.sh --setup
```

`./run.sh --setup` creates `.venv/` and installs pymobiledevice3. Python 3.9 or newer is enough;
the one that ships with macOS works. `./run.sh --check` lists what's installed.

To build the helper APK:

```bash
brew install openjdk@17 gradle
```

```bash
brew install --cask android-commandlinetools
```

```bash
sdkmanager "platforms;android-35" "build-tools;35.0.0"
```

(Or install Android Studio and use its SDK Manager. `build-apk.sh` looks in `$ANDROID_HOME` and
`~/Library/Android/sdk`.)

### On the Samsung phone

1. **Developer options:** Settings › About phone › Software information › tap **Build number**
   seven times and enter your PIN.
2. **USB debugging:** Settings › Developer options › turn on **USB debugging**.
3. Connect by USB. When the phone asks **Allow USB debugging?**, tick *Always allow from this
   computer* and tap **Allow**.
4. **Root (Magisk or similar).** The phone must *already* be rooted. **Important:** on Samsung,
   unlocking the bootloader to install Magisk **factory-resets the phone, which erases your Viber
   chats**. It also permanently trips Knox, which disables Samsung Pay, Secure Folder and similar
   features, and many US/Canadian models can't be unlocked at all. If your phone isn't rooted
   today, rooting it is not a way to save its current Viber data.
5. In Magisk › Superuser, allow **Viber Transfer Helper** when asked (the app prompts you at the
   right moment).

### On the iPhone

1. Connect by USB, unlock it, tap **Trust** on *Trust This Computer?* and enter your passcode.
   (`idevicepair validate` should print SUCCESS.)
2. Install an app that accepts files over USB, i.e. one that appears under Finder › your iPhone ›
   **Files**. Many file manager and document reader apps qualify. The app lists every eligible app
   and lets you choose one; Viber itself is never offered.

## Build and run

```bash
./build-apk.sh
```

```bash
./run.sh
```

`./run.sh` opens `http://127.0.0.1:8765` in your browser. Check that both device panels say
*Connected* and press **Start Transfer**. Optional settings (adb path, phone serial when several
are connected, helper APK path, target iPhone app, skip iPhone step) are under **Settings & tools**
and saved in `~/Library/Application Support/ViberTransfer/settings.json`.

Other commands:

```bash
python3 -m vibertransfer verify ~/Documents/ViberTransfer/backups/<folder>
```

```bash
python3 -m vibertransfer convert ~/Documents/ViberTransfer/backups/<folder>
```

`verify` re-hashes every file against `manifest.json`. `convert` re-runs the chat conversion,
for example after adjusting the column names in `convert.py`.

### Tests

```bash
python3 -m unittest discover -s tests
```

No devices are needed. The tests cover manifest creation and verification, checksum parsing and
post-pull verification, the converter (including rows still in the SQLite WAL and an unexpected
schema), adb/dumpsys/idevice output parsing, `.ab` extraction (compressed, uncompressed,
encrypted, empty, path traversal), and a full transfer job against `tests/fake_adb.py`.

## Backup folder

`~/Documents/ViberTransfer/backups/2026-11-02_09-30-00/`

| Path | What it is |
|---|---|
| `manifest.json` | format version, tool version, time, method (`root`/`adb-backup`), source device (model, Android version, serial), Viber package/version, file/byte counts per folder, conversation/message counts, how the transfer was verified, and `files`: SHA-256 + size of **every other file** |
| `raw/` | untouched copy of Viber's private app data: `databases/` (incl. `-wal`/`-shm`), `shared_prefs/`, `files/`, … (caches excluded) |
| `media/` | Viber's attachment folders from shared storage, kept in their original structure (`Android_data_files/`, `Android_media/`, `Viber/`) |
| `chats/` | `index.json` plus `<id>_<name>.json` and `<id>_<name>.txt` per conversation |
| `schema_report.txt` | every table and column found in every database |
| `device_checksums.sha256`, `device_inventory.json` | checksums computed on the phone and the list of source folders found (root method only) |

Each message in `chats/*.json` has `id`, `time` (ISO 8601, UTC), `timestamp_raw`, `sender`,
`direction` (`sent`/`received`/null), `direction_raw`, `text`, and when present `media_uri` plus
`media_file` (a path in `media/` matched by file name).

### About the converter

Viber doesn't document its database, and it changes between versions. The converter doesn't
assume a schema. It records every table and column (log + `schema_report.txt`), looks for a
`messages` table using the candidate column names at the top of
[`vibertransfer/convert.py`](vibertransfer/convert.py), and stops with an explanation if the
required columns (`id`, `conversation_id`, `timestamp`) aren't found. `raw/` is still complete
in that case: edit the candidates and run `python3 -m vibertransfer convert <folder>`. The
`sent`/`received` labels come from an observed convention. The log prints how often each raw
value occurs so you can check it against your own chats.

To add a different output format, subclass `Converter` in `convert.py` and register it in
`CONVERTERS`.

## What the iPhone step produces

On the iPhone, in **Files › On My iPhone › <the app you chose> › ViberTransfer-<timestamp>/**:

- `chats/*.txt`: one readable transcript per conversation (opens in Files/Quick Look)
- `chats/*.json` and `chats/index.json`: the same data in structured form
- `media/`: photos, videos, voice notes and other attachments that were on the Android phone
- `manifest.json`: so you can tell later what the archive contains

Only a new folder is created, and nothing existing is overwritten. If the copy is cancelled or
fails, that folder is removed. After copying, every file's size on the iPhone is compared with
the Mac copy. `raw/` (the databases) stays on the Mac.

## Limitations

Viber on iOS:

- **Chats will not show up inside Viber on the iPhone.** Viber's iOS data lives in its sandbox.
  On a non-jailbroken iPhone, Apple only lets a Mac reach the Documents folder of apps that opt
  into File Sharing, and the Media partition. Viber publishes no import format, and its own
  backups are per-platform (Google Drive on Android, iCloud on iOS). This tool does not try to
  get around any of that.
- If Viber offers an official Android→iPhone history transfer for your app versions, use it
  instead (check Viber's help center). It's the only way to get history *inside* Viber. You can
  still run this tool first to keep an independent backup.

Android side:

- **Root is effectively required.** Without root, only `adb backup` is possible, and only if
  Viber allows it. Current Viber versions set `allowBackup=false`, and Android 12+ excludes apps
  targeting SDK 31+ anyway. The app checks this and tells you.
- The helper only reads files that root can read. If a database were encrypted, the converter
  would report it, and the tool will not try to break Viber's encryption.
- Only Android user 0 is backed up (not Secure Folder or other profiles).
- Copying works best with Viber stopped. The app force-stops it, which doesn't touch its data.
- Media that was never downloaded to the phone, or that Viber already cleaned up, can't be
  backed up.

## What gets deleted, and when

- **Viber's data on the phone is never modified or deleted.** It is only read.
- The staging copy `/data/local/tmp/vibertransfer_staging` (made by this tool) is removed after a
  successful pull, on Cancel, and on errors.
- The helper app is removed from the phone only when you click *Remove helper app…* and confirm
  the dialog.
- On the iPhone, only the `ViberTransfer-<timestamp>` folder created by the current run is
  removed, and only if that run is cancelled or fails.
- On the Mac, Cancel removes the incomplete backup folder. After an error the partial folder is
  kept so you can inspect it.

## Privacy

No network access: the app makes no internet requests, and the helper APK doesn't even request
the INTERNET permission. Backups contain your private messages, so keep
`~/Documents/ViberTransfer` somewhere safe (FileVault on). Use this tool only with your own
devices and accounts.

## Troubleshooting

- **Android panel says *Unauthorized*:** unlock the phone and accept the USB debugging prompt.
  If it never appears, use Developer options › *Revoke USB debugging authorizations* and
  reconnect.
- **"The helper app did not respond":** open *Viber Transfer Helper* on the phone, then run the
  transfer again.
- **Root prompt never appears:** in Magisk › Superuser, check that Viber Transfer Helper isn't
  set to *Deny*.
- **iPhone panel says *Not trusted*:** unlock the iPhone, reconnect, and tap Trust.
- **Conversion failed:** open `schema_report.txt` in the backup folder, adjust the candidates in
  `convert.py`, and run `python3 -m vibertransfer convert <folder>`.
