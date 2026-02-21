"""
WellcomSOFT 런처
- PyInstaller EXE의 엔트리포인트
- 고정 설치 경로: C:\\WellcomSOFT
- EXE 위치에 관계없이 항상 C:\\WellcomSOFT에서 실행
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

LAUNCHER_VERSION = "3.0.0"

# ───────────────────────────────────────────
# 매니저 업데이트 설정 (런처에 하드코딩)
# ───────────────────────────────────────────
GITHUB_REPO = "hy0567/wellcom_soft"
ASSET_NAME = "app.zip"

# ───────────────────────────────────────────
# 경로 설정
# ───────────────────────────────────────────
INSTALL_DIR = Path(r"C:\WellcomSOFT")   # 고정 설치 경로
APP_DIR = INSTALL_DIR / "app"
DATA_DIR = INSTALL_DIR / "data"
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
        # ShellExecuteW: 관리자 권한으로 재실행
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, None, 1
        )
    except Exception as e:
        print(f"관리자 권한 요청 실패: {e}")


# ───────────────────────────────────────────
# 설치 확인
# ───────────────────────────────────────────
def ensure_install_dir():
    """C:\\WellcomSOFT 설치 디렉터리 생성

    첫 실행 시 관리자 권한으로 폴더를 생성하고,
    이후에는 관리자 권한 없이 접근 가능하도록 설정.
    """
    logger = logging.getLogger('Launcher')

    # 이미 존재하면 별도 권한 불필요
    if INSTALL_DIR.exists():
        return True

    logger.info(f"설치 디렉터리 생성: {INSTALL_DIR}")

    # 관리자 권한 없으면 요청
    if not is_admin():
        logger.info("관리자 권한 필요 - UAC 요청")
        request_admin()
        sys.exit(0)

    # 디렉터리 구조 생성
    try:
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        APP_DIR.mkdir(exist_ok=True)
        DATA_DIR.mkdir(exist_ok=True)
        LOG_DIR.mkdir(exist_ok=True)
        logger.info("설치 디렉터리 생성 완료")

        # 일반 사용자도 접근할 수 있도록 권한 설정
        _set_directory_permissions(INSTALL_DIR)
        return True
    except Exception as e:
        logger.error(f"설치 디렉터리 생성 실패: {e}")
        return False


def _set_directory_permissions(path: Path):
    """디렉터리 권한을 일반 사용자도 읽기/쓰기 가능하게 설정"""
    try:
        import subprocess
        # Everyone에게 Full Control 부여
        subprocess.run(
            ['icacls', str(path), '/grant', 'Everyone:(OI)(CI)F', '/T', '/Q'],
            capture_output=True, timeout=30
        )
    except Exception:
        pass  # 실패해도 관리자 권한으로는 접근 가능


# ───────────────────────────────────────────
# 데이터 마이그레이션
# ───────────────────────────────────────────
def migrate_data_if_needed():
    """이전 EXE 위치의 data/를 C:\\WellcomSOFT\\data/로 마이그레이션

    기존 사용자가 EXE 폴더 기준으로 data/를 사용하던 경우,
    C:\\WellcomSOFT\\data/로 복사 (원본은 보존).
    """
    logger = logging.getLogger('Launcher')

    exe_dir = _get_exe_dir()

    # EXE가 이미 C:\WellcomSOFT에 있으면 마이그레이션 불필요
    try:
        if exe_dir.resolve() == INSTALL_DIR.resolve():
            return
    except Exception:
        pass

    # 이전 EXE 위치의 data/ 확인
    old_data_dir = exe_dir / "data"
    if not old_data_dir.exists() or not old_data_dir.is_dir():
        return

    # 새 data/에 이미 DB가 있으면 스킵 (이미 마이그레이션됨)
    new_db = DATA_DIR / "kvm_devices.db"
    if new_db.exists():
        return

    logger.info(f"데이터 마이그레이션: {old_data_dir} → {DATA_DIR}")
    try:
        DATA_DIR.mkdir(exist_ok=True)
        shutil.copytree(old_data_dir, DATA_DIR, dirs_exist_ok=True)
        logger.info("마이그레이션 완료")
    except Exception as e:
        logger.error(f"마이그레이션 실패: {e}")

    # 이전 위치의 backup/ 도 마이그레이션
    old_backup = exe_dir / "backup"
    if old_backup.exists() and old_backup.is_dir():
        try:
            BACKUP_DIR.mkdir(exist_ok=True)
            shutil.copytree(old_backup, BACKUP_DIR, dirs_exist_ok=True)
            logger.info("백업 마이그레이션 완료")
        except Exception:
            pass


# ───────────────────────────────────────────
# 로깅
# ───────────────────────────────────────────
def setup_logging():
    """로그 설정

    console=False EXE에서는 sys.stdout이 None이므로
    StreamHandler 대신 FileHandler만 사용.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        # 설치 전이면 로그 디렉터리가 없을 수 있음
        pass

    handlers = []

    # stdout이 유효할 때만 StreamHandler 추가 (console=False EXE에서는 None)
    if sys.stdout is not None and hasattr(sys.stdout, 'write'):
        handlers.append(logging.StreamHandler(sys.stdout))

    try:
        handlers.append(
            logging.FileHandler(LOG_DIR / 'wellcomsoft.log', encoding='utf-8')
        )
    except Exception:
        pass

    # 핸들러가 하나도 없으면 NullHandler
    if not handlers:
        handlers.append(logging.NullHandler())

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=handlers
    )


