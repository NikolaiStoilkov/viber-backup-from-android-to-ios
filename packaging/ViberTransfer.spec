# PyInstaller spec for the transfer engine (vibertransfer/ + Python + pymobiledevice3) that the
# native app runs from "Viber Transfer.app/Contents/Resources/engine". Run through ./build-dmg.sh,
# which sets:
#   VT_ARCH   arm64 or x86_64
#   VT_APK    path to the helper APK to bundle (empty = build without it)
#   VT_CODESIGN_IDENTITY  "Developer ID Application: ..." (empty = ad-hoc signature)
import os

root = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = []
if os.environ.get("VT_APK"):
    datas.append((os.environ["VT_APK"], "helper"))  # -> helper/viber-transfer-helper.apk (see tools.DEFAULT_APK)

a = Analysis(
    [os.path.join(root, "packaging", "launcher.py")],
    pathex=[root],
    datas=datas,
    excludes=["tkinter", "_tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ViberTransferEngine",
    console=True,
    target_arch=os.environ.get("VT_ARCH") or None,
    codesign_identity=os.environ.get("VT_CODESIGN_IDENTITY") or None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="engine")
