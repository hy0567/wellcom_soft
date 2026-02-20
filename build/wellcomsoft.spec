# -*- mode: python ; coding: utf-8 -*-
"""
WellcomSOFT PyInstaller Spec
빌드: python -m PyInstaller build/wellcomsoft.spec
출력: dist/WellcomSOFT/ (onedir 모드)

onedir을 사용하는 이유:
- app/ 폴더를 외부에서 교체할 수 있어야 함 (자동 업데이트)
- WebEngine (~200MB)의 시작 시간 단축 (매번 해제 불필요)
"""
import sys
from pathlib import Path

project_path = Path(SPECPATH).parent  # wellcomsoft/

block_cipher = None

a = Analysis(
    [str(project_path / 'launcher.py')],  # 런처가 엔트리포인트
    pathex=[str(project_path)],
    binaries=[
        # sqlite3 모듈 명시적 포함
        (r'C:\Users\-\AppData\Local\Python\pythoncore-3.14-64\DLLs\_sqlite3.pyd', '.'),
    ],
    datas=[
        # 최초 배포 시 app/ 코드를 포함
        (str(project_path / 'main.py'), 'app'),
        (str(project_path / 'config.py'), 'app'),
        (str(project_path / 'version.py'), 'app'),
        (str(project_path / 'api_client.py'), 'app'),
        (str(project_path / 'core'), 'app/core'),
        (str(project_path / 'ui'), 'app/ui'),
        (str(project_path / 'agent'), 'app/agent'),
        (str(project_path / 'updater'), 'app/updater'),
        # 아이콘 파일 (프로그램 내부에서 사용)
        (str(project_path / 'build' / 'wellcom.ico'), 'assets'),
    ],
    hiddenimports=[
        'PyQt6.sip',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'PyQt6.QtNetwork',
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebChannel',
        'PyQt6.QtPrintSupport',
        'paramiko',
        'requests',
        'PIL',
        'sqlite3',
        '_sqlite3',
        'json',
        'hashlib',
        'zipfile',
        'mss',
        'pynput',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'pandas',
        'scipy',
        'tensorflow',
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
    name='WellcomSOFT',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # GUI 앱이므로 콘솔 없음
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
    name='WellcomSOFT',
)
