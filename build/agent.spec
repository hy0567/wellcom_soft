# -*- mode: python ; coding: utf-8 -*-
"""
WellcomSOFT Agent PyInstaller Spec
빌드: python -m PyInstaller build/agent.spec
출력: dist/WellcomAgent/ (onedir 모드)

에이전트는 GUI가 없으므로 PyQt6 제외 — 경량 빌드.
"""
import sys
from pathlib import Path

project_path = Path(SPECPATH).parent  # wellcomsoft/

block_cipher = None

a = Analysis(
    [str(project_path / 'agent' / 'agent_main.py')],
    pathex=[
        str(project_path / 'agent'),
        str(project_path),
    ],
    binaries=[],
    datas=[
        # 아이콘 파일 (트레이 아이콘용)
        (str(project_path / 'build' / 'wellcom.ico'), 'assets'),
    ],
    hiddenimports=[
        'requests',
        'websockets',
        'mss',
        'mss.windows',
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._win32',
        'pynput.mouse',
        'pynput.mouse._win32',
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'json',
        'hashlib',
        'asyncio',
        'uuid',
        'winreg',
        'tkinter',
        'tkinter.simpledialog',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'PyQt6',
        'PyQt5',
        'matplotlib',
        'pandas',
        'scipy',
        'numpy',
        'tensorflow',
        'torch',
        'cv2',
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # onedir 모드
    name='WellcomAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # 트레이 아이콘으로 동작
    icon=str(project_path / 'build' / 'wellcom.ico'),
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='WellcomAgent',
)
