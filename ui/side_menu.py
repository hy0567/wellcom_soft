"""사이드 메뉴바

LinkIO Desktop 스타일의 사이드 메뉴 바.
DesktopWidget 옆에 표시되며, 빠른 조작 버튼을 제공한다.
"""

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QLabel, QSizePolicy, QToolTip,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QIcon, QFont

logger = logging.getLogger(__name__)


class SideMenuButton(QPushButton):
    """사이드 메뉴 버튼"""

    def __init__(self, text: str, tooltip: str = '', parent=None):
        super().__init__(text, parent)
        self.setFixedSize(40, 36)
        self.setToolTip(tooltip)
        self.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d;
                color: #ccc;
                border: 1px solid #3e3e3e;
                border-radius: 3px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3e3e3e;
                color: #fff;
            }
            QPushButton:pressed {
                background-color: #094771;
            }
        """)


class SideMenu(QWidget):
    """사이드 메뉴바"""

    # 기존 시그널
    clipboard_sync_clicked = pyqtSignal()
    file_send_clicked = pyqtSignal()
    screenshot_clicked = pyqtSignal()
    fullscreen_clicked = pyqtSignal()
    close_clicked = pyqtSignal()
    script_run_clicked = pyqtSignal()
    script_stop_clicked = pyqtSignal()
    command_clicked = pyqtSignal()

    # v2.0.1 — 화질/FPS 조절
    quality_up_clicked = pyqtSignal()
    quality_down_clicked = pyqtSignal()
    fps_up_clicked = pyqtSignal()
    fps_down_clicked = pyqtSignal()

    # v2.0.1 — 특수키
    ctrl_alt_del_clicked = pyqtSignal()
    alt_tab_clicked = pyqtSignal()
    win_key_clicked = pyqtSignal()

    # v2.0.1 — 화면 비율
    ratio_toggle_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(50)
        self.setStyleSheet("background-color: #252526; border-left: 1px solid #3e3e3e;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)

        # ── 화면 제어 ──
        self.btn_fullscreen = SideMenuButton("F", "전체화면 (F11)")
        self.btn_fullscreen.clicked.connect(self.fullscreen_clicked.emit)
        layout.addWidget(self.btn_fullscreen)

        self.btn_ratio = SideMenuButton("R", "화면 비율 (Fit/Stretch)")
        self.btn_ratio.clicked.connect(self.ratio_toggle_clicked.emit)
        layout.addWidget(self.btn_ratio)

        layout.addSpacing(6)

        # ── 화질/FPS 조절 ──
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

        layout.addSpacing(6)

        # ── 클립보드/파일/스크린샷 ──
        self.btn_clipboard = SideMenuButton("CB", "클립보드 동기화")
        self.btn_clipboard.clicked.connect(self.clipboard_sync_clicked.emit)
        layout.addWidget(self.btn_clipboard)

        self.btn_file = SideMenuButton("FT", "파일 전송")
        self.btn_file.clicked.connect(self.file_send_clicked.emit)
        layout.addWidget(self.btn_file)

        self.btn_screenshot = SideMenuButton("SS", "스크린샷 저장")
        self.btn_screenshot.clicked.connect(self.screenshot_clicked.emit)
        layout.addWidget(self.btn_screenshot)

        layout.addSpacing(6)

        # ── 특수키 ──
        self.btn_cad = SideMenuButton("CD", "Ctrl+Alt+Del")
        self.btn_cad.clicked.connect(self.ctrl_alt_del_clicked.emit)
        layout.addWidget(self.btn_cad)

        self.btn_alt_tab = SideMenuButton("AT", "Alt+Tab")
        self.btn_alt_tab.clicked.connect(self.alt_tab_clicked.emit)
        layout.addWidget(self.btn_alt_tab)

        self.btn_win = SideMenuButton("W", "Windows 키")
        self.btn_win.clicked.connect(self.win_key_clicked.emit)
        layout.addWidget(self.btn_win)

        layout.addSpacing(6)

        # ── 명령/스크립트 ──
        self.btn_command = SideMenuButton("C>", "명령 실행")
        self.btn_command.clicked.connect(self.command_clicked.emit)
        layout.addWidget(self.btn_command)

        self.btn_script_run = SideMenuButton("SR", "스크립트 실행 (Ctrl+3)")
        self.btn_script_run.clicked.connect(self.script_run_clicked.emit)
        layout.addWidget(self.btn_script_run)

        self.btn_script_stop = SideMenuButton("ST", "스크립트 중지 (Ctrl+4)")
        self.btn_script_stop.clicked.connect(self.script_stop_clicked.emit)
        layout.addWidget(self.btn_script_stop)

        layout.addStretch()

        # ── 닫기 ──
        self.btn_close = SideMenuButton("X", "닫기")
        self.btn_close.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d;
                color: #e04040;
                border: 1px solid #3e3e3e;
                border-radius: 3px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #e04040;
                color: #fff;
            }
        """)
        self.btn_close.clicked.connect(self.close_clicked.emit)
        layout.addWidget(self.btn_close)
