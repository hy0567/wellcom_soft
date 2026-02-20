"""
WellcomSOFT 빌드 스크립트
사용법: python build/build.py
출력: dist/WellcomSOFT_Setup.exe (Installer)
"""

import os
import sys
import shutil
import subprocess
import py_compile
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
    # PATH에서 탐색
    result = subprocess.run(['where', 'ISCC'], capture_output=True, text=True)
    if result.returncode == 0:
        return Path(result.stdout.strip().split('\n')[0])
    return None


def clean():
    """이전 빌드 결과물 정리"""
    print("=== 이전 빌드 정리 ===")
    for d in [DIST_DIR / "WellcomSOFT", BUILD_DIR / "work"]:
        if d.exists():
            try:
                shutil.rmtree(d)
                print(f"  삭제: {d}")
            except PermissionError:
                shutil.rmtree(d, ignore_errors=True)
                print(f"  삭제 (일부 파일 잠김 무시): {d}")

    # 이전 installer 삭제
    setup_exe = DIST_DIR / "WellcomSOFT_Setup.exe"
    if setup_exe.exists():
        setup_exe.unlink()
        print(f"  삭제: {setup_exe}")

    print("  정리 완료")


def build_exe():
    """PyInstaller로 EXE 빌드"""
    print("\n=== PyInstaller 빌드 시작 ===")
    spec_file = BUILD_DIR / "wellcomsoft.spec"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR / "work"),
        "--clean",
        "--noconfirm",
        str(spec_file)
    ]

    print(f"  명령: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(PROJECT_DIR))

    if result.returncode != 0:
        print("  빌드 실패!")
        sys.exit(1)

    print(f"  빌드 완료: {DIST_DIR / 'WellcomSOFT'}")


def create_data_dir():
    """data/ 디렉터리 생성 (빈 상태)"""
    data_dir = DIST_DIR / "WellcomSOFT" / "data"
    data_dir.mkdir(exist_ok=True)
    print(f"\n  data/ 디렉터리 생성: {data_dir}")


def compile_app_to_pyc():
    """app/ 디렉터리의 .py 파일을 .pyc로 변환하고 소스 삭제

    소스 코드 보호를 위해 .py → .pyc 변환 후 .py 삭제.
    version.py는 소스 유지 (버전 읽기용).
    """
    print("\n=== .py → .pyc 변환 (소스 보호) ===")

    app_dir = DIST_DIR / "WellcomSOFT" / "_internal" / "app"
    if not app_dir.exists():
        print(f"  app/ 디렉터리 없음: {app_dir}")
        return

    py_files = list(app_dir.rglob("*.py"))
    if not py_files:
        print("  변환할 .py 파일 없음")
        return

    converted = 0
    failed = 0
    skipped = 0

    for py_file in py_files:
        # __pycache__ 내부 파일은 스킵
        if "__pycache__" in str(py_file):
            continue

        try:
            # .pyc 출력 경로 (같은 위치에 .pyc 생성)
            pyc_file = py_file.with_suffix('.pyc')

            # optimize=2: docstring + assert 제거
            py_compile.compile(
                str(py_file),
                cfile=str(pyc_file),
                doraise=True,
                optimize=2
            )

            # .py 소스 삭제
            py_file.unlink()
            converted += 1

        except py_compile.PyCompileError as e:
            print(f"  변환 실패: {py_file.name} - {e}")
            failed += 1
        except Exception as e:
            print(f"  오류: {py_file.name} - {e}")
            failed += 1

    # __pycache__ 폴더 정리
    for cache_dir in app_dir.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)

    print(f"  변환 완료: {converted}개 성공, {failed}개 실패")
    print(f"  .py 소스 파일 삭제 완료 (보안 강화)")


