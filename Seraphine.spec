# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Seraphine (macOS .app bundle).

Mirrors the Windows make.ps1 packaging logic, adapted for macOS:
  - Windowed app named "Seraphine", entry point main.py.
  - The runtime loads resources via RELATIVE paths (e.g. "app/resource/qss/...").
    main.py does os.chdir(dirname(abspath(__file__))) at startup; in a frozen
    onedir build __file__ resolves inside sys._MEIPASS, so the app/resource tree
    is bundled (as datas) under the "app/resource/..." prefix at the bundle root.
  - The qfluentwidgets fork compiles its qss/image/font resources into
    _rc/resource.py (Qt :/ resource scheme), so they ship as Python modules via
    collect_submodules — there are no loose qfluentwidgets data files to collect.
"""

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# --- Bundle the app/resource tree, excluding runtime-downloaded / source-only files ---
# Mirrors make.ps1 deletions:
#   - app/resource/game*        (runtime-downloaded)
#   - *.ts source translations  (only the compiled *.qm is needed)
#   - bin/fix_lcu_window.c, bin/readme.md
# The Windows .exe helper (fix_lcu_window.exe) is also dropped — Windows-only.
_EXCLUDE_SUFFIXES = ('.ts', '.c', '.exe')
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
)

app = BUNDLE(
    coll,
    name='Seraphine.app',
    icon='app/resource/images/logo.icns',
    bundle_identifier='com.ezekielrchy.seraphine',
    info_plist={
        'CFBundleName': 'Seraphine',
        'CFBundleDisplayName': 'Seraphine',
        'CFBundleShortVersionString': '1.1.8',
        'CFBundleVersion': '1.1.8',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.13',
    },
)
