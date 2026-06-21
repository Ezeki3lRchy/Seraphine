# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Seraphine (Windows onedir build).

Windows counterpart of Seraphine.spec (which targets the macOS .app/.dmg).
Differences from the macOS spec:
  - Onedir build. main.py does os.chdir(dirname(abspath(__file__))) at startup;
    in a frozen onedir build __file__ resolves inside sys._MEIPASS, and the
    app/resource tree is bundled (as datas) under the "app/resource/..." prefix,
    so the relative loads ("app/resource/...") resolve after the chdir.
    NOTE: contents_directory='.' below requests the historical flat layout
    (Seraphine.exe + deps at the folder root), but PyInstaller 6.x SILENTLY
    IGNORES it and nests everything under _internal/. That is fine and is what
    actually ships: _MEIPASS points at _internal/, the chdir lands there, and the
    bundled app/resource/ sits alongside, so resolution still succeeds. The line
    is kept (it built the released .7z) but do not rely on it producing flat
    output on 6.x. The resulting archive has Seraphine.exe + filelist.txt at the
    root and everything else under _internal/ — the v1.1.9 in-place updater
    handles this layout.
  - The Windows-only helper app/resource/bin/fix_lcu_window.exe MUST ship, so
    '.exe' is NOT in the exclude list (the macOS spec drops it).
  - EXE with the .ico icon and no console; no BUNDLE/.app/.icns.
"""

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# --- Bundle the app/resource tree, excluding runtime-downloaded / source-only files ---
# Mirrors make.ps1 deletions:
#   - app/resource/game*        (runtime-downloaded)
#   - *.ts source translations  (only the compiled *.qm is needed)
#   - bin/fix_lcu_window.c, bin/readme.md
# NOTE: unlike the macOS spec, fix_lcu_window.exe is KEPT (Windows helper).
_EXCLUDE_SUFFIXES = ('.ts', '.c')
_EXCLUDE_BASENAMES = {'readme.md'}

resource_datas = []
for root, dirs, files in os.walk('app/resource'):
    # Skip the runtime-downloaded game* directories entirely.
    dirs[:] = [d for d in dirs if not d.startswith('game')]
    if os.path.basename(root).startswith('game'):
        continue
    for fname in files:
        if fname.lower() in _EXCLUDE_BASENAMES:
            continue
        if fname.endswith(_EXCLUDE_SUFFIXES):
            continue
        src = os.path.join(root, fname)
        # Destination keeps the "app/resource/..." prefix so relative path
        # resolution after os.chdir(_MEIPASS) succeeds.
        dest_dir = os.path.dirname(src)
        resource_datas.append((src, dest_dir))

datas = resource_datas
datas += collect_data_files('qfluentwidgets')
datas += collect_data_files('qframelesswindow')

# --- Hidden imports ---
hiddenimports = []
hiddenimports += collect_submodules('app')
hiddenimports += collect_submodules('qfluentwidgets')
hiddenimports += collect_submodules('qframelesswindow')
hiddenimports += ['qasync']

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Seraphine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app/resource/images/logo.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Seraphine',
    contents_directory='.',
)
