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


# ==================== 로깅 ====================

class LogTee:
    """stdout/stderr → 콘솔 + 파일 동시 출력"""

    def __init__(self, log_path: str, stream):
        self._stream = stream
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self._file = open(log_path, 'a', encoding='utf-8', buffering=1)

    def write(self, msg):
        if msg:
            try:
                self._stream.write(msg)
            except Exception:
                pass
            try:
                self._file.write(msg)
            except Exception:
                pass

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass
        try:
            self._file.flush()
        except Exception:
            pass

    def fileno(self):
        return self._stream.fileno()


def setup_logging():
    """로깅 설정"""
    log_path = os.path.join(LOG_DIR, "app.log")

    # 로그 파일 크기 제한 (1MB)
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 1_000_000:
            backup = log_path + '.bak'
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(log_path, backup)
    except Exception:
        pass

    # EXE 환경에서 stdout/stderr 리디렉트
    if getattr(sys, 'frozen', False):
        sys.stdout = LogTee(log_path, sys.stdout or sys.__stdout__)
        sys.stderr = LogTee(log_path, sys.stderr or sys.__stderr__)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        handlers=[logging.StreamHandler()],
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

    # 에이전트 서버 시작
    from core.agent_server import AgentServer
    agent_port = settings.get('agent_server.port', 9877)
    agent_server = AgentServer(port=agent_port)
    agent_server.start_server()
    logger.info(f"에이전트 서버 시작: 포트 {agent_port}")

    # PC 매니저 초기화
    from core.pc_manager import PCManager
    pc_manager = PCManager(agent_server)

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
    agent_server.stop_server()
    logger.info("WellcomSOFT 종료")

    sys.exit(exit_code)


if __name__ == '__main__':
    main()
