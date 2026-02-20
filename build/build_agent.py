"""
WellcomSOFT Agent 빌드 스크립트
사용법: python build/build_agent.py
출력: dist/WellcomAgent_Setup.exe (Installer)

에이전트는 PyQt6 없이 경량 빌드됩니다.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent  # wellcomsoft/
BUILD_DIR = PROJECT_DIR / "build"
DIST_DIR = PROJECT_DIR / "dist"

# InnoSetup 컴파일러 경로 (자동 탐색)
ISCC_PATHS = [
    Path(os.environ.get('LOCALAPPDATA', '')) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def find_iscc() -> Path:
    """InnoSetup 컴파일러 경로 탐색"""
    for p in ISCC_PATHS:
        if p.exists():
            return p
    result = subprocess.run(['where', 'ISCC'], capture_output=True, text=True)
    if result.returncode == 0:
        return Path(result.stdout.strip().split('\n')[0])
    return None


def clean():
    """이전 빌드 결과물 정리"""
    print("=== 이전 Agent 빌드 정리 ===")
    for d in [DIST_DIR / "WellcomAgent", BUILD_DIR / "work_agent"]:
        if d.exists():
            try:
                shutil.rmtree(d)
                print(f"  삭제: {d}")
            except PermissionError:
                shutil.rmtree(d, ignore_errors=True)
                print(f"  삭제 (일부 파일 잠김 무시): {d}")

    setup_exe = DIST_DIR / "WellcomAgent_Setup.exe"
    if setup_exe.exists():
        setup_exe.unlink()
        print(f"  삭제: {setup_exe}")

    print("  정리 완료")


def build_exe():
    """PyInstaller로 에이전트 EXE 빌드"""
    print("\n=== PyInstaller Agent 빌드 시작 ===")
    spec_file = BUILD_DIR / "agent.spec"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR / "work_agent"),
        "--clean",
        "--noconfirm",
        str(spec_file)
    ]

    print(f"  명령: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(PROJECT_DIR))

    if result.returncode != 0:
        print("  Agent 빌드 실패!")
        sys.exit(1)

    exe_path = DIST_DIR / "WellcomAgent" / "WellcomAgent.exe"
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / 1024 / 1024
        print(f"  빌드 완료: {exe_path} ({size_mb:.1f} MB)")
    else:
        print("  빌드 실패: WellcomAgent.exe 없음")
        sys.exit(1)


def build_installer():
    """InnoSetup으로 Agent Installer 빌드"""
    print("\n=== Agent Installer 빌드 시작 ===")

    iscc = find_iscc()
    if not iscc:
        print("  InnoSetup(ISCC.exe)을 찾을 수 없습니다!")
        print("  설치: winget install -e --id JRSoftware.InnoSetup")
        print("  Installer 빌드를 건너뜁니다.")
        return False

    print(f"  ISCC: {iscc}")

    iss_file = BUILD_DIR / "agent_installer.iss"
    if not iss_file.exists():
        print(f"  agent_installer.iss 파일 없음: {iss_file}")
        return False

    cmd = [str(iscc), str(iss_file)]
    print(f"  명령: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(BUILD_DIR))

    if result.returncode != 0:
        print("  Agent Installer 빌드 실패!")
        return False

    setup_exe = DIST_DIR / "WellcomAgent_Setup.exe"
    if setup_exe.exists():
        size_mb = setup_exe.stat().st_size / 1024 / 1024
        print(f"  Installer 빌드 완료: {setup_exe} ({size_mb:.1f} MB)")
        return True
    else:
        print("  Installer 출력 파일을 찾을 수 없습니다.")
        return False


def verify_build():
    """빌드 결과물 검증"""
    print("\n=== Agent 빌드 검증 ===")

    checks = [
        (DIST_DIR / "WellcomAgent" / "WellcomAgent.exe", "WellcomAgent.exe"),
        (DIST_DIR / "WellcomAgent_Setup.exe", "WellcomAgent_Setup.exe (Installer)"),
    ]

    for path, name in checks:
        if path.exists():
            size_mb = path.stat().st_size / 1024 / 1024
            print(f"  OK: {name} ({size_mb:.1f} MB)")
        else:
            print(f"  SKIP: {name} 없음")

    if not (DIST_DIR / "WellcomAgent" / "WellcomAgent.exe").exists():
        print("\n  빌드 검증 실패! WellcomAgent.exe 없음")
        sys.exit(1)

    print("\n  빌드 검증 통과!")


def print_deploy_info():
    """배포 안내 메시지"""
    setup_exe = DIST_DIR / "WellcomAgent_Setup.exe"

    print("\n" + "=" * 50)
    print("  Agent 배포 안내")
    print("=" * 50)

    if setup_exe.exists():
        size_mb = setup_exe.stat().st_size / 1024 / 1024
        print(f"  Installer: WellcomAgent_Setup.exe ({size_mb:.1f} MB)")
        print(f"  설치 경로: C:\\WellcomAgent\\")
        print(f"  바탕화면 바로가기 + 시작프로그램 등록")
        print()
        print("  대상 PC 설치:")
        print(f"  -> WellcomAgent_Setup.exe 전달 (단일 파일)")
        print("  -> 실행하면 C:\\WellcomAgent에 자동 설치")
        print("  -> Windows 시작 시 자동 실행 옵션 제공")
    else:
        print("  Installer 미생성 - PyInstaller 결과만 있음")
        print(f"  -> dist/WellcomAgent/ 폴더를 대상 PC에 복사")

    print("=" * 50)


def main():
    print("WellcomSOFT Agent 빌드 스크립트")
    print(f"프로젝트: {PROJECT_DIR}")
    print(f"출력: {DIST_DIR}")
    print()

    clean()
    build_exe()
    build_installer()
    verify_build()
    print_deploy_info()

    setup_exe = DIST_DIR / "WellcomAgent_Setup.exe"
    if setup_exe.exists():
        print(f"\n완료! Installer: {setup_exe}")
    else:
        print(f"\n완료! EXE: {DIST_DIR / 'WellcomAgent' / 'WellcomAgent.exe'}")


if __name__ == "__main__":
    main()
