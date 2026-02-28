# -*- mode: python ; coding: utf-8 -*-
"""
WellcomSOFT Agent PyInstaller Spec
빌드: python -m PyInstaller build/agent.spec
출력: dist/WellcomAgent/ (onedir 모드)

에이전트는 GUI가 없으므로 PyQt6 제외 — 경량 빌드.
런처(agent_launcher.py)가 엔트리포인트 → app/ 폴더의 agent_main을 동적 로드.
자동 업데이트 시 app/ 폴더만 교체하면 코드가 갱신됨.
"""
import sys
from pathlib import Path

project_path = Path(SPECPATH).parent  # wellcomsoft/

block_cipher = None

a = Analysis(
    [str(project_path / 'agent' / 'agent_launcher.py')],  # 런처가 엔트리포인트
    pathex=[
        str(project_path / 'agent'),
        str(project_path),
    ],
    binaries=[],
    datas=[
        # 최초 배포 시 app/ 코드를 포함
        (str(project_path / 'agent' / 'agent_main.py'), 'app'),
        (str(project_path / 'agent' / 'agent_config.py'), 'app'),
        (str(project_path / 'agent' / 'screen_capture.py'), 'app'),
        (str(project_path / 'agent' / 'input_handler.py'), 'app'),
        (str(project_path / 'agent' / 'clipboard_monitor.py'), 'app'),
        (str(project_path / 'agent' / 'file_receiver.py'), 'app'),
        (str(project_path / 'agent' / 'version.py'), 'app'),
        (str(project_path / 'agent' / 'h264_encoder.py'), 'app'),
        # core 모듈 (UDP P2P 홀펀칭용)
        (str(project_path / 'core' / '__init__.py'), 'app/core'),
        (str(project_path / 'core' / 'stun_client.py'), 'app/core'),
        (str(project_path / 'core' / 'udp_punch.py'), 'app/core'),
        (str(project_path / 'core' / 'udp_channel.py'), 'app/core'),
        # updater 모듈 (자동 업데이트용)
        (str(project_path / 'updater' / '__init__.py'), 'app/updater'),
        (str(project_path / 'updater' / 'github_client.py'), 'app/updater'),
        (str(project_path / 'updater' / 'update_checker.py'), 'app/updater'),
        (str(project_path / 'updater' / 'file_manager.py'), 'app/updater'),
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
        'zipfile',
        'asyncio',
        'uuid',
        'winreg',
        'tkinter',
        'tkinter.ttk',
        'tkinter.simpledialog',
        'tkinter.messagebox',
        'miniupnpc',
        'av',
        'av.codec',
        'av.video',
        'av.video.frame',
        'av.error',
        'numpy',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'PyQt6',
        'PyQt5',
        'matplotlib',
        'pandas',
        'scipy',
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
