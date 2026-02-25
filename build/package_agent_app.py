"""
릴리스용 agent.zip 패키징 (.pyc 바이트코드 변환)
사용법: python build/package_agent_app.py
출력: dist/agent.zip + dist/agent_checksum.json

보안: .py 소스를 .pyc 바이트코드로 컴파일하여 패키징.
에이전트 자동 업데이트 시 app/ 폴더에 배치되는 코드.
"""

import os
import sys
import py_compile
import shutil
import zipfile
import hashlib
import json
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent  # wellcomsoft/
OUTPUT_DIR = PROJECT_DIR / "dist"

# agent.zip에 포함할 파일들 (agent/ 소스 + updater/ 모듈)
AGENT_APP_FILES = [
    # 에이전트 핵심 코드
    'agent/agent_main.py',
    'agent/agent_config.py',
    'agent/screen_capture.py',
    'agent/input_handler.py',
    'agent/clipboard_monitor.py',
    'agent/file_receiver.py',
    'agent/version.py',
    'agent/h264_encoder.py',
    # core 모듈 (UDP P2P 홀펀칭용)
    'core/__init__.py',
    'core/stun_client.py',
    'core/udp_punch.py',
    'core/udp_channel.py',
    # updater 모듈 (에이전트 자동 업데이트용)
    'updater/__init__.py',
    'updater/github_client.py',
    'updater/update_checker.py',
    'updater/file_manager.py',
]

# agent.zip 내부 경로 매핑 (agent/xxx.py → xxx.py, updater/는 유지)
def _get_arc_name(rel_path: str) -> str:
    """zip 내부 경로 결정

    agent/ 하위 파일은 루트에 배치 (app/agent_main.py → agent_main.py)
    updater/ 하위 파일은 updater/ 유지 (app/updater/xxx.py)
    """
    if rel_path.startswith('agent/'):
        return rel_path[len('agent/'):]  # "agent/agent_main.py" → "agent_main.py"
    return rel_path  # "updater/xxx.py" 유지


def compile_to_pyc(src_path: Path, dest_dir: Path, arc_name: str):
    """
    .py 파일을 .pyc로 컴파일하여 dest_dir에 저장.
    """
    pyc_arc = arc_name.replace('.py', '.pyc')
    dest_path = dest_dir / pyc_arc
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        py_compile.compile(
            str(src_path),
            cfile=str(dest_path),
            doraise=True,
            optimize=2
        )
        return pyc_arc
    except py_compile.PyCompileError as e:
        print(f"  컴파일 오류: {arc_name} - {e}")
        return None


def create_agent_zip():
    """agent.zip 생성 (.pyc 바이트코드)"""
    OUTPUT_DIR.mkdir(exist_ok=True)
    zip_path = OUTPUT_DIR / "agent.zip"

    print("=== agent.zip 패키징 (.pyc 바이트코드) ===")

    # 버전 정보 읽기
    agent_dir = str(PROJECT_DIR / "agent")
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)
    from version import __version__
    print(f"  버전: v{__version__}")

    # 임시 디렉터리에서 .pyc 컴파일
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        print("\n--- .py → .pyc 컴파일 ---")

        compiled_files = []
        for rel_path in AGENT_APP_FILES:
            full_path = PROJECT_DIR / rel_path
            if not full_path.exists():
                print(f"  경고: 파일 없음 - {rel_path}")
                continue

            arc_name = _get_arc_name(rel_path)
            pyc_arc = compile_to_pyc(full_path, tmp_path, arc_name)
            if pyc_arc:
                compiled_files.append(pyc_arc)
                print(f"  컴파일: {rel_path} → {pyc_arc}")

        # version.py는 소스도 포함 (업데이트 체커가 버전 읽기 위해)
        version_src = PROJECT_DIR / "agent" / "version.py"
        version_dest = tmp_path / "version.py"
        shutil.copy2(version_src, version_dest)
        print(f"  복사: version.py (소스 유지 - 버전 확인용)")

        # ZIP 생성
        print(f"\n--- ZIP 패키징 ---")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # .pyc 파일 추가
            for pyc_arc in compiled_files:
                pyc_path = tmp_path / pyc_arc
                if pyc_path.exists():
                    zf.write(pyc_path, pyc_arc)

            # version.py 소스 추가
            zf.write(version_dest, "version.py")

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
    checksum_path = OUTPUT_DIR / "agent_checksum.json"
    with open(checksum_path, 'w', encoding='utf-8') as f:
        json.dump(checksum_data, f, indent=2)

    print(f"\n  agent_checksum.json: {checksum_path}")
    print(f"\n릴리스 노트에 추가:")
    print(f"  SHA256(agent.zip): {checksum}")

    return checksum


if __name__ == "__main__":
    create_agent_zip()