# ───────────────────────────────────────────
# Pending Update 처리
# ───────────────────────────────────────────
def apply_pending_update():
    """Pending 업데이트 적용 (프로그램 재시작 후 실행)

    업데이트 과정에서 파일 잠금으로 교체 실패 시,
    temp/pending_update.zip을 남겨두고 재시작 후 여기서 적용.
    """
    logger = logging.getLogger('Launcher')
    pending_flag = TEMP_DIR / "pending_update.flag"
    pending_zip = TEMP_DIR / "pending_update.zip"

    if not pending_flag.exists() or not pending_zip.exists():
        return

    logger.info("Pending 업데이트 발견 - 적용 중...")

    try:
        import zipfile

        # app/ 삭제 후 재생성
        if APP_DIR.exists():
            shutil.rmtree(APP_DIR, ignore_errors=True)
        APP_DIR.mkdir(exist_ok=True)

        # zip 해제
        with zipfile.ZipFile(pending_zip, 'r') as zf:
            zf.extractall(APP_DIR)

        logger.info("Pending 업데이트 적용 완료")

    except Exception as e:
        logger.error(f"Pending 업데이트 적용 실패: {e}")
    finally:
        # 정리
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
    """현재 설치된 매니저 버전 읽기"""
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


def check_and_apply_update(on_status=None, on_progress=None):
    """GitHub Release에서 최신 app.zip 확인 -> 다운로드 -> app/ 교체

    app/ 로드 전에 실행. 실패해도 기존 app/로 정상 실행.

    Args:
        on_status: 상태 메시지 콜백 (str)
        on_progress: 진행률 콜백 (downloaded, total)

    Returns:
        dict: {'updated': bool, 'current': str, 'latest': str}
    """
    logger = logging.getLogger('Launcher')
    result = {'updated': False, 'current': '0.0.0', 'latest': '0.0.0'}

    def _status(msg):
        if on_status:
            on_status(msg)
        logger.info(msg)

    if not APP_DIR.exists():
        return result

    try:
        import requests
        _HAS_REQUESTS = True
    except ImportError:
        try:
            import urllib.request
            import ssl
            _HAS_REQUESTS = False
        except ImportError:
            logger.debug("HTTP 라이브러리 없음 — 업데이트 스킵")
            return result

    current_version = _get_installed_version()
    result['current'] = current_version
    _status(f"업데이트 확인 중... (v{current_version})")

    # 1) GitHub API 최신 릴리스 조회
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    headers = {"Accept": "application/vnd.github+json"}

    try:
        if _HAS_REQUESTS:
            resp = requests.get(api_url, headers=headers, timeout=10)
            if resp.status_code != 200:
                return result
            data = resp.json()
        else:
            req = urllib.request.Request(api_url, headers=headers)
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.debug(f"업데이트 확인 실패 (네트워크): {e}")
        return result

    latest_version = data.get("tag_name", "").lstrip("v")
    if not latest_version:
        return result
    result['latest'] = latest_version

    if not _compare_versions(current_version, latest_version):
        _status(f"최신 버전입니다 (v{current_version})")
        return result

    _status(f"v{current_version} → v{latest_version} 다운로드 중...")

    # 2) app.zip 다운로드 URL 찾기
    download_url = None
    total_size = 0
    for asset in data.get("assets", []):
        if asset["name"] == ASSET_NAME:
            download_url = asset["browser_download_url"]
            total_size = asset.get("size", 0)
            break

    if not download_url:
        logger.warning(f"릴리스에 {ASSET_NAME} 에셋 없음")
        return result

    # 3) SHA256 체크섬 파싱
    expected_checksum = ""
    body = data.get("body", "")
    for line in body.split('\n'):
        if f'SHA256({ASSET_NAME})' in line and ':' in line:
            expected_checksum = line.split(':', 1)[1].strip().strip('` ')
            break
        elif ASSET_NAME in line and 'SHA256' in line.upper() and ':' in line:
            # 마크다운 형식: `app.zip` SHA256: `hash`
            expected_checksum = line.split(':')[-1].strip().strip('` ')
            break
        elif 'SHA256' in line.upper() and '(' not in line and ':' in line:
            expected_checksum = line.split(':')[-1].strip().strip('` ')
            break

    # 4) 다운로드 (프로그레스 콜백 지원)
    TEMP_DIR.mkdir(exist_ok=True)
    zip_path = TEMP_DIR / ASSET_NAME

    try:
        downloaded = 0
        if _HAS_REQUESTS:
            resp = requests.get(download_url, stream=True, timeout=60)
            resp.raise_for_status()
            content_length = int(resp.headers.get('content-length', total_size))
            with open(zip_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress and content_length:
                        on_progress(downloaded, content_length)
        else:
            req = urllib.request.Request(download_url)
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                content_length = int(resp.headers.get('Content-Length', total_size))
                with open(zip_path, 'wb') as f:
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if on_progress and content_length:
                            on_progress(downloaded, content_length)

        logger.info(f"다운로드 완료: {zip_path} ({zip_path.stat().st_size} bytes)")
    except Exception as e:
        logger.warning(f"다운로드 실패: {e}")
        _cleanup_temp(zip_path)
        return result

    _status("검증 중...")

    # 5) 체크섬 검증
    if expected_checksum:
        sha256 = hashlib.sha256()
        with open(zip_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        actual = sha256.hexdigest()
        if actual != expected_checksum:
            logger.error(f"체크섬 불일치!")
            _cleanup_temp(zip_path)
            return result
        logger.info("체크섬 검증 통과")

    # 6) ZIP 유효성 확인
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            if not zf.namelist():
                _cleanup_temp(zip_path)
                return result
    except zipfile.BadZipFile:
        _cleanup_temp(zip_path)
        return result

    _status("업데이트 적용 중...")

    # 7) 현재 app/ 백업
    try:
        BACKUP_DIR.mkdir(exist_ok=True)
        backup_path = BACKUP_DIR / f"app_v{current_version}.zip"
        if APP_DIR.exists():
            with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for fp in APP_DIR.rglob('*'):
                    if fp.is_file() and '__pycache__' not in str(fp):
                        zf.write(fp, fp.relative_to(APP_DIR))
    except Exception as e:
        logger.warning(f"백업 생성 실패 (계속 진행): {e}")

    # 8) app/ 교체
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

        result['updated'] = True
        _status(f"업데이트 완료! v{latest_version}")
        logger.info(f"업데이트 적용 완료: v{current_version} -> v{latest_version}")
    except Exception as e:
        logger.error(f"업데이트 적용 실패: {e}")
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
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        for old in backups[3:]:
            old.unlink()
    except Exception:
        pass

    return result


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
# 업데이트 스플래시 UI (PyQt6)
# ───────────────────────────────────────────
def _create_splash_ui():
    """UpdateSplashDialog + UpdateWorkerThread 클래스 생성

    PyQt6가 없는 환경(에이전트 등)에서도 런처가 동작하도록
    함수 내부에서 import + 클래스 정의.
    """
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar
    from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
    from PyQt6.QtGui import QFont

    _SPLASH_STYLE = """
    QDialog {
        background: #1e1e1e;
        border: 1px solid #333333;
    }
    QLabel {
        color: #e0e0e0;
    }
    QLabel#app_title {
        color: #2196F3;
        font-weight: bold;
    }
    QLabel#status {
        color: #aaaaaa;
        font-size: 11px;
    }
    QLabel#pct {
        color: #888888;
        font-size: 10px;
    }
    QProgressBar {
        border: none;
        border-radius: 4px;
        background: #333333;
        height: 8px;
        text-align: center;
    }
    QProgressBar::chunk {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #4CAF50, stop:1 #66BB6A);
        border-radius: 4px;
    }
    """

    class UpdateWorkerThread(QThread):
        """업데이트 확인/적용 워커 스레드"""
        status_changed = pyqtSignal(str)
        progress_changed = pyqtSignal(int, int)
        finished = pyqtSignal(dict)  # {'updated': bool, 'current': str, 'latest': str}

        def run(self):
            result = check_and_apply_update(
                on_status=lambda msg: self.status_changed.emit(msg),
                on_progress=lambda d, t: self.progress_changed.emit(d, t),
            )
            self.finished.emit(result)

    class UpdateSplashDialog(QDialog):
        """업데이트 스플래시 화면

        360×180, 프레임리스, 다크 테마.
        업데이트 확인/다운로드/적용 진행 상황을 시각적으로 표시.
        """

        def __init__(self):
            super().__init__()
            self._result = {'updated': False, 'current': '0.0.0', 'latest': '0.0.0'}
            self._init_ui()

        def _init_ui(self):
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Dialog
            )
            self.setFixedSize(360, 180)
            self.setStyleSheet(_SPLASH_STYLE)

            layout = QVBoxLayout(self)
            layout.setContentsMargins(30, 24, 30, 24)
            layout.setSpacing(8)

            # 앱 타이틀
            title = QLabel("WellcomSOFT")
            title.setObjectName("app_title")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title_font = QFont()
            title_font.setPointSize(16)
            title_font.setBold(True)
            title.setFont(title_font)
            layout.addWidget(title)

            # 서브 타이틀
            sub = QLabel("소프트웨어 기반 다중 PC 원격 관리")
            sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sub.setStyleSheet("color: #666; font-size: 10px;")
            layout.addWidget(sub)

            layout.addSpacing(12)

            # 상태 메시지
            self.status_label = QLabel("시작 중...")
            self.status_label.setObjectName("status")
            self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self.status_label)

            # 프로그레스바
            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.progress_bar.setTextVisible(False)
            layout.addWidget(self.progress_bar)

            # 퍼센트
            self.pct_label = QLabel("")
            self.pct_label.setObjectName("pct")
            self.pct_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self.pct_label)

            layout.addStretch()

        def start_update(self):
            """워커 스레드 시작"""
            self.worker = UpdateWorkerThread()
            self.worker.status_changed.connect(self._on_status)
            self.worker.progress_changed.connect(self._on_progress)
            self.worker.finished.connect(self._on_finished)
            self.worker.start()

        def _on_status(self, msg):
            self.status_label.setText(msg)

        def _on_progress(self, downloaded, total):
            if total > 0:
                pct = int(downloaded / total * 100)
                self.progress_bar.setValue(pct)
                mb_down = downloaded / (1024 * 1024)
                mb_total = total / (1024 * 1024)
                self.pct_label.setText(f"{pct}%  ({mb_down:.1f}/{mb_total:.1f} MB)")

        def _on_finished(self, result):
            self._result = result
            if result.get('updated'):
                self.status_label.setText(
                    f"업데이트 완료! v{result['latest']} — 앱 시작 중..."
                )
                self.progress_bar.setValue(100)
                self.pct_label.setText("100%")
            else:
                current = result.get('current', '0.0.0')
                self.status_label.setText(f"최신 버전입니다 (v{current})")
                self.progress_bar.setValue(100)
                self.pct_label.setText("")

            # 1.5초 후 자동 닫기
            QTimer.singleShot(1500, self.accept)

        @property
        def result(self):
            return self._result

    return UpdateSplashDialog


