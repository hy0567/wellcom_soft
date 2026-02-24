"""
릴리스용 app.zip 패키징 (.pyc 바이트코드 변환)
사용법: python build/package_app.py
출력: dist/app.zip + dist/checksum.json

보안: .py 소스를 .pyc 바이트코드로 컴파일하여 패키징.
사용자가 코드를 직접 읽을 수 없음.

v2.0.8: 서드파티 패키지(websockets) 번들링 추가
  - PyInstaller 빌드에 websockets가 불완전 포함된 경우 대비
  - app.zip 내에 _vendor/ 디렉터리로 패키지 포함
"""

import os
import sys
import py_compile
import compileall
import shutil
import zipfile
import hashlib
import json
import tempfile
import importlib
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent  # wellcomsoft/
OUTPUT_DIR = PROJECT_DIR / "dist"

# app.zip에 포함할 파일들 (data/ 는 절대 포함하지 않음)
APP_FILES = [
    'main.py',
    'config.py',
    'version.py',
    'api_client.py',
    'launcher.py',
    'core/__init__.py',
    'core/pc_manager.py',
    'core/pc_device.py',
    'core/agent_server.py',
    'core/database.py',
    'core/multi_control.py',
    'core/script_engine.py',
    'core/key_mapper.py',
    'core/recorder.py',
    'core/h264_decoder.py',
    'ui/__init__.py',
    'ui/main_window.py',
    'ui/grid_view.py',
    'ui/viewer_widget.py',
    'ui/login_dialog.py',
    'ui/desktop_widget.py',
    'ui/side_menu.py',
    'ui/themes.py',
    'ui/settings_dialog.py',
    'ui/script_editor.py',
    'ui/keymap_editor.py',
    'ui/recording_panel.py',
    'ui/pc_info_dialog.py',
    'updater/__init__.py',
    'updater/github_client.py',
    'updater/update_checker.py',
    'updater/update_dialog.py',
    'updater/file_manager.py',
    'agent/screen_capture.py',
    'agent/input_handler.py',
    'agent/clipboard_monitor.py',
    'agent/file_receiver.py',
]

# v2.0.8: 서드파티 패키지 번들링
# PyInstaller 빌드에서 누락될 수 있는 pure-Python 패키지를 app.zip에 포함
VENDOR_PACKAGES = [
    'websockets',  # WebSocket 클라이언트 (agent_server.py에서 사용)
]


def compile_to_pyc(src_path: Path, dest_dir: Path, rel_path: str):
    """
    .py 파일을 .pyc로 컴파일하여 dest_dir에 저장.
    .pyc 파일명은 .py와 동일한 위치에 확장자만 .pyc로 변경.
    예: core/pc_device.py → core/pc_device.pyc
    """
    # 대상 경로 생성
    pyc_rel = rel_path.replace('.py', '.pyc')
    dest_path = dest_dir / pyc_rel
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # py_compile로 .pyc 생성 (임시 위치)
        py_compile.compile(
            str(src_path),
            cfile=str(dest_path),
            doraise=True,
            optimize=2  # 최적화 레벨 2: docstring 제거 + assert 제거
        )
        return pyc_rel
    except py_compile.PyCompileError as e:
        print(f"  컴파일 오류: {rel_path} - {e}")
        return None


