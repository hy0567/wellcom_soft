"""
WellcomSOFT 원커맨드 릴리스 도구

사용법:
    python tools/release.py patch "버그 수정 내용"
    python tools/release.py minor "새 기능 설명"
    python tools/release.py major "대규모 변경 설명"
    python tools/release.py --current              # 현재 버전 확인
    python tools/release.py --dry-run patch "테스트" # 실제 실행 없이 확인

동작:
    1. version.py (매니저+에이전트) 버전 자동 증가
    2. app.zip (매니저) + agent.zip (에이전트) 생성
    3. SHA256 체크섬 계산
    4. git commit + tag
    5. gh release create + 두 에셋 업로드
"""

import os
import sys
import re
import hashlib
import zipfile
import subprocess
import argparse
from pathlib import Path

# 프로젝트 루트 (wellcomsoft/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "version.py"
AGENT_VERSION_FILE = PROJECT_ROOT / "agent" / "version.py"

# ==================== app.zip (매니저) ====================
# app.zip에 포함할 소스 디렉터리/파일
INCLUDE_DIRS = ["core", "ui", "updater"]
INCLUDE_FILES = ["main.py", "config.py", "version.py", "api_client.py"]

# 매니저 app.zip 제외 패턴
EXCLUDE_PATTERNS = [
    "__pycache__",
    ".pyc",
    ".pyo",
    ".git",
    ".idea",
    ".vscode",
    "test_*",
    "*.log",
    ".env",
    "agent_main.py",
    "agent_config.py",
    "agent_launcher.py",
    "build_agent.py",
]

# ==================== agent.zip (에이전트) ====================
# agent.zip에 포함할 파일 (agent/ 하위 → 루트, updater/ → updater/)
AGENT_FILES = [
    "agent/agent_main.py",
    "agent/agent_config.py",
    "agent/screen_capture.py",
    "agent/input_handler.py",
    "agent/clipboard_monitor.py",
    "agent/file_receiver.py",
    "agent/version.py",
    "agent/h264_encoder.py",
    # core 모듈 (UDP P2P 홀펀칭용)
    "core/__init__.py",
    "core/stun_client.py",
    "core/udp_punch.py",
    "core/udp_channel.py",
    # updater 모듈
    "updater/__init__.py",
    "updater/github_client.py",
    "updater/update_checker.py",
    "updater/file_manager.py",
]


def read_version() -> str:
    """version.py에서 현재 버전 읽기"""
    content = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if not match:
        print("[ERROR] version.py에서 __version__을 찾을 수 없습니다.")
        sys.exit(1)
    return match.group(1)


def bump_version(current: str, bump_type: str) -> str:
    """버전 증가: major/minor/patch"""
    parts = [int(x) for x in current.split(".")]
    while len(parts) < 3:
        parts.append(0)

    if bump_type == "major":
        parts[0] += 1
        parts[1] = 0
        parts[2] = 0
    elif bump_type == "minor":
        parts[1] += 1
        parts[2] = 0
    elif bump_type == "patch":
        parts[2] += 1
    else:
        print(f"[ERROR] 알 수 없는 bump 타입: {bump_type}")
        sys.exit(1)

    return ".".join(str(p) for p in parts)


def write_version(new_version: str):
    """매니저 + 에이전트 version.py 모두 업데이트"""
    for vf in [VERSION_FILE, AGENT_VERSION_FILE]:
        if not vf.exists():
            print(f"[WARN] 버전 파일 없음: {vf}")
            continue
        content = vf.read_text(encoding="utf-8")
        updated = re.sub(
            r'(__version__\s*=\s*["\'])[^"\']+(["\'])',
            rf"\g<1>{new_version}\g<2>",
            content,
        )
        vf.write_text(updated, encoding="utf-8")


def should_exclude(path: Path) -> bool:
    """제외 대상인지 확인"""
    path_str = str(path)
    name = path.name

    for pattern in EXCLUDE_PATTERNS:
        if pattern.startswith("*"):
            if name.endswith(pattern[1:]):
                return True
        elif pattern.endswith("*"):
            if name.startswith(pattern[:-1]):
                return True
        else:
            if pattern in path_str:
                return True
    return False


def create_app_zip(output_path: Path) -> int:
    """app.zip (매니저) 생성, 파일 수 반환"""
    file_count = 0
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 개별 파일
        for fname in INCLUDE_FILES:
            src = PROJECT_ROOT / fname
            if src.exists():
                zf.write(src, fname)
                file_count += 1

        # 디렉터리
        for dname in INCLUDE_DIRS:
            src_dir = PROJECT_ROOT / dname
            if not src_dir.exists():
                continue
            for file_path in src_dir.rglob("*"):
                if not file_path.is_file():
                    continue
                if should_exclude(file_path):
                    continue
                arc_name = str(file_path.relative_to(PROJECT_ROOT))
                zf.write(file_path, arc_name)
                file_count += 1

    return file_count


def _agent_arc_name(rel_path: str) -> str:
    """agent.zip 내부 경로 결정

    agent/ 하위 파일은 루트에 배치, updater/ 는 유지
    """
    if rel_path.startswith('agent/'):
        return rel_path[len('agent/'):]
    return rel_path


def create_agent_zip(output_path: Path) -> int:
    """agent.zip (에이전트) 생성, 파일 수 반환"""
    file_count = 0
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in AGENT_FILES:
            src = PROJECT_ROOT / rel_path
            if not src.exists():
                print(f"  [WARN] 에이전트 파일 없음: {rel_path}")
                continue
            arc_name = _agent_arc_name(rel_path)
            zf.write(src, arc_name)
            file_count += 1

    return file_count