# ───────────────────────────────────────────
# App 초기화
# ───────────────────────────────────────────
def ensure_app_dir():
    """app/ 디렉터리 확인 및 최초 설정

    PyInstaller 빌드 시 _internal/app/ 에 코드가 포함됨.
    최초 실행 시 이를 C:\\WellcomSOFT\\app/ 로 복사.
    """
    logger = logging.getLogger('Launcher')

    if APP_DIR.exists() and (any(APP_DIR.glob("main.py")) or any(APP_DIR.glob("main.pyc"))):
        return  # 이미 존재

    logger.info("최초 실행 - app/ 디렉터리 초기화")
    APP_DIR.mkdir(exist_ok=True)

    # PyInstaller 내부에서 app 코드 찾기
    if getattr(sys, 'frozen', False):
        # _MEIPASS/app/ 에서 복사
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
    """app/ 폴더의 main 모듈을 로드하여 실행 (.py 또는 .pyc)"""
    logger = logging.getLogger('Launcher')

    # app/ 를 sys.path 최상위에 추가
    app_path = str(APP_DIR)
    if app_path not in sys.path:
        sys.path.insert(0, app_path)

    # v2.0.8: app/_vendor/ 를 sys.path에 추가 (번들된 서드파티 패키지)
    vendor_path = str(APP_DIR / "_vendor")
    if os.path.isdir(vendor_path) and vendor_path not in sys.path:
        sys.path.insert(1, vendor_path)
        logger.info(f"번들 패키지 경로 추가: {vendor_path}")

    # 환경변수 전달
    os.environ['WELLCOMSOFT_BASE_DIR'] = str(INSTALL_DIR)
    os.environ['WELLCOMSOFT_EXE_PATH'] = str(_get_exe_path())

    # .pyc 또는 .py 확인
    has_pyc = (APP_DIR / "main.pyc").exists()
    has_py = (APP_DIR / "main.py").exists()
    logger.info(f"앱 로드: {'main.pyc (바이트코드)' if has_pyc else 'main.py (소스)'}")

    import importlib.util

    if has_pyc and not has_py:
        main_file = APP_DIR / "main.pyc"
    elif has_py:
        main_file = APP_DIR / "main.py"
    else:
        raise FileNotFoundError(f"main.py/main.pyc 파일을 찾을 수 없습니다: {APP_DIR}")

    # 항상 spec_from_file_location 사용 (PyInstaller FrozenImporter 우회)
    spec = importlib.util.spec_from_file_location("main", str(main_file))
    if spec is None:
        raise ImportError(f"모듈 스펙 생성 실패: {main_file}")
    main_module = importlib.util.module_from_spec(spec)
    sys.modules['main'] = main_module
    spec.loader.exec_module(main_module)

    main_module.main()