def build_installer():
    """InnoSetup으로 Installer 빌드"""
    print("\n=== Installer 빌드 시작 ===")

    iscc = find_iscc()
    if not iscc:
        print("  InnoSetup(ISCC.exe)을 찾을 수 없습니다!")
        print("  설치: winget install -e --id JRSoftware.InnoSetup")
        print("  Installer 빌드를 건너뜁니다.")
        return False

    print(f"  ISCC: {iscc}")

    iss_file = BUILD_DIR / "installer.iss"
    if not iss_file.exists():
        print(f"  installer.iss 파일 없음: {iss_file}")
        return False

    cmd = [str(iscc), str(iss_file)]
    print(f"  명령: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(BUILD_DIR))

    if result.returncode != 0:
        print("  Installer 빌드 실패!")
        return False

    setup_exe = DIST_DIR / "WellcomSOFT_Setup.exe"
    if setup_exe.exists():
        size_mb = setup_exe.stat().st_size / 1024 / 1024
        print(f"  Installer 빌드 완료: {setup_exe} ({size_mb:.1f} MB)")
        return True
    else:
        print("  Installer 출력 파일을 찾을 수 없습니다.")
        return False


def verify_build():
    """빌드 결과물 검증"""
    print("\n=== 빌드 검증 ===")

    checks = [
        (DIST_DIR / "WellcomSOFT" / "WellcomSOFT.exe", "WellcomSOFT.exe"),
        (DIST_DIR / "WellcomSOFT_Setup.exe", "WellcomSOFT_Setup.exe (Installer)"),
    ]

    all_ok = True
    for path, name in checks:
        if path.exists():
            size_mb = path.stat().st_size / 1024 / 1024
            print(f"  OK: {name} ({size_mb:.1f} MB)")
        else:
            print(f"  SKIP: {name} 없음")

    # WellcomSOFT.exe는 필수
    if not (DIST_DIR / "WellcomSOFT" / "WellcomSOFT.exe").exists():
        print("\n  빌드 검증 실패! WellcomSOFT.exe 없음")
        sys.exit(1)

    print("\n  빌드 검증 통과!")


def print_deploy_info():
    """배포 안내 메시지"""
    setup_exe = DIST_DIR / "WellcomSOFT_Setup.exe"

    print("\n" + "=" * 50)
    print("  배포 안내")
    print("=" * 50)

    if setup_exe.exists():
        size_mb = setup_exe.stat().st_size / 1024 / 1024
        print(f"  Installer: WellcomSOFT_Setup.exe ({size_mb:.1f} MB)")
        print(f"  설치 경로: C:\\WellcomSOFT\\")
        print(f"  바탕화면 바로가기 자동 생성")
        print()
        print("  사용자 배포:")
        print(f"  → WellcomSOFT_Setup.exe 전달 (단일 파일)")
        print("  → 실행하면 C:\\WellcomSOFT에 자동 설치")
    else:
        print("  Installer 미생성 - PyInstaller 결과만 있음")

    print()
    print("  업데이트 릴리스:")
    print("  python build/package_app.py → dist/app.zip")
    print("  GitHub Release에 app.zip 첨부")
    print("=" * 50)


def main():
    # 버전 표시
    sys.path.insert(0, str(PROJECT_DIR))
    try:
        from version import __version__
        ver = __version__
    except Exception:
        ver = "unknown"

    print(f"WellcomSOFT v{ver} 빌드 스크립트")
    print(f"프로젝트: {PROJECT_DIR}")
    print(f"출력: {DIST_DIR}")
    print()

    clean()
    build_exe()
    create_data_dir()
    compile_app_to_pyc()
    build_installer()
    verify_build()
    print_deploy_info()

    setup_exe = DIST_DIR / "WellcomSOFT_Setup.exe"
    if setup_exe.exists():
        print(f"\n완료! Installer: {setup_exe}")
    else:
        print(f"\n완료! EXE: {DIST_DIR / 'WellcomSOFT' / 'WellcomSOFT.exe'}")


if __name__ == "__main__":
    main()
