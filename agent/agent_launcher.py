"""
WellcomAgent 런처
- PyInstaller EXE의 엔트리포인트
- 고정 설치 경로: C:\\WellcomAgent
- EXE 위치에 관계없이 항상 C:\\WellcomAgent에서 실행
- app/ 폴더의 코드를 동적 로드하여 실행
- Pending update 처리 (파일 잠금 대응)
"""

import sys
import os
import shutil
import logging
import ctypes
from pathlib import Path

LAUNCHER_VERSION = "1.0.0"

# ───────────────────────────────────────────
# 경로 설정
# ───────────────────────────────────────────
INSTALL_DIR = Path(r"C:\WellcomAgent")   # 고정 설치 경로
APP_DIR = INSTALL_DIR / "app"
LOG_DIR = INSTALL_DIR / "logs"
TEMP_DIR = INSTALL_DIR / "temp"
BACKUP_DIR = INSTALL_DIR / "backup"


def _get_exe_path() -> Path:
    """런처 EXE 실제 경로"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable)
    else:
        return Path(os.path.abspath(__file__))


def _get_exe_dir() -> Path:
    """런처 EXE가 위치한 디렉터리"""
    return _get_exe_path().parent


# ───────────────────────────────────────────
# 관리자 권한 (Windows)
# ───────────────────────────────────────────
def is_admin() -> bool:
    """관리자 권한 확인"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def request_admin():
    """UAC 다이얼로그로 관리자 권한 재실행"""
    try:
        exe = str(_get_exe_path())
        params = ' '.join(sys.argv[1:])
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, None, 1
        )
    except Exception as e:
        print(f"관리자 권한 요청 실패: {e}")


# ───────────────────────────────────────────
# 설치 확인
# ───────────────────────────────────────────
def ensure_install_dir():
    """C:\\WellcomAgent 설치 디렉터리 생성"""
    logger = logging.getLogger('AgentLauncher')

    if INSTALL_DIR.exists():
        return True

    logger.info(f"설치 디렉터리 생성: {INSTALL_DIR}")

    if not is_admin():
        logger.info("관리자 권한 필요 - UAC 요청")
        request_admin()
        sys.exit(0)

    try:
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        APP_DIR.mkdir(exist_ok=True)
        LOG_DIR.mkdir(exist_ok=True)
        logger.info("설치 디렉터리 생성 완료")

        _set_directory_permissions(INSTALL_DIR)
        return True
    except Exception as e:
        logger.error(f"설치 디렉터리 생성 실패: {e}")
        return False


def _set_directory_permissions(path: Path):
    """디렉터리 권한을 일반 사용자도 읽기/쓰기 가능하게 설정"""
    try:
        import subprocess
        subprocess.run(
            ['icacls', str(path), '/grant', 'Everyone:(OI)(CI)F', '/T', '/Q'],
            capture_output=True, timeout=30
        )
    except Exception:
        pass