# ───────────────────────────────────────────
# 메인
# ───────────────────────────────────────────
def main():
    """런처 메인"""
    setup_logging()
    logger = logging.getLogger('Launcher')
    logger.info(f"WellcomSOFT Launcher v{LAUNCHER_VERSION}")
    logger.info(f"설치 경로: {INSTALL_DIR}")
    logger.info(f"EXE 위치: {_get_exe_dir()}")

    # 1. 설치 디렉터리 확인 (필요 시 관리자 권한 요청)
    if not ensure_install_dir():
        input("설치 실패. 엔터를 눌러 종료...")
        sys.exit(1)

    # 2. 필요 디렉터리 보장
    DATA_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)

    # 로그 파일 핸들러 재설정 (설치 후 로그 경로 확정)
    for handler in logging.root.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            handler.close()
            logging.root.removeHandler(handler)
    try:
        logging.root.addHandler(
            logging.FileHandler(LOG_DIR / 'wellcomsoft.log', encoding='utf-8')
        )
    except Exception:
        pass

    # 3. 이전 위치 데이터 마이그레이션
    migrate_data_if_needed()

    # 4. Pending 업데이트 적용
    apply_pending_update()

    # 5. app/ 디렉터리 확인 (최초 실행 시 복사)
    ensure_app_dir()

    # 6. PyQt6 스플래시 + 자동 업데이트
    _run_splash_update(logger)

    # 7. 앱 실행
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


