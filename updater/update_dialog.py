"""업데이트 다이얼로그 — 심플 버전"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from .github_client import ReleaseInfo

# 공통 스타일
_STYLE = """
QDialog {
    background: #1e1e1e;
}
QLabel {
    color: #e0e0e0;
}
QLabel#title {
    color: #4CAF50;
    font-size: 13px;
    font-weight: bold;
}
QLabel#version {
    font-size: 22px;
    font-weight: bold;
    color: #ffffff;
}
QLabel#arrow {
    font-size: 18px;
    color: #666666;
}
QPushButton#skip {
    padding: 8px 24px;
    background: transparent;
    color: #888888;
    border: 1px solid #444444;
    border-radius: 6px;
    font-size: 12px;
}
QPushButton#skip:hover {
    background: #2a2a2a;
    color: #bbbbbb;
}
QPushButton#update {
    padding: 8px 24px;
    background: #4CAF50;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    font-size: 12px;
    font-weight: bold;
}
QPushButton#update:hover {
    background: #45a049;
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
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, checker, release_info):
        super().__init__()
        self.checker = checker
        self.release_info = release_info

    def run(self):
        success = self.checker.apply_update(
            self.release_info,
            progress_callback=lambda d, t: self.progress.emit(d, t)
        )
        if success:
            self.finished.emit(True, "완료")
        else:
            self.finished.emit(False, "업데이트 실패")


class UpdateNotifyDialog(QDialog):
    """업데이트 알림 — 버전만 표시"""

    def __init__(self, current_version: str, release_info: ReleaseInfo,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("WellcomSOFT")
        self.setFixedSize(320, 160)
        self.setStyleSheet(_STYLE)
        self._init_ui(current_version, release_info)

    def _init_ui(self, current_version, release_info):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)

        # 제목
        title = QLabel("업데이트 가능")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        layout.addSpacing(4)

        # 버전 표시: v1.9.1 → v1.9.2
        ver_layout = QHBoxLayout()
        ver_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        old_ver = QLabel(f"v{current_version}")
        old_ver.setObjectName("version")
        old_ver.setStyleSheet("color: #888888; font-size: 20px;")
        ver_layout.addWidget(old_ver)

        arrow = QLabel("  →  ")
        arrow.setObjectName("arrow")
        ver_layout.addWidget(arrow)

        new_ver = QLabel(f"v{release_info.version}")
        new_ver.setObjectName("version")
        ver_layout.addWidget(new_ver)

        layout.addLayout(ver_layout)

        layout.addStretch()

        # 버튼
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        btn_skip = QPushButton("나중에")
        btn_skip.setObjectName("skip")
        btn_skip.clicked.connect(self.reject)
        btn_layout.addWidget(btn_skip)

        btn_layout.addStretch()

        btn_update = QPushButton("업데이트")
        btn_update.setObjectName("update")
        btn_update.clicked.connect(self.accept)
        btn_layout.addWidget(btn_update)

        layout.addLayout(btn_layout)


class UpdateDialog(QDialog):
    """업데이트 진행 — 프로그레스바 + 퍼센트"""

    update_completed = pyqtSignal(bool)

    def __init__(self, release_info: ReleaseInfo, parent=None):
        super().__init__(parent)
        self.release_info = release_info
        self._success = False
        self.setWindowTitle("WellcomSOFT")
        self.setFixedSize(320, 100)
        self.setStyleSheet(_STYLE)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint
        )
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)

        # 상태 텍스트
        self.status_label = QLabel(f"v{self.release_info.version} 업데이트 중...")
        self.status_label.setObjectName("title")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        # 프로그레스바
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        # 퍼센트
        self.pct_label = QLabel("0%")
        self.pct_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pct_label.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(self.pct_label)

    def start_update(self, checker):
        self.worker = UpdateWorkerThread(checker, self.release_info)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_progress(self, downloaded, total):
        percent = int(downloaded / total * 100) if total else 0
        self.progress_bar.setValue(percent)
        self.pct_label.setText(f"{percent}%")

    def _on_finished(self, success, message):
        self._success = success
        if success:
            self.progress_bar.setValue(100)
            self.pct_label.setText("100%")
            self.status_label.setText("완료 — 재시작합니다")
            self.update_completed.emit(True)
            self.accept()
        else:
            QMessageBox.warning(self, "오류", message)
            self.reject()

    @property
    def is_success(self) -> bool:
        return self._success
