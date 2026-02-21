"""WellcomSOFT 로그인 다이얼로그"""

import logging
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QMessageBox,
    QFrame,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from api_client import api_client
from config import settings

logger = logging.getLogger(__name__)

# 기본 서버 URL (config.py의 defaults와 동일)
DEFAULT_API_URL = 'http://log.wellcomll.org:4797'


class LoginDialog(QDialog):
    """서버 로그인 다이얼로그

    서버 주소는 기본값 사용 (사용자 입력 불필요).
    사용자 이름 + 비밀번호만 입력.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._logged_in = False
        self._init_ui()
        self._load_saved()

    def _init_ui(self):
        self.setWindowTitle("WellcomSOFT 로그인")
        self.setFixedSize(400, 280)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowCloseButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(30, 20, 30, 20)

        # 타이틀
        title = QLabel("WellcomSOFT")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #2196F3; margin-bottom: 5px;")
        layout.addWidget(title)

        subtitle = QLabel("소프트웨어 기반 다중 PC 원격 관리")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #888; font-size: 11px; margin-bottom: 10px;")
        layout.addWidget(subtitle)

        # 구분선
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #444;")
        layout.addWidget(line)

        # 사용자명
        layout.addWidget(QLabel("사용자 이름"))
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("사용자 이름 입력")
        layout.addWidget(self.username_input)

        # 비밀번호
        layout.addWidget(QLabel("비밀번호"))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("비밀번호 입력")
        self.password_input.returnPressed.connect(self._do_login)
        layout.addWidget(self.password_input)

        # 자동 로그인
        self.auto_login_cb = QCheckBox("자동 로그인")
        layout.addWidget(self.auto_login_cb)

        # 버튼
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.login_btn = QPushButton("로그인")
        self.login_btn.setDefault(True)
        self.login_btn.setMinimumHeight(35)
        self.login_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; "
            "border: none; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background-color: #1976D2; }"
            "QPushButton:pressed { background-color: #0D47A1; }"
        )
        self.login_btn.clicked.connect(self._do_login)
        btn_layout.addWidget(self.login_btn)

        self.cancel_btn = QPushButton("종료")
        self.cancel_btn.setMinimumHeight(35)
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        layout.addLayout(btn_layout)

    def _load_saved(self):
        """저장된 설정 로드"""
        username = settings.get('server.username', '')
        auto_login = settings.get('server.auto_login', False)

        if username:
            self.username_input.setText(username)
        self.auto_login_cb.setChecked(auto_login)

        # api_client의 base_url을 기본값으로 동기화
        api_client._base_url = settings.get('server.api_url', DEFAULT_API_URL)

        # 자동 로그인 시도
        if auto_login and username:
            token = settings.get('server.token', '')
            if token:
                self._try_auto_login()

    def _try_auto_login(self):
        """저장된 토큰으로 자동 로그인 시도"""
        try:
            if api_client.verify_token():
                self._logged_in = True
                logger.info("자동 로그인 성공")
                self.accept()
        except Exception:
            pass

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
