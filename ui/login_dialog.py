"""WellcomSOFT 로그인 다이얼로그"""

import logging
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QMessageBox,
    QFrame, QSpacerItem, QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from api_client import api_client
from config import settings

logger = logging.getLogger(__name__)

# 기본 서버 URL (config.py의 defaults와 동일)
DEFAULT_API_URL = 'http://log.wellcomll.org:4797'

# 다크 모던 스타일시트
_LOGIN_STYLE = """
QDialog {
    background: #1e1e1e;
}
QLabel {
    color: #e0e0e0;
    background: transparent;
}
QLabel#title {
    color: #2196F3;
}
QLabel#subtitle {
    color: #666666;
    font-size: 11px;
}
QLabel#field_label {
    color: #aaaaaa;
    font-size: 11px;
    padding-bottom: 2px;
}
QLabel#version_info {
    color: #555555;
    font-size: 10px;
}
QLineEdit {
    padding: 10px 12px;
    border: 1px solid #3a3a3a;
    border-radius: 6px;
    background: #2a2a2a;
    color: #e0e0e0;
    font-size: 13px;
    selection-background-color: #2196F3;
}
QLineEdit:focus {
    border: 1px solid #2196F3;
    background: #2d2d2d;
}
QLineEdit::placeholder {
    color: #555555;
}
QCheckBox {
    color: #888888;
    font-size: 11px;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 3px;
    border: 1px solid #444444;
    background: #2a2a2a;
}
QCheckBox::indicator:checked {
    background: #2196F3;
    border: 1px solid #2196F3;
}
QPushButton#login_btn {
    padding: 10px;
    background: #2196F3;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    font-size: 13px;
    font-weight: bold;
}
QPushButton#login_btn:hover {
    background: #1976D2;
}
QPushButton#login_btn:pressed {
    background: #0D47A1;
}
QPushButton#login_btn:disabled {
    background: #333333;
    color: #666666;
}
QPushButton#cancel_btn {
    padding: 10px;
    background: transparent;
    color: #888888;
    border: 1px solid #3a3a3a;
    border-radius: 6px;
    font-size: 13px;
}
QPushButton#cancel_btn:hover {
    background: #2a2a2a;
    color: #bbbbbb;
}
QFrame#separator {
    color: #333333;
}
"""


