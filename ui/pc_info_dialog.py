"""PC 하드웨어 정보 팝업 다이얼로그"""

import logging
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QFormLayout, QWidget, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

logger = logging.getLogger(__name__)

try:
    from version import __version__ as MANAGER_VERSION
except ImportError:
    MANAGER_VERSION = ''


def _parse_ver(v: str):
    try:
        return tuple(int(x) for x in v.split('.'))
    except Exception:
        return (0,)


CONNECTION_LABELS = {
    'lan':   'LAN (사설망 직접)',
    'wan':   'WAN (공인IP 직접)',
    'relay': '릴레이 (서버 경유)',
    '':      '—',
}


class PCInfoDialog(QDialog):
    """PC 하드웨어 정보 팝업"""

    def __init__(self, pc, agent_server, parent=None):
        super().__init__(parent)
        self._pc = pc
        self._agent_server = agent_server
        self._init_ui()

    def _init_ui(self):
        from core.pc_device import PCStatus
        pc = self._pc
        info = pc.info

        self.setWindowTitle(f"PC 정보 — {pc.name}")
        self.setFixedWidth(440)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowCloseButtonHint
        )

        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # ── 헤더 ──────────────────────────────────
        header = QWidget()
        header.setStyleSheet("background-color: #252526;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 12, 16, 12)

        name_lbl = QLabel(pc.name)
        name_lbl.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        name_lbl.setStyleSheet("color: #ffffff;")
        h_layout.addWidget(name_lbl)

        h_layout.addStretch()

        status_text = "● 온라인" if pc.status == PCStatus.ONLINE else "● 오프라인"
        status_color = "#2ecc71" if pc.status == PCStatus.ONLINE else "#e74c3c"
        status_lbl = QLabel(status_text)
        status_lbl.setStyleSheet(f"color: {status_color}; font-size: 11px;")
        h_layout.addWidget(status_lbl)

        root.addWidget(header)

        # 구분선
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #3e3e3e;")
        root.addWidget(sep)

        # ── 폼 영역 ───────────────────────────────
        body = QWidget()
        body.setStyleSheet("background-color: #1e1e1e;")
        form = QFormLayout(body)
        form.setContentsMargins(20, 14, 20, 14)
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(16)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        label_style = "color: #888888; font-size: 11px;"
        value_style = "color: #d4d4d4; font-size: 11px;"
        na_style    = "color: #555555; font-size: 11px; font-style: italic;"

        def _lbl(text: str, style: str = value_style) -> QLabel:
            w = QLabel(text)
            w.setStyleSheet(style)
            w.setWordWrap(True)
            return w

        def _row(key: str, value: str, placeholder: str = '정보 없음'):
            k_lbl = _lbl(key, label_style)
            v_lbl = _lbl(value if value else placeholder,
                         value_style if value else na_style)
            form.addRow(k_lbl, v_lbl)
            return v_lbl

        # CPU
        cpu_text = info.cpu_model or ''
        if info.cpu_cores:
            cpu_text += f"  ({info.cpu_cores}코어)" if cpu_text else f"{info.cpu_cores}코어"
        _row("CPU", cpu_text)

        # RAM
        ram_text = f"{info.ram_gb:.1f} GB" if info.ram_gb else ''
        _row("RAM", ram_text)

        # 메인보드
        _row("메인보드", info.motherboard)

        # GPU
        _row("GPU", info.gpu_model)

        form.addRow(_lbl('', label_style), _lbl('', label_style))  # 공백 구분

        # IP
        _row("사설 IP", info.ip)
        _row("공인 IP", info.public_ip)

        # OS
        _row("OS", info.os_info)

        # 연결 모드
        mode_str = CONNECTION_LABELS.get(info.connection_mode, info.connection_mode or '—')
        _row("연결", mode_str)

        # 에이전트 버전
        av = info.agent_version
        if av:
            needs_update = False
            ver_text = f"v{av}"
            if MANAGER_VERSION:
                try:
                    needs_update = _parse_ver(av) < _parse_ver(MANAGER_VERSION)
                except Exception:
                    pass
            if needs_update:
                ver_text += f"  → v{MANAGER_VERSION} 업데이트 필요"
                ver_style = "color: #e74c3c; font-size: 11px; font-weight: bold;"
            else:
                ver_text += "  ✓ 최신"
                ver_style = "color: #2ecc71; font-size: 11px;"
            k_lbl = _lbl("에이전트", label_style)
            v_lbl = _lbl(ver_text, ver_style)
            form.addRow(k_lbl, v_lbl)
            self._needs_update = needs_update
        else:
            _row("에이전트", '')
            self._needs_update = False

        # 마지막 접속
        _row("마지막 접속", pc.last_seen_str or '')

        root.addWidget(body)

        # ── 구분선 ────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #3e3e3e;")
        root.addWidget(sep2)

        # ── 버튼 영역 ─────────────────────────────
        btn_area = QWidget()
        btn_area.setStyleSheet("background-color: #252526;")
        btn_layout = QHBoxLayout(btn_area)
        btn_layout.setContentsMargins(16, 10, 16, 10)
        btn_layout.setSpacing(8)

        self._update_btn = QPushButton("원격 업데이트")
        self._update_btn.setEnabled(pc.is_online and self._needs_update)
        self._update_btn.setStyleSheet("""
            QPushButton {
                background-color: #e74c3c; color: white;
                border: none; border-radius: 4px;
                padding: 6px 14px; font-size: 11px;
            }
            QPushButton:hover { background-color: #c0392b; }
            QPushButton:disabled {
                background-color: #3e3e3e; color: #555555;
            }
        """)
        self._update_btn.clicked.connect(self._on_update)
        btn_layout.addWidget(self._update_btn)

        btn_layout.addStretch()

        close_btn = QPushButton("닫기")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #3e3e3e; color: #d4d4d4;
                border: none; border-radius: 4px;
                padding: 6px 14px; font-size: 11px;
            }
            QPushButton:hover { background-color: #555555; }
        """)
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        root.addWidget(btn_area)

    def _on_update(self):
        """원격 업데이트 명령 전송"""
        pc = self._pc
        if not self._agent_server.is_agent_connected(pc.agent_id):
            QMessageBox.warning(
                self, "연결 없음",
                f"{pc.name}이(가) 현재 연결되어 있지 않습니다.\n\n"
                "에이전트가 온라인 상태인지 확인하세요."
            )
            return
        try:
            # AgentServer 공개 메서드 사용 (릴레이/P2P 자동 처리)
            self._agent_server.send_update_request(pc.agent_id)
            self._update_btn.setText("업데이트 시작됨")
            self._update_btn.setEnabled(False)
            logger.info(f"원격 업데이트 명령 전송: {pc.name}")
            QMessageBox.information(
                self, "업데이트 요청 전송",
                f"{pc.name}에 업데이트 명령을 전송했습니다.\n\n"
                "에이전트가 자동으로 최신 버전을 다운로드 후 재시작합니다."
            )
        except Exception as e:
            logger.error(f"원격 업데이트 실패: {e}")
            QMessageBox.critical(
                self, "업데이트 실패",
                f"원격 업데이트 명령 전송에 실패했습니다:\n\n{e}"
            )
