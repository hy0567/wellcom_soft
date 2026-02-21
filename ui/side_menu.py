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

    # 시그널
    clipboard_sync_clicked = pyqtSignal()
    file_send_clicked = pyqtSignal()
    screenshot_clicked = pyqtSignal()
    fullscreen_clicked = pyqtSignal()
    close_clicked = pyqtSignal()
    script_run_clicked = pyqtSignal()
    script_stop_clicked = pyqtSignal()
    command_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(50)
        self.setStyleSheet("background-color: #252526; border-left: 1px solid #3e3e3e;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)

        # 전체화면
        self.btn_fullscreen = SideMenuButton("F", "전체화면 (F11)")
        self.btn_fullscreen.clicked.connect(self.fullscreen_clicked.emit)
        layout.addWidget(self.btn_fullscreen)

        # 구분선
        layout.addSpacing(8)

        # 클립보드
        self.btn_clipboard = SideMenuButton("CB", "클립보드 동기화")
        self.btn_clipboard.clicked.connect(self.clipboard_sync_clicked.emit)
        layout.addWidget(self.btn_clipboard)

        # 파일 전송
        self.btn_file = SideMenuButton("FT", "파일 전송")
        self.btn_file.clicked.connect(self.file_send_clicked.emit)
        layout.addWidget(self.btn_file)

        # 스크린샷
        self.btn_screenshot = SideMenuButton("SS", "스크린샷 저장")
        self.btn_screenshot.clicked.connect(self.screenshot_clicked.emit)
        layout.addWidget(self.btn_screenshot)

        # 구분선
        layout.addSpacing(8)

        # 명령 실행
        self.btn_command = SideMenuButton("C>", "명령 실행")
        self.btn_command.clicked.connect(self.command_clicked.emit)
        layout.addWidget(self.btn_command)

        # 구분선
        layout.addSpacing(8)

        # 스크립트 실행 (Ctrl+3)
        self.btn_script_run = SideMenuButton("SR", "스크립트 실행 (Ctrl+3)")
        self.btn_script_run.clicked.connect(self.script_run_clicked.emit)
        layout.addWidget(self.btn_script_run)

        # 스크립트 중지 (Ctrl+4)
        self.btn_script_stop = SideMenuButton("ST", "스크립트 중지 (Ctrl+4)")
        self.btn_script_stop.clicked.connect(self.script_stop_clicked.emit)
        layout.addWidget(self.btn_script_stop)

        layout.addStretch()

        # 닫기
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