def _bundle_vendor_package(pkg_name: str, tmp_path: Path):
    """서드파티 패키지를 _vendor/ 디렉터리로 번들링

    site-packages에서 패키지를 찾아 .py 파일을 .pyc로 컴파일하여
    _vendor/패키지명/ 으로 복사.

    Returns:
        list[str]: ZIP 내 상대 경로 목록
    """
    bundled = []
    try:
        mod = importlib.import_module(pkg_name)
        pkg_dir = Path(mod.__file__).parent
    except (ImportError, AttributeError, TypeError):
        print(f"  경고: {pkg_name} 패키지를 찾을 수 없음 — 스킵")
        return bundled

    print(f"  패키지: {pkg_name} ({pkg_dir})")

    vendor_dest = tmp_path / "_vendor" / pkg_name
    vendor_dest.mkdir(parents=True, exist_ok=True)

    for src_file in pkg_dir.rglob("*.py"):
        rel = src_file.relative_to(pkg_dir)
        # __pycache__ 스킵
        if '__pycache__' in str(rel):
            continue

        pyc_rel_in_vendor = f"_vendor/{pkg_name}/{rel}"
        pyc_dest = tmp_path / pyc_rel_in_vendor.replace('.py', '.pyc')
        pyc_dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            py_compile.compile(
                str(src_file),
                cfile=str(pyc_dest),
                doraise=True,
                optimize=2,
            )
            bundled.append(pyc_rel_in_vendor.replace('.py', '.pyc'))
        except py_compile.PyCompileError as e:
            # 컴파일 실패 시 소스 그대로 복사
            src_dest = tmp_path / pyc_rel_in_vendor
            src_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, src_dest)
            bundled.append(pyc_rel_in_vendor)
            print(f"    컴파일 실패 (소스 복사): {rel} - {e}")

    # .pyd / .so 파일도 복사 (C 확장)
    for ext_file in pkg_dir.rglob("*.pyd"):
        if '__pycache__' in str(ext_file):
            continue
        rel = ext_file.relative_to(pkg_dir)
        ext_rel = f"_vendor/{pkg_name}/{rel}"
        ext_dest = tmp_path / ext_rel
        ext_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ext_file, ext_dest)
        bundled.append(ext_rel)

    print(f"    → {len(bundled)} 파일 번들")
    return bundled


def create_app_zip():
    """app.zip 생성 (.pyc 바이트코드)"""
    OUTPUT_DIR.mkdir(exist_ok=True)
    zip_path = OUTPUT_DIR / "app.zip"

    print("=== app.zip 패키징 (.pyc 바이트코드) ===")

    # 버전 정보 읽기
    sys.path.insert(0, str(PROJECT_DIR))
    from version import __version__
    print(f"  버전: v{__version__}")

    # 임시 디렉터리에서 .pyc 컴파일
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        print("\n--- .py → .pyc 컴파일 ---")

        compiled_files = []
        for rel_path in APP_FILES:
            full_path = PROJECT_DIR / rel_path
            if not full_path.exists():
                print(f"  경고: 파일 없음 - {rel_path}")
                continue

            pyc_rel = compile_to_pyc(full_path, tmp_path, rel_path)
            if pyc_rel:
                compiled_files.append(pyc_rel)
                print(f"  컴파일: {rel_path} → {pyc_rel}")

        # version.py는 소스도 포함 (업데이트 체커가 버전 읽기 위해)
        version_src = PROJECT_DIR / "version.py"
        version_dest = tmp_path / "version.py"
        shutil.copy2(version_src, version_dest)
        print(f"  복사: version.py (소스 유지 - 버전 확인용)")

        # v2.0.8: 서드파티 패키지 번들링
        print("\n--- 서드파티 패키지 번들링 ---")
        vendor_files = []
        for pkg_name in VENDOR_PACKAGES:
            vendor_files.extend(_bundle_vendor_package(pkg_name, tmp_path))

        # ZIP 생성
        print(f"\n--- ZIP 패키징 ---")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # .pyc 파일 추가
            for pyc_rel in compiled_files:
                pyc_path = tmp_path / pyc_rel
                if pyc_path.exists():
                    zf.write(pyc_path, pyc_rel)

            # version.py 소스 추가
            zf.write(version_dest, "version.py")

            # 서드파티 패키지 추가
            for vendor_rel in vendor_files:
                vendor_path = tmp_path / vendor_rel
                if vendor_path.exists():
                    zf.write(vendor_path, vendor_rel)

    # SHA256 체크섬
    sha256 = hashlib.sha256()
    with open(zip_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    checksum = sha256.hexdigest()

    print(f"\n  파일: {zip_path}")
    print(f"  크기: {zip_path.stat().st_size / 1024:.1f} KB")
    print(f"  SHA256: {checksum}")
    print(f"  포맷: .pyc 바이트코드 (소스 비공개)")

    # checksum.json 생성
    checksum_data = {
        "version": __version__,
        "sha256": checksum,
        "size": zip_path.stat().st_size
    }
    checksum_path = OUTPUT_DIR / "checksum.json"
    with open(checksum_path, 'w', encoding='utf-8') as f:
        json.dump(checksum_data, f, indent=2)

    print(f"\n  checksum.json: {checksum_path}")
    print(f"\n릴리스 노트에 추가:")
    print(f"  SHA256: {checksum}")

    return checksum


if __name__ == "__main__":
    create_app_zip()
