# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Framework 16 LED Matrix Control
#
# Build on Windows:  pyinstaller led_control.spec
# Build on Linux:    pyinstaller led_control.spec
#
# PyInstaller always builds for the platform it runs on.
# Output: dist/LedMatrixControl(.exe on Windows)

import sys

_win = sys.platform == "win32"

# Platform-specific hidden imports
_hidden = [
    "pystray._appindicator",  # GNOME: only backend that actually docks
    "pystray._xorg",          # always include all Linux backends;
    "pystray._gtk",           # PyInstaller will skip the ones that don't exist
    "pystray._win32",
]
if _win:
    _hidden += ["win32api", "win32con", "wmi", "pythoncom"]

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["wmi"] if not _win else [],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="LedMatrixControl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=not _win,   # strip debug symbols on Linux to reduce binary size
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,    # no terminal window — runs as tray/background app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,        # TODO: add .ico (Windows) or .png (Linux) path
)
