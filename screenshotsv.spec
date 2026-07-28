# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

# pynput は動的ロードを含むため明示的に収集する
pynput_datas, pynput_binaries, pynput_hiddenimports = collect_all('pynput')

a = Analysis(
    ['screenshotsv.py'],
    pathex=[],
    binaries=pynput_binaries,
    datas=pynput_datas + [('icons/*.svg', 'icons')],
    hiddenimports=pynput_hiddenimports + [
        'pynput.keyboard._win32',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtNetwork',
        'PySide6.QtSvg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy'],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='screenshotsv',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 圧縮はアンチウイルスの誤検知率を大きく上げるため使わない
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    # 昇格はコード側の任意昇格（UAC拒否時は通常権限で継続）に任せるため、
    # マニフェストでの管理者必須化はしない（標準ユーザーでも起動可能にする）
    uac_admin=False,
    icon='icons/app-icon.ico',
    version='version_info.txt',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
