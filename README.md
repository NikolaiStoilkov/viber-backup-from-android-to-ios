# Viber Transfer

A native Mac app (SwiftUI) that backs up Viber from your own Samsung/Android phone over USB, verifies the
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

- **SwiftUI window, Python engine.** The window is a native SwiftUI app (`mac-app/`): device cards,
  Start/Cancel, prompts, steps, summary, log, and a standard Settings window (⌘,). The work is done
  by the Python package `vibertransfer/`, which the app runs as a child process (the "engine").
  The engine stays in Python because the iPhone side needs pymobiledevice3, a Python library, and
  because that code is unit-tested without devices. The two talk with one JSON object per line
  over the engine's stdin/stdout (see `vibertransfer/engine.py`). There is no network socket, and
  when the app quits or crashes, the engine sees its input close, cancels any running transfer and
  cleans up. The packaged app carries its own Python, so nothing needs to be installed except adb
  and libimobiledevice. The app is built with SwiftPM and the Command Line Tools; no Xcode project
  is needed.
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
run.sh                 build the native app and run it against this checkout (--setup, --check)
build-apk.sh           build the Android helper APK
build-dmg.sh           build "Viber Transfer.app" and dist/ViberTransfer-<version>-<arch>.dmg
requirements.txt       pymobiledevice3 (only needed for the iPhone step)
requirements-build.txt PyInstaller + Pillow (only needed to build the .dmg)
mac-app/               the native app (SwiftPM package)
  build-app.sh         assembles "Viber Transfer.app" (Swift binary, Info.plist, icon, bundled engine, signature)
  Sources/ViberTransfer/
    ViberTransferApp.swift   app entry, window + Settings scenes, quit handling
    ContentView.swift        the main window
    SettingsView.swift       the Settings window
    Model.swift              decoded engine data + the observable state the views show
    Engine.swift             starts the engine and speaks its JSON-lines protocol
packaging/             PyInstaller spec for the engine, launcher, icon drawing, entitlements, DMG read-me
vibertransfer/
  __main__.py          CLI: `engine`, `verify <folder>`, `convert <folder>`, `selftest`
  engine.py            the backend the app talks to (JSON over stdin/stdout)
  job.py               runs the steps in order; cancel + cleanup
  tools.py             find adb/libimobiledevice, install hints, settings, cancellable subprocess runner
  android.py           adb wrapper, output parsers, adb-backup (.ab) extraction
  backup.py            folder layout, SHA-256, manifest.json
  convert.py           Converter interface + Viber SQLite → JSON/TXT converter
  ios.py               iPhone detection and the copy to an app's Documents folder
android-helper/        Kotlin helper app (one screen + one foreground service, no dependencies)
  app/src/main/res/raw/copy.sh   the root copy script
tests/                 unit tests, a fake adb, end-to-end job and engine-protocol tests
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
the one that ships with macOS works. Building the app needs the Xcode Command Line Tools
(`xcode-select --install`) for `swift`. `./run.sh --check` lists what's installed.

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

## Install from the .dmg

There are two disk images, one per architecture, because several pymobiledevice3 dependencies
ship single-architecture binaries:

- `ViberTransfer-<version>-arm64.dmg` for Apple Silicon (M1 and later)
- `ViberTransfer-<version>-x86_64.dmg` for Intel Macs

1. Open the .dmg and drag **Viber Transfer** onto **Applications**.
2. The first time, macOS blocks it because the build isn't notarized. Double-click it, click
   *Done*, then go to **System Settings › Privacy & Security** and click **Open Anyway**. You
   only need to do this once.
3. Install the device tools once, in Terminal:

   ```bash
   brew install --cask android-platform-tools
   ```

   ```bash
   brew install libimobiledevice
   ```

   Python and pymobiledevice3 are already inside the app.
4. Open **Viber Transfer**, connect both phones, check that both cards say *Connected*, and click
   **Start Transfer**. Settings are under **Viber Transfer › Settings…** (⌘,). If you quit
   during a transfer, the app asks first, then cancels the transfer and cleans up before closing.
   The engine's log is written to `~/Library/Logs/ViberTransfer/engine.log`.

### Building the .dmg

```bash
./build-apk.sh
```

```bash
./build-dmg.sh
```

`./build-dmg.sh` builds for this Mac's architecture. Pass `--arch x86_64` or `--arch arm64` to
build for the other one; on Apple Silicon the Intel build runs under Rosetta
(`softwareupdate --install-rosetta`). The script:

- creates a separate build venv per architecture under `build/`
- runs the unit tests
- draws the icon
- bundles the helper APK if `./build-apk.sh` has produced it (otherwise it warns, and the app can
  only use the no-root fallback)
- builds the engine with PyInstaller (Python, pymobiledevice3, helper APK)
- builds the SwiftUI app for the same architecture and puts the engine in
  `Contents/Resources/engine`
- runs the engine's `selftest` on the result and checks the signature
- creates `dist/ViberTransfer-<version>-<arch>.dmg` with an Applications shortcut

Without a signing identity the app gets an ad-hoc signature, which is fine on your own Mac. To
hand the .dmg to other people without Gatekeeper warnings, you need an Apple Developer ID:

```bash
CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" NOTARY_PROFILE=myprofile ./build-dmg.sh
```

(`NOTARY_PROFILE` is a keychain profile created once with `xcrun notarytool store-credentials`.)

## Build and run from source

```bash
./build-apk.sh
```

```bash
./run.sh
```

`./run.sh` builds the native app into `build/dev/` and starts it. In this development build the
app's engine is `.venv/bin/python -m vibertransfer engine` from this checkout, so Python changes
take effect on the next launch without packaging. Check that both device cards say *Connected*
and click **Start Transfer**. Optional settings (adb path, phone serial when several are
connected, helper APK path, target iPhone app, skip iPhone step, and the status of each external
tool) are in the Settings window (⌘,) and saved in
`~/Library/Application Support/ViberTransfer/settings.json`.

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