def calc_sha256(file_path: Path) -> str:
    """SHA256 체크섬 계산"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """명령 실행"""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    if check and result.returncode != 0:
        print(f"[ERROR] 명령 실패: {result.stderr.strip()}")
        sys.exit(1)
    return result


def check_prerequisites():
    """사전 조건 확인"""
    # gh CLI 확인
    result = subprocess.run(
        ["gh", "--version"], capture_output=True, text=True
    )
    if result.returncode != 0:
        print("[ERROR] GitHub CLI (gh)가 설치되어 있지 않습니다.")
        print("        https://cli.github.com/ 에서 설치하세요.")
        sys.exit(1)

    # gh 인증 확인
    result = subprocess.run(
        ["gh", "auth", "status"], capture_output=True, text=True
    )
    if result.returncode != 0:
        print("[ERROR] GitHub CLI 인증이 필요합니다.")
        print("        'gh auth login' 을 실행하세요.")
        sys.exit(1)

    # git 상태 확인
    result = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True,
        cwd=str(PROJECT_ROOT)
    )
    # 경고만 (강제 중단하지 않음)
    if result.stdout.strip():
        print("[WARN] 커밋되지 않은 변경사항이 있습니다:")
        for line in result.stdout.strip().split("\n")[:5]:
            print(f"       {line}")
        print()


def main():
    parser = argparse.ArgumentParser(description="WellcomSOFT 릴리스 도구")
    parser.add_argument(
        "bump", nargs="?", choices=["major", "minor", "patch"],
        help="버전 증가 타입"
    )
    parser.add_argument("message", nargs="?", default="", help="릴리스 메시지")
    parser.add_argument("--current", action="store_true", help="현재 버전 확인")
    parser.add_argument("--dry-run", action="store_true", help="실제 실행 없이 확인")
    args = parser.parse_args()

    current = read_version()

    if args.current:
        print(f"현재 버전: v{current}")
        return

    if not args.bump:
        parser.print_help()
        return

    new_version = bump_version(current, args.bump)
    tag = f"v{new_version}"

    print(f"{'[DRY-RUN] ' if args.dry_run else ''}WellcomSOFT 릴리스")
    print(f"  버전: v{current} → v{new_version}")
    print(f"  메시지: {args.message or '(없음)'}")
    print()

    if not args.dry_run:
        check_prerequisites()

    # 1. version.py 업데이트 (매니저 + 에이전트)
    print("[1/6] version.py 업데이트 (매니저 + 에이전트)")
    if not args.dry_run:
        write_version(new_version)
    print(f"  → {new_version}")

    # 2. app.zip (매니저) 생성
    print("[2/6] app.zip (매니저) 생성")
    app_zip_path = PROJECT_ROOT / "app.zip"
    if not args.dry_run:
        file_count = create_app_zip(app_zip_path)
        size_mb = app_zip_path.stat().st_size / 1024 / 1024
        print(f"  → {file_count}개 파일, {size_mb:.1f}MB")
    else:
        print("  → (스킵)")

    # 3. agent.zip (에이전트) 생성
    print("[3/6] agent.zip (에이전트) 생성")
    agent_zip_path = PROJECT_ROOT / "agent.zip"
    if not args.dry_run:
        file_count = create_agent_zip(agent_zip_path)
        size_mb = agent_zip_path.stat().st_size / 1024 / 1024
        print(f"  → {file_count}개 파일, {size_mb:.1f}MB")
    else:
        print("  → (스킵)")

    # 4. SHA256 체크섬
    print("[4/6] SHA256 체크섬")
    if not args.dry_run:
        app_checksum = calc_sha256(app_zip_path)
        agent_checksum = calc_sha256(agent_zip_path)
        print(f"  app.zip   → {app_checksum[:16]}...")
        print(f"  agent.zip → {agent_checksum[:16]}...")
    else:
        app_checksum = "(dry-run)"
        agent_checksum = "(dry-run)"
        print("  → (스킵)")

    # 5. Git commit + tag
    print("[5/6] Git commit + tag")
    if not args.dry_run:
        run_cmd(["git", "add", "version.py", "agent/version.py"])
        run_cmd(["git", "commit", "-m", f"release: v{new_version} - {args.message}"])
        run_cmd(["git", "tag", tag])
        run_cmd(["git", "push"])
        run_cmd(["git", "push", "--tags"])
    else:
        print("  → (스킵)")

    # 6. GitHub Release (두 에셋 업로드)
    print("[6/6] GitHub Release 생성 (app.zip + agent.zip)")
    release_notes = args.message or ""
    if release_notes:
        release_notes += "\n\n"
    release_notes += f"SHA256(app.zip): {app_checksum}\nSHA256(agent.zip): {agent_checksum}"

    if not args.dry_run:
        run_cmd([
            "gh", "release", "create", tag,
            str(app_zip_path),
            str(agent_zip_path),
            "--title", f"v{new_version}",
            "--notes", release_notes,
        ])
        # 정리
        app_zip_path.unlink(missing_ok=True)
        agent_zip_path.unlink(missing_ok=True)
    else:
        print(f"  → gh release create {tag} app.zip agent.zip")
        print(f"  → notes: {release_notes}")

    print()
    print(f"릴리스 완료: v{new_version}")
    if not args.dry_run:
        print(f"https://github.com/{_get_repo()}/releases/tag/{tag}")


def _get_repo() -> str:
    """version.py에서 repo 정보 읽기"""
    content = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'__github_repo__\s*=\s*["\']([^"\']+)["\']', content)
    return match.group(1) if match else "hy0567/wellcom_soft"


if __name__ == "__main__":
    main()
