# -*- mode: python ; coding: utf-8 -*-

import sys
import os

block_cipher = None

app_name = 'Formix'
app_version = '1.2.2'

project_root = os.path.dirname(os.path.abspath(SPEC))
format_factory_path = os.path.join(project_root, 'format_factory')

a = Analysis(
    ['format_factory/main.py'],
    pathex=[project_root],
    binaries=[],
    datas=[
        ('format_factory/assets', 'assets'),
    ],
    hiddenimports=[
        'PyQt6',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'mutagen',
        'mutagen.mp4',
        'mutagen.mp3',
        'mutagen.flac',
        'mutagen.ogg',
        'mutagen.wave',
        'pycryptodome',
        'pycryptodome.Cipher',
        'pycryptodome.Util',
    ],
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

# Windows: 单文件 exe
if sys.platform == 'win32':
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name=app_name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon='format_factory/assets/logo.ico',
    )
else:
    # macOS 和 Linux: 目录形式
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=app_name,
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
        name=app_name,
    )

    # macOS: 生成 .app
    if sys.platform == 'darwin':
        app = BUNDLE(
            coll,
            name=f'{app_name}.app',
            bundle_identifier='com.formix.app',
            info_plist={
                'CFBundleName': app_name,
                'CFBundleDisplayName': app_name,
                'CFBundleGetInfoString': f'Formix - 格式转换通 v{app_version}',
                'CFBundleIdentifier': 'com.formix.app',
                'CFBundleVersion': app_version,
                'CFBundleShortVersionString': app_version,
                'NSHighResolutionCapable': True,
                'LSMinimumSystemVersion': '10.13',
            },
        )
