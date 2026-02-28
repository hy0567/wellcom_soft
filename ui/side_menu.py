"""사이드 메뉴바 — 현대적 UI

LinkIO Desktop 스타일 사이드 메뉴 바.
DesktopWidget 옆에 표시, 빠른 조작 버튼 + 섹션 구분 + 테마 지원.
"""

import logging
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QLabel, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from config import settings

logger = logging.getLogger(__name__)


def _side_theme():
    """사이드 메뉴 테마 색상"""
    theme = settings.get('general.theme', 'light')
    if theme == 'dark':
        return {
            'bg': '#1e1e1e', 'border': '#333',
            'btn_bg': '#2a2a2e', 'btn_hover': '#3a3a40', 'btn_pressed': '#094771',
            'btn_text': '#d0d0d0', 'btn_text_hover': '#fff',
            'btn_border': '#3e3e3e',
            'section_text': '#888', 'sep': '#333',
            'close_text': '#e04040', 'close_hover_bg': '#e04040',
        }
    return {
        'bg': '#f8fafc', 'border': '#e2e8f0',
        'btn_bg': '#ffffff', 'btn_hover': '#f1f5f9', 'btn_pressed': '#dbeafe',
        'btn_text': '#475569', 'btn_text_hover': '#1e293b',
        'btn_border': '#e2e8f0',
        'section_text': '#94a3b8', 'sep': '#e2e8f0',
        'close_text': '#ef4444', 'close_hover_bg': '#fef2f2',
    }


