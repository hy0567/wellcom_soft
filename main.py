"""WellcomSOFT — 소프트웨어 기반 다중 PC 원격 관리 시스템

진입점. PyQt6 애플리케이션 초기화 및 실행.
"""

import sys
import os
import logging
import traceback
import subprocess
from io import TextIOWrapper


def _get_base_dir():
    env_base = os.environ.get('WELLCOMSOFT_BASE_DIR')
    if env_base and os.path.isdir(env_base):
        return env_base
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _get_base_dir()
LOG_DIR = os.path.join(BASE_DIR, "logs")

# v2.0.8: app/_vendor/ 서드파티 패키지 경로 추가
# PyInstaller 빌드에서 websockets 등이 누락된 경우 대비
_vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor")
if os.path.isdir(_vendor_dir) and _vendor_dir not in sys.path:
    sys.path.insert(0, _vendor_dir)


# ==================== 로깅 ====================

class LogTee:
    """stdout/stderr → 콘솔 + 파일 동시 출력

    console=False EXE에서 sys.stdout/stderr가 None인 경우를 안전하게 처리.
    """

    def __init__(self, log_path: str, stream=None):
        self._stream = stream  # None일 수 있음 (console=False EXE)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self._file = open(log_path, 'a', encoding='utf-8', buffering=1)

    def write(self, msg):
        if msg:
            if self._stream is not None:
                try:
                    self._stream.write(msg)
                except Exception:
                    pass
            try:
                self._file.write(msg)
            except Exception:
                pass

    def flush(self):
        if self._stream is not None:
            try:
                self._stream.flush()
            except Exception:
                pass
        try:
            self._file.flush()
        except Exception:
            pass

    def fileno(self):
        if self._stream is not None:
            return self._stream.fileno()
        raise OSError("underlying stream is None")


def setup_logging():
    """로깅 설정

    EXE 환경 (frozen):
      - sys.stdout = LogTee(app.log) 으로 교체 → print()가 파일에 기록됨
      - logging은 FileHandler(app.log) 1개만 등록 (LogTee 경유 안 함 → 중복 방지)
    개발 환경:
      - StreamHandler(stdout) → 콘솔 출력

    중요: 런처가 이미 root logger에 핸들러를 등록했을 수 있으므로 모두 제거 후 재등록.
    """
    log_path = os.path.join(LOG_DIR, "app.log")

    # 로그 디렉터리 보장
    os.makedirs(LOG_DIR, exist_ok=True)

    # 로그 파일 크기 제한 (1MB)
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 1_000_000:
            backup = log_path + '.bak'
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(log_path, backup)
    except Exception:
        pass

    # EXE 환경에서 stdout/stderr 리디렉트 (print → 파일)
    if getattr(sys, 'frozen', False):
        sys.stdout = LogTee(log_path, sys.stdout)
        sys.stderr = LogTee(log_path, sys.stderr)

    # ★ 기존 핸들러 모두 제거 (런처가 등록한 깨진/중복 핸들러 포함)
    root = logging.getLogger()
    for h in root.handlers[:]:
        try:
            h.close()
        except Exception:
            pass
        root.removeHandler(h)

    # 핸들러 등록 (1개만 — 중복 방지)
    handler = None
    if getattr(sys, 'frozen', False):
        # EXE: FileHandler만 (LogTee가 stdout/print 담당, logging은 파일 직접)
        try:
            handler = logging.FileHandler(log_path, encoding='utf-8')
        except Exception:
            pass
    else:
        # 개발 환경: 콘솔 출력
        handler = logging.StreamHandler(sys.stdout)

    if handler is None:
        handler = logging.NullHandler()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        handlers=[handler],
    )


# ==================== 크래시 핸들러 ====================

def install_crash_handler():
    """전역 예외 핸들러"""
    def handler(exc_type, exc_value, exc_tb):
        msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logging.critical(f"치명적 오류:\n{msg}")
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = handler


# ==================== 업데이트 ====================

def check_for_updates(app) -> bool:
    """시작 시 업데이트 확인. True=정상진행, False=재시작필요"""
    try:
        from pathlib import Path
        from config import settings
        from version import __version__, __github_repo__
        from updater import UpdateChecker
        from updater.update_dialog import UpdateNotifyDialog, UpdateDialog

        if not settings.get('update.auto_check', True):
            return True

        token = settings.get('update.github_token', '')
        checker = UpdateChecker(
            Path(BASE_DIR), __github_repo__, token or None,
            running_version=__version__,
        )

        has_update, release_info = checker.check_update()
        if not has_update or not release_info:
            return True

        # 스킵 버전 확인
        skip_ver = settings.get('update.skip_version', '')
        if skip_ver and release_info.version == skip_ver:
            return True

        # 알림 다이얼로그
        notify = UpdateNotifyDialog(checker.get_current_version(), release_info)
        result = notify.exec()
        if result == 0:
            return True

        # 업데이트 진행
        dlg = UpdateDialog(release_info)
        dlg.start_update(checker)
        dlg.exec()

        if dlg.is_success:
            _restart_application()
            return False

        return True
    except Exception as e:
        logging.getLogger('WellcomSOFT').debug(f"업데이트 확인 실패: {e}")
        return True