class LoginDialog(QDialog):
    """서버 로그인 다이얼로그

    서버 주소는 기본값 사용 (사용자 입력 불필요).
    사용자 이름 + 비밀번호만 입력.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._logged_in = False
        self._auto_login_pending = False  # exec() 후 자동 로그인 시도
        self._init_ui()
        self._load_saved()

    def _init_ui(self):
        self.setWindowTitle("WellcomSOFT 로그인")
        self.setFixedSize(420, 380)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setStyleSheet(_LOGIN_STYLE)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(36, 28, 36, 20)

        # 타이틀
        title = QLabel("WellcomSOFT")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = QFont()
        title_font.setPointSize(22)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # 서브 타이틀
        subtitle = QLabel("소프트웨어 기반 다중 PC 원격 관리")
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(6)

        # 구분선
        line = QFrame()
        line.setObjectName("separator")
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #333333;")
        layout.addWidget(line)

        layout.addSpacing(8)

        # 사용자명
        lbl_user = QLabel("사용자 이름")
        lbl_user.setObjectName("field_label")
        layout.addWidget(lbl_user)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("사용자 이름 입력")
        layout.addWidget(self.username_input)

        layout.addSpacing(4)

        # 비밀번호
        lbl_pass = QLabel("비밀번호")
        lbl_pass.setObjectName("field_label")
        layout.addWidget(lbl_pass)

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("비밀번호 입력")
        self.password_input.returnPressed.connect(self._do_login)
        layout.addWidget(self.password_input)

        layout.addSpacing(4)

        # 자동 로그인
        self.auto_login_cb = QCheckBox("자동 로그인")
        layout.addWidget(self.auto_login_cb)

        layout.addSpacing(8)

        # 버튼
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.login_btn = QPushButton("로그인")
        self.login_btn.setObjectName("login_btn")
        self.login_btn.setDefault(True)
        self.login_btn.setMinimumHeight(42)
        self.login_btn.clicked.connect(self._do_login)
        btn_layout.addWidget(self.login_btn, stretch=2)

        self.cancel_btn = QPushButton("종료")
        self.cancel_btn.setObjectName("cancel_btn")
        self.cancel_btn.setMinimumHeight(42)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn, stretch=1)

        layout.addLayout(btn_layout)

        layout.addStretch()

        # 하단 구분선
        bottom_line = QFrame()
        bottom_line.setFrameShape(QFrame.Shape.HLine)
        bottom_line.setStyleSheet("color: #2a2a2a;")
        layout.addWidget(bottom_line)

        # 하단 버전 + 서버 정보
        self.version_label = QLabel("")
        self.version_label.setObjectName("version_info")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.version_label)

        # 버전 정보 로드
        self._update_version_label()

    def _update_version_label(self):
        """하단에 버전 + 서버 정보 표시"""
        try:
            from version import __version__
            ver = __version__
        except ImportError:
            ver = "?"

        server_url = settings.get('server.api_url', DEFAULT_API_URL)
        # URL에서 호스트만 추출
        try:
            host = server_url.split('://')[1].split(':')[0] if '://' in server_url else server_url
        except Exception:
            host = server_url

        self.version_label.setText(f"v{ver}  |  {host}")

    def _load_saved(self):
        """저장된 설정 로드"""
        username = settings.get('server.username', '')
        auto_login = settings.get('server.auto_login', False)

        if username:
            self.username_input.setText(username)
        self.auto_login_cb.setChecked(auto_login)

        # api_client의 base_url을 기본값으로 동기화
        api_client._base_url = settings.get('server.api_url', DEFAULT_API_URL)

        # 자동 로그인: exec() 후 시도 (생성자에서 accept() 호출하면 동작 불안정)
        if auto_login and username:
            token = settings.get('server.token', '')
            if token:
                self._auto_login_pending = True

    def showEvent(self, event):
        """다이얼로그 표시 후 자동 로그인 시도

        생성자에서 accept()을 호출하면 exec() 전이라 동작이 불안정하므로,
        showEvent에서 QTimer.singleShot(0)으로 이벤트 루프 시작 후 시도.
        """
        super().showEvent(event)
        if self._auto_login_pending:
            self._auto_login_pending = False
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._try_auto_login)

    def _try_auto_login(self):
        """저장된 토큰으로 자동 로그인 시도"""
        try:
            if api_client.verify_token():
                self._logged_in = True
                logger.info("자동 로그인 성공")
                self.accept()
                return
        except Exception:
            pass
        logger.debug("자동 로그인 실패 — 수동 로그인 대기")

    def _do_login(self):
        """로그인 실행"""
        username = self.username_input.text().strip()
        password = self.password_input.text()

        if not username:
            QMessageBox.warning(self, "입력 오류", "사용자 이름을 입력하세요.")
            self.username_input.setFocus()
            return

        if not password:
            QMessageBox.warning(self, "입력 오류", "비밀번호를 입력하세요.")
            self.password_input.setFocus()
            return

        # 로그인 버튼 비활성화
        self.login_btn.setEnabled(False)
        self.login_btn.setText("로그인 중...")

        try:
            data = api_client.login(username, password)
            self._logged_in = True

            # 자동 로그인 설정 저장
            settings.set('server.auto_login', self.auto_login_cb.isChecked())
            # api_url도 저장 (다음 시작 시 auto_login에서 올바른 URL 사용)
            settings.set('server.api_url', api_client._base_url)

            logger.info(f"로그인 성공: {username}")
            self.accept()

        except Exception as e:
            err_msg = str(e)
            if 'Connection' in err_msg:
                QMessageBox.critical(
                    self, "연결 실패",
                    f"서버에 연결할 수 없습니다.\n\n서버 상태를 확인하세요."
                )
            elif '401' in err_msg or '사용자' in err_msg or '비밀번호' in err_msg:
                QMessageBox.warning(
                    self, "로그인 실패",
                    "사용자 이름 또는 비밀번호가 올바르지 않습니다."
                )
            else:
                QMessageBox.critical(
                    self, "로그인 오류",
                    f"로그인 중 오류가 발생했습니다.\n\n{err_msg}"
                )
        finally:
            self.login_btn.setEnabled(True)
            self.login_btn.setText("로그인")

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in
