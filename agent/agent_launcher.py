"""
WellcomAgent 런처
- PyInstaller EXE의 엔트리포인트
- 고정 설치 경로: C:\\WellcomAgent
- EXE 위치에 관계없이 항상 C:\\WellcomAgent에서 실행
- app/ 로드 **전에** GitHub에서 자동 업데이트 확인/적용
- app/ 폴더의 코드를 동적 로드하여 실행
- Pending update 처리 (파일 잠금 대응)
"""

import sys
import os
import json
import shutil
import hashlib
import logging
import ctypes
import zipfile
from pathlib import Path

LAUNCHER_VERSION = "2.0.0"

# ───────────────────────────────────────────
# 에이전트 업데이트 설정 (런처에 하드코딩)
# ───────────────────────────────────────────
GITHUB_REPO = "hy0567/wellcom_soft"
ASSET_NAME = "agent.zip"

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
# 자동 업데이트 (app/ 로드 전에 실행)
# ───────────────────────────────────────────
def _get_installed_version() -> str:
    """현재 설치된 에이전트 버전 읽기

    우선순위:
    1. app/version.json (업데이트 후 생성)
    2. app/version.py (__version__ 파싱)
    3. "0.0.0" (최초 설치)
    """
    # 1) version.json
    vj = APP_DIR / "version.json"
    try:
        if vj.exists():
            data = json.loads(vj.read_text(encoding='utf-8'))
            return data.get("version", "0.0.0")
    except Exception:
        pass

    # 2) version.py
    vp = APP_DIR / "version.py"
    try:
        if vp.exists():
            for line in vp.read_text(encoding='utf-8').splitlines():
                if line.startswith('__version__'):
                    return line.split('=', 1)[1].strip().strip('"\'')
    except Exception:
        pass

    return "0.0.0"


def _compare_versions(current: str, latest: str) -> bool:
    """latest > current 이면 True"""
    try:
        def parse(v):
            return tuple(int(x) for x in v.split('.'))
        return parse(latest) > parse(current)
    except Exception:
        return latest != current