def _restart_application():
    """프로그램 재시작"""
    # 1) 런처가 설정한 EXE 경로
    exe_path = os.environ.get('WELLCOMSOFT_EXE_PATH')
    if exe_path and os.path.exists(exe_path):
        print(f"[Restart] EXE 경로: {exe_path}")
        subprocess.Popen([exe_path])
        sys.exit(0)

    # 2) 설치 디렉터리 기준 EXE
    base_dir = os.environ.get('WELLCOMSOFT_BASE_DIR')
    if base_dir:
        candidate = os.path.join(base_dir, 'WellcomSOFT.exe')
        if os.path.exists(candidate):
            print(f"[Restart] BASE_DIR 기준: {candidate}")
            subprocess.Popen([candidate])
            sys.exit(0)

    # 3) Fallback
    if getattr(sys, 'frozen', False):
        subprocess.Popen([sys.executable])
    else:
        subprocess.Popen([sys.executable] + sys.argv)
    sys.exit(0)


# ==================== 메인 ====================

def main():
    setup_logging()
    install_crash_handler()

    logger = logging.getLogger('WellcomSOFT')
    logger.info("=" * 50)
    logger.info("WellcomSOFT 시작")
    logger.info(f"Base: {BASE_DIR}")
    logger.info(f"Python: {sys.version}")
    logger.info("=" * 50)

    # PyQt6
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt

    # High DPI
    os.environ.setdefault('QT_ENABLE_HIGHDPI_SCALING', '1')

    # QApplication: 런처 스플래시가 생성했다 삭제한 경우,
    # 잔존 인스턴스가 있으면 재사용, 없으면 새로 생성
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # 버전 정보
    try:
        from version import __version__, __app_name__
        app.setApplicationName(__app_name__)
        app.setApplicationVersion(__version__)
        logger.info(f"{__app_name__} v{__version__}")
    except ImportError:
        app.setApplicationName("WellcomSOFT")

    # 아이콘 설정
    from config import ICON_PATH
    if ICON_PATH and os.path.exists(ICON_PATH):
        from PyQt6.QtGui import QIcon
        app.setWindowIcon(QIcon(ICON_PATH))

    # 설정 로드
    from config import settings

    # 로그인
    from ui.login_dialog import LoginDialog
    login_dialog = LoginDialog()
    if login_dialog.exec() != LoginDialog.DialogCode.Accepted:
        logger.info("로그인 취소 — 종료")
        sys.exit(0)

    logger.info("로그인 완료")

    # 업데이트 확인 (업데이트 적용 시 재시작)
    if not check_for_updates(app):
        return

    # v3.0.0: P2P 연결 매니저 시작 (LAN→WAN 직접 연결, 서버 릴레이 폴백)
    from core.agent_server import AgentServer
    from api_client import api_client as _api
    agent_server = AgentServer()

    # 서버 URL + JWT 토큰 → P2P 매니저 시작 + 릴레이 폴백 준비
    _server_url = _api._base_url
    _token = _api.token
    logger.info(f"서버 API URL: {_server_url}")
    logger.info(f"JWT 토큰: {_token[:20]}..." if len(_token) > 20 else f"JWT 토큰: {_token}")
    logger.info(f"로그인 사용자: {_api.username} (ID: {_api.user_id})")
    agent_server.start_connection(_server_url, _token)
    logger.info("P2P 연결 매니저 시작 (LAN→WAN→릴레이 폴백)")

    # PC 매니저 초기화
    from core.pc_manager import PCManager
    pc_manager = PCManager(agent_server)

    # 서버에서 에이전트 목록 동기화 + P2P 연결 시도
    logger.info("서버에서 에이전트 목록 동기화 + P2P 연결 시도...")
    pc_manager.load_from_server()
    logger.info(f"에이전트 동기화 완료 (총 {len(pc_manager.pcs)}개)")

    # 메인 윈도우
    from ui.main_window import MainWindow
    window = MainWindow(agent_server, pc_manager)
    window.show()

    logger.info("메인 윈도우 표시 완료")

    # 이벤트 루프
    exit_code = app.exec()

    # 정리
    from api_client import api_client
    api_client.logout()
    agent_server.stop_connection()
    logger.info("WellcomSOFT 종료")

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