class SideMenuButton(QPushButton):
    """사이드 메뉴 버튼 — 테마 지원 + 아이콘 텍스트"""

    def __init__(self, icon_text: str, tooltip: str = '', parent=None):
        super().__init__(icon_text, parent)
        c = _side_theme()
        self.setFixedSize(48, 38)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(QFont("Segoe UI", 11))
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {c['btn_bg']};
                color: {c['btn_text']};
                border: 1px solid {c['btn_border']};
                border-radius: 6px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {c['btn_hover']};
                color: {c['btn_text_hover']};
                border: 1px solid {c['btn_border']};
            }}
            QPushButton:pressed {{
                background-color: {c['btn_pressed']};
            }}
        """)


class SideMenu(QWidget):
    """사이드 메뉴바 — 현대적 UI + 섹션 구분"""

    # 기존 시그널
    clipboard_sync_clicked = pyqtSignal()
    file_send_clicked = pyqtSignal()
    screenshot_clicked = pyqtSignal()
    fullscreen_clicked = pyqtSignal()
    close_clicked = pyqtSignal()
    script_run_clicked = pyqtSignal()
    script_stop_clicked = pyqtSignal()
    command_clicked = pyqtSignal()

    # 화질/FPS 조절
    quality_up_clicked = pyqtSignal()
    quality_down_clicked = pyqtSignal()
    fps_up_clicked = pyqtSignal()
    fps_down_clicked = pyqtSignal()

    # 특수키
    ctrl_alt_del_clicked = pyqtSignal()
    alt_tab_clicked = pyqtSignal()
    win_key_clicked = pyqtSignal()

    # 화면 비율
    ratio_toggle_clicked = pyqtSignal()

    # 모니터 / 오디오
    monitor_clicked = pyqtSignal()
    audio_toggle_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        c = _side_theme()
        self.setFixedWidth(56)
        self.setStyleSheet(
            f"background-color: {c['bg']}; border-left: 1px solid {c['border']};"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 6, 3, 6)
        layout.setSpacing(3)

        # ── 화면 ──
        layout.addWidget(self._section_label("화면", c))

        self.btn_fullscreen = SideMenuButton("[ ]", "전체화면 (F11)")
        self.btn_fullscreen.clicked.connect(self.fullscreen_clicked.emit)
        layout.addWidget(self.btn_fullscreen)

        self.btn_ratio = SideMenuButton("16:9", "화면 비율 전환 (Fit/Stretch)")
        self.btn_ratio.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_ratio.clicked.connect(self.ratio_toggle_clicked.emit)
        layout.addWidget(self.btn_ratio)

        self.btn_monitor = SideMenuButton("MON", "모니터 선택")
        self.btn_monitor.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_monitor.clicked.connect(self.monitor_clicked.emit)
        layout.addWidget(self.btn_monitor)

        layout.addWidget(self._separator(c))

        # ── 품질 ──
        layout.addWidget(self._section_label("품질", c))

        self.btn_quality_up = SideMenuButton("Q+", "화질 올리기 (+10)")
        self.btn_quality_up.clicked.connect(self.quality_up_clicked.emit)
        layout.addWidget(self.btn_quality_up)

        self.btn_quality_down = SideMenuButton("Q-", "화질 내리기 (-10)")
        self.btn_quality_down.clicked.connect(self.quality_down_clicked.emit)
        layout.addWidget(self.btn_quality_down)

        self.btn_fps_up = SideMenuButton("F+", "FPS 올리기 (+5)")
        self.btn_fps_up.clicked.connect(self.fps_up_clicked.emit)
        layout.addWidget(self.btn_fps_up)

        self.btn_fps_down = SideMenuButton("F-", "FPS 내리기 (-5)")
        self.btn_fps_down.clicked.connect(self.fps_down_clicked.emit)
        layout.addWidget(self.btn_fps_down)

        layout.addWidget(self._separator(c))

        # ── 도구 ──
        layout.addWidget(self._section_label("도구", c))

        self.btn_clipboard = SideMenuButton("CB", "클립보드 동기화\n원격 PC와 텍스트 공유")
        self.btn_clipboard.clicked.connect(self.clipboard_sync_clicked.emit)
        layout.addWidget(self.btn_clipboard)

        self.btn_file = SideMenuButton("FILE", "파일 전송\n로컬 파일을 원격 PC로 전송")
        self.btn_file.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_file.clicked.connect(self.file_send_clicked.emit)
        layout.addWidget(self.btn_file)

        self.btn_screenshot = SideMenuButton("CAP", "스크린샷 저장\n현재 화면을 PNG로 저장")
        self.btn_screenshot.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_screenshot.clicked.connect(self.screenshot_clicked.emit)
        layout.addWidget(self.btn_screenshot)

        self.btn_audio = SideMenuButton("AUD", "오디오 켜기/끄기\n원격 PC 소리 스트리밍")
        self.btn_audio.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_audio.clicked.connect(self.audio_toggle_clicked.emit)
        layout.addWidget(self.btn_audio)

        layout.addWidget(self._separator(c))

        # ── 키보드 ──
        layout.addWidget(self._section_label("키", c))

        self.btn_cad = SideMenuButton("CAD", "Ctrl + Alt + Del 전송")
        self.btn_cad.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_cad.clicked.connect(self.ctrl_alt_del_clicked.emit)
        layout.addWidget(self.btn_cad)

        self.btn_alt_tab = SideMenuButton("ALT", "Alt + Tab 전송")
        self.btn_alt_tab.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_alt_tab.clicked.connect(self.alt_tab_clicked.emit)
        layout.addWidget(self.btn_alt_tab)

        self.btn_win = SideMenuButton("WIN", "Windows 키 전송")
        self.btn_win.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_win.clicked.connect(self.win_key_clicked.emit)
        layout.addWidget(self.btn_win)

        layout.addWidget(self._separator(c))

        # ── 명령 ──
        layout.addWidget(self._section_label("실행", c))

        self.btn_command = SideMenuButton("CMD", "명령 실행\n원격 PC에서 명령어 실행")
        self.btn_command.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_command.clicked.connect(self.command_clicked.emit)
        layout.addWidget(self.btn_command)

        self.btn_script_run = SideMenuButton("RUN", "스크립트 실행 (Ctrl+3)")
        self.btn_script_run.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_script_run.clicked.connect(self.script_run_clicked.emit)
        layout.addWidget(self.btn_script_run)

        self.btn_script_stop = SideMenuButton("STP", "스크립트 중지 (Ctrl+4)")
        self.btn_script_stop.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.btn_script_stop.clicked.connect(self.script_stop_clicked.emit)
        layout.addWidget(self.btn_script_stop)

        layout.addStretch()

        # ── 닫기 ──
        self.btn_close = SideMenuButton("X", "뷰어 닫기")
        self.btn_close.setStyleSheet(f"""
            QPushButton {{
                background-color: {c['btn_bg']};
                color: {c['close_text']};
                border: 1px solid {c['btn_border']};
                border-radius: 6px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {c['close_hover_bg']};
                color: {c['close_text']};
            }}
        """)
        self.btn_close.clicked.connect(self.close_clicked.emit)
        layout.addWidget(self.btn_close)

    @staticmethod
    def _section_label(text: str, c: dict) -> QLabel:
        """섹션 헤더 라벨"""
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(QFont("Segoe UI", 7))
        label.setStyleSheet(
            f"color: {c['section_text']}; background: transparent;"
            f" border: none; padding: 1px 0;"
        )
        label.setFixedHeight(14)
        return label

    @staticmethod
    def _separator(c: dict) -> QFrame:
        """섹션 구분선"""
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(
            f"QFrame {{ background-color: {c['sep']}; border: none; }}"
        )
        return sep