def check_and_apply_update():
    """GitHub Release에서 최신 agent.zip 확인 → 다운로드 → app/ 교체

    app/ 로드 **전에** 실행되므로, updater 모듈에 의존하지 않고
    런처 자체에 경량 HTTP 로직을 내장.

    실패해도 기존 app/로 정상 실행 (업데이트는 최선 노력).
    """
    logger = logging.getLogger('AgentLauncher')

    # app/ 디렉터리가 없으면 아직 최초 설치 전 → 스킵
    if not APP_DIR.exists():
        return

    try:
        import requests
    except ImportError:
        # PyInstaller 빌드에서 requests가 없을 수 있음 → urllib 폴백
        try:
            import urllib.request
            import ssl
            _HAS_REQUESTS = False
        except ImportError:
            logger.debug("HTTP 라이브러리 없음 — 업데이트 스킵")
            return
    else:
        _HAS_REQUESTS = True

    current_version = _get_installed_version()
    logger.info(f"현재 에이전트 버전: v{current_version}")

    # ── 1) GitHub API에서 최신 릴리스 조회 ──
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    headers = {"Accept": "application/vnd.github+json"}

    try:
        if _HAS_REQUESTS:
            resp = requests.get(api_url, headers=headers, timeout=10)
            if resp.status_code != 200:
                logger.debug(f"GitHub API 응답: {resp.status_code}")
                return
            data = resp.json()
        else:
            req = urllib.request.Request(api_url, headers=headers)
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.debug(f"업데이트 확인 실패 (네트워크): {e}")
        return

    latest_version = data.get("tag_name", "").lstrip("v")
    if not latest_version:
        return

    if not _compare_versions(current_version, latest_version):
        logger.info(f"최신 버전 사용 중: v{current_version}")
        return

    logger.info(f"업데이트 발견: v{current_version} -> v{latest_version}")

    # ── 2) agent.zip 다운로드 URL 찾기 ──
    download_url = None
    for asset in data.get("assets", []):
        if asset["name"] == ASSET_NAME:
            download_url = asset["browser_download_url"]
            break

    if not download_url:
        logger.warning(f"릴리스에 {ASSET_NAME} 에셋 없음")
        return

    # ── 3) 릴리스 노트에서 SHA256 체크섬 파싱 ──
    expected_checksum = ""
    body = data.get("body", "")
    for line in body.split('\n'):
        if f'SHA256({ASSET_NAME})' in line and ':' in line:
            expected_checksum = line.split(':', 1)[1].strip()
            break
        elif 'SHA256' in line.upper() and '(' not in line and ':' in line:
            expected_checksum = line.split(':')[-1].strip()
            break

    # ── 4) 다운로드 ──
    TEMP_DIR.mkdir(exist_ok=True)
    zip_path = TEMP_DIR / ASSET_NAME

    try:
        logger.info(f"다운로드 중: {ASSET_NAME} (v{latest_version})")
        if _HAS_REQUESTS:
            resp = requests.get(download_url, stream=True, timeout=60)
            resp.raise_for_status()
            with open(zip_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
        else:
            req = urllib.request.Request(download_url)
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                with open(zip_path, 'wb') as f:
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)

        logger.info(f"다운로드 완료: {zip_path} ({zip_path.stat().st_size} bytes)")
    except Exception as e:
        logger.warning(f"다운로드 실패: {e}")
        _cleanup_temp(zip_path)
        return

    # ── 5) 체크섬 검증 ──
    if expected_checksum:
        sha256 = hashlib.sha256()
        with open(zip_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        actual = sha256.hexdigest()
        if actual != expected_checksum:
            logger.error(f"체크섬 불일치! expected={expected_checksum[:16]}... actual={actual[:16]}...")
            _cleanup_temp(zip_path)
            return
        logger.info("체크섬 검증 통과")

    # ── 6) ZIP 유효성 확인 ──
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            names = zf.namelist()
            if not names:
                logger.error("빈 ZIP 파일")
                _cleanup_temp(zip_path)
                return
    except zipfile.BadZipFile:
        logger.error("손상된 ZIP 파일")
        _cleanup_temp(zip_path)
        return

    # ── 7) 현재 app/ 백업 ──
    try:
        BACKUP_DIR.mkdir(exist_ok=True)
        backup_path = BACKUP_DIR / f"app_v{current_version}.zip"
        if APP_DIR.exists():
            with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for fp in APP_DIR.rglob('*'):
                    if fp.is_file() and '__pycache__' not in str(fp):
                        zf.write(fp, fp.relative_to(APP_DIR))
            logger.info(f"백업 생성: {backup_path.name}")
    except Exception as e:
        logger.warning(f"백업 생성 실패 (계속 진행): {e}")

    # ── 8) app/ 교체 ──
    try:
        if APP_DIR.exists():
            shutil.rmtree(APP_DIR, ignore_errors=True)
        APP_DIR.mkdir(exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(APP_DIR)

        # version.json 생성
        version_data = {
            "version": latest_version,
            "checksum": expected_checksum,
            "updated_at": data.get("published_at", ""),
        }
        (APP_DIR / "version.json").write_text(
            json.dumps(version_data, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )

        logger.info(f"업데이트 적용 완료: v{current_version} -> v{latest_version}")
    except Exception as e:
        logger.error(f"업데이트 적용 실패: {e}")
        # 롤백 시도
        try:
            backup_path = BACKUP_DIR / f"app_v{current_version}.zip"
            if backup_path.exists():
                if APP_DIR.exists():
                    shutil.rmtree(APP_DIR, ignore_errors=True)
                APP_DIR.mkdir(exist_ok=True)
                with zipfile.ZipFile(backup_path, 'r') as zf:
                    zf.extractall(APP_DIR)
                logger.info("롤백 완료")
        except Exception as e2:
            logger.error(f"롤백도 실패: {e2}")
    finally:
        _cleanup_temp(zip_path)

    # 오래된 백업 정리 (최대 3개)
    try:
        backups = sorted(
            BACKUP_DIR.glob("app_v*.zip"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        for old in backups[3:]:
            old.unlink()
    except Exception:
        pass


def _cleanup_temp(zip_path: Path):
    """temp/ 정리"""
    try:
        if zip_path.exists():
            zip_path.unlink()
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

    # 5. 자동 업데이트 (app/ 로드 전에 실행)
    try:
        check_and_apply_update()
    except Exception as e:
        logger.warning(f"자동 업데이트 확인 실패 (무시): {e}")

    # 6. 앱 실행
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