# ───────────────────────────────────────────
# 로깅
# ───────────────────────────────────────────
def setup_logging():
    """로그 설정"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    handlers = [logging.StreamHandler()]
    try:
        handlers.append(
            logging.FileHandler(LOG_DIR / 'wellcomagent.log', encoding='utf-8')
        )
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=handlers
    )


# ───────────────────────────────────────────
# Pending Update 처리
# ───────────────────────────────────────────
def apply_pending_update():
    """Pending 업데이트 적용 (프로그램 재시작 후 실행)"""
    logger = logging.getLogger('AgentLauncher')
    pending_flag = TEMP_DIR / "pending_update.flag"
    pending_zip = TEMP_DIR / "pending_update.zip"

    if not pending_flag.exists() or not pending_zip.exists():
        return

    logger.info("Pending 업데이트 발견 - 적용 중...")

    try:
        import zipfile

        if APP_DIR.exists():
            shutil.rmtree(APP_DIR, ignore_errors=True)
        APP_DIR.mkdir(exist_ok=True)

        with zipfile.ZipFile(pending_zip, 'r') as zf:
            zf.extractall(APP_DIR)

        logger.info("Pending 업데이트 적용 완료")

    except Exception as e:
        logger.error(f"Pending 업데이트 적용 실패: {e}")
    finally:
        try:
            pending_flag.unlink(missing_ok=True)
            pending_zip.unlink(missing_ok=True)
            if TEMP_DIR.exists() and not any(TEMP_DIR.iterdir()):
                TEMP_DIR.rmdir()
        except Exception:
            pass


# ───────────────────────────────────────────
# App 초기화
# ───────────────────────────────────────────
def ensure_app_dir():
    """app/ 디렉터리 확인 및 최초 설정

    PyInstaller 빌드 시 _internal/app/ 에 코드가 포함됨.
    최초 실행 시 이를 C:\\WellcomAgent\\app/ 로 복사.
    """
    logger = logging.getLogger('AgentLauncher')

    if APP_DIR.exists() and (
        any(APP_DIR.glob("agent_main.py")) or
        any(APP_DIR.glob("agent_main.pyc"))
    ):
        return  # 이미 존재

    logger.info("최초 실행 - app/ 디렉터리 초기화")
    APP_DIR.mkdir(exist_ok=True)

    if getattr(sys, 'frozen', False):
        internal_app = Path(sys._MEIPASS) / "app"
        if internal_app.exists():
            logger.info(f"내부 app 복사: {internal_app} -> {APP_DIR}")
            shutil.copytree(internal_app, APP_DIR, dirs_exist_ok=True)
        else:
            logger.error("내부 app/ 디렉터리를 찾을 수 없습니다.")
    else:
        logger.info("개발환경 - app/ 디렉터리 생성 스킵")


# ───────────────────────────────────────────
# App 실행
# ───────────────────────────────────────────
def load_and_run_app():
    """app/ 폴더의 agent_main 모듈을 로드하여 실행 (.py 또는 .pyc)"""
    logger = logging.getLogger('AgentLauncher')

    # app/ 를 sys.path 최상위에 추가
    app_path = str(APP_DIR)
    if app_path not in sys.path:
        sys.path.insert(0, app_path)

    # 환경변수 전달
    os.environ['WELLCOMAGENT_BASE_DIR'] = str(INSTALL_DIR)
    os.environ['WELLCOMAGENT_EXE_PATH'] = str(_get_exe_path())

    # .pyc 또는 .py 확인
    has_pyc = (APP_DIR / "agent_main.pyc").exists()
    has_py = (APP_DIR / "agent_main.py").exists()
    logger.info(f"앱 로드: {'agent_main.pyc (바이트코드)' if has_pyc else 'agent_main.py (소스)'}")

    import importlib.util

    if has_pyc and not has_py:
        main_file = APP_DIR / "agent_main.pyc"
    elif has_py:
        main_file = APP_DIR / "agent_main.py"
    else:
        raise FileNotFoundError(f"agent_main.py/agent_main.pyc 파일을 찾을 수 없습니다: {APP_DIR}")

    spec = importlib.util.spec_from_file_location("agent_main", str(main_file))
    if spec is None:
        raise ImportError(f"모듈 스펙 생성 실패: {main_file}")
    main_module = importlib.util.module_from_spec(spec)
    sys.modules['agent_main'] = main_module
    spec.loader.exec_module(main_module)

    main_module.main()


# ───────────────────────────────────────────
# 메인
# ───────────────────────────────────────────
def main():
    """런처 메인"""
    setup_logging()
    logger = logging.getLogger('AgentLauncher')
    logger.info(f"WellcomAgent Launcher v{LAUNCHER_VERSION}")
    logger.info(f"설치 경로: {INSTALL_DIR}")
    logger.info(f"EXE 위치: {_get_exe_dir()}")

    # 1. 설치 디렉터리 확인 (필요 시 관리자 권한 요청)
    if not ensure_install_dir():
        input("설치 실패. 엔터를 눌러 종료...")
        sys.exit(1)

    # 2. 필요 디렉터리 보장
    LOG_DIR.mkdir(exist_ok=True)

    # 로그 파일 핸들러 재설정 (설치 후 로그 경로 확정)
    for handler in logging.root.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            handler.close()
            logging.root.removeHandler(handler)
    try:
        logging.root.addHandler(
            logging.FileHandler(LOG_DIR / 'wellcomagent.log', encoding='utf-8')
        )
    except Exception:
        pass

    # 3. Pending 업데이트 적용
    apply_pending_update()

    # 4. app/ 디렉터리 확인 (최초 실행 시 복사)
    ensure_app_dir()

    # 5. 앱 실행
    try:
        load_and_run_app()
    except Exception as e:
        logger.error(f"앱 실행 실패: {e}")
        import traceback
        traceback.print_exc()

        # 긴급 복구: 최신 백업으로 롤백 시도
        if BACKUP_DIR.exists():
            backups = sorted(BACKUP_DIR.glob("app_v*.zip"),
                             key=lambda p: p.stat().st_mtime, reverse=True)
            if backups:
                logger.info(f"긴급 롤백 시도: {backups[0].name}")
                try:
                    import zipfile
                    if APP_DIR.exists():
                        shutil.rmtree(APP_DIR, ignore_errors=True)
                    APP_DIR.mkdir(exist_ok=True)
                    with zipfile.ZipFile(backups[0], 'r') as zf:
                        zf.extractall(APP_DIR)
                    logger.info("롤백 완료 - 앱 재실행")
                    load_and_run_app()
                except Exception as e2:
                    logger.error(f"롤백도 실패: {e2}")

        input("엔터를 눌러 종료...")


if __name__ == "__main__":
    main()
