# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Duplicate Finder. The SAME spec builds on Windows and
macOS (and Linux) — PyInstaller reads the host platform at build time. It does
NOT cross-compile: run it on Windows to get the .exe, on a Mac to get the .app.

    pyinstaller DuplicateFinder.spec

Output lands in dist/ :
    Windows : dist/DuplicateFinder/DuplicateFinder.exe   (a folder you zip & ship)
    macOS   : dist/Duplicate Finder.app                  (a bundle you zip/dmg)
"""

import sys
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []

# Bundle everything these packages need. scipy is a HIDDEN dependency of
# imagehash.phash (it uses scipy's DCT) — without it the frozen app crashes on
# the first image/video hash. cv2 ships data files + native libs that must come
# along too.
for pkg in ("cv2", "scipy", "imagehash", "PIL", "flask", "jinja2",
            "werkzeug", "numpy"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += ["scipy.fftpack", "scipy.fft", "scipy.special",
                  "scipy._lib.array_api_compat.numpy.fft"]

block_cipher = None

a = Analysis(
    ["run_app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "pandas", "pytest", "tkinter.test"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DuplicateFinder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # GUI app: no terminal window
    disable_windowed_traceback=False,
    target_arch=None,         # builds for the host arch (arm64 on Apple Silicon)
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DuplicateFinder",
)

# macOS: wrap the onedir output into a clickable .app bundle.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Duplicate Finder.app",
        icon=None,
        bundle_identifier="com.atacat.duplicatefinder",
        info_plist={
            "CFBundleName": "Duplicate Finder",
            "CFBundleDisplayName": "Duplicate Finder",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
        },
    )
