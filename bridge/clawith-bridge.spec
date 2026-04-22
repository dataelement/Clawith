# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for clawith-bridge.exe (onefile, Windows console).
#
# Build:
#     cd bridge/
#     pip install pyinstaller
#     pyinstaller clawith-bridge.spec --clean
#
# Output: dist/clawith-bridge.exe
#
# The console window is visible when run from cmd (for `install` mode output)
# but hidden automatically when launched by Task Scheduler (see
# `_hide_console_if_service` in __main__.py).

block_cipher = None


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Imported lazily only in install mode; static analysis misses it.
        'clawith_bridge.install_windows',
        'clawith_bridge.baked_config',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Shave size: these are transitive but never used in bridge.
        # Don't exclude email/http/xml — httpx uses them internally.
        'tkinter',
        'unittest',
    ],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='clawith-bridge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