def _run_splash_update(logger):
    """자동 업데이트 확인/적용

    업데이트가 있을 때만 PyQt6 스플래시를 표시.
    업데이트가 없으면 스플래시 없이 바로 앱 실행으로 진행 (빠른 시작).
    """
    # 먼저 업데이트 유무만 빠르게 확인 (스플래시 없이)
    try:
        import requests as _req
        _HAS_REQ = True
    except ImportError:
        _HAS_REQ = False

    has_update = False
    latest_version = ""
    current_version = _get_installed_version()

    try:
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        headers = {"Accept": "application/vnd.github+json"}

        if _HAS_REQ:
            resp = _req.get(api_url, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                latest_version = data.get("tag_name", "").lstrip("v")
                if latest_version and _compare_versions(current_version, latest_version):
                    has_update = True
        else:
            import urllib.request, ssl
            req = urllib.request.Request(api_url, headers=headers)
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                latest_version = data.get("tag_name", "").lstrip("v")
                if latest_version and _compare_versions(current_version, latest_version):
                    has_update = True
    except Exception as e:
        logger.debug(f"업데이트 확인 실패 (네트워크): {e}")
        return  # 네트워크 오류 — 스킵

    if not has_update:
        logger.info(f"최신 버전 (v{current_version}) — 스플래시 없이 바로 시작")
        return

    # 업데이트 있음 → 스플래시 UI로 다운로드/적용
    logger.info(f"업데이트 발견: v{current_version} → v{latest_version}")

    try:
        from PyQt6.QtWidgets import QApplication

        splash_app = QApplication(sys.argv)
        splash_app.setStyle('Fusion')

        UpdateSplashDialog = _create_splash_ui()
        splash = UpdateSplashDialog()

        screen = splash_app.primaryScreen()
        if screen:
            geo = screen.geometry()
            x = (geo.width() - splash.width()) // 2
            y = (geo.height() - splash.height()) // 2
            splash.move(x, y)

        splash.show()
        splash.start_update()
        splash_app.exec()

        result = splash.result
        if result.get('updated'):
            logger.info(f"업데이트 적용: v{result['current']} → v{result['latest']}")
        else:
            logger.info(f"업데이트 적용 실패 (기존 버전으로 계속)")

        # QApplication 정리 (main.py에서 새로 생성할 수 있도록)
        del splash
        del splash_app

    except ImportError:
        logger.debug("PyQt6 없음 — 스플래시 없이 업데이트 실행")
        try:
            check_and_apply_update()
        except Exception as e:
            logger.warning(f"자동 업데이트 확인 실패 (무시): {e}")
    except Exception as e:
        logger.warning(f"스플래시 업데이트 실패 (무시): {e}")
        try:
            check_and_apply_update()
        except Exception:
            pass


if __name__ == "__main__":
    main()
