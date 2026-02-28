"""다중 PC 썸네일 그리드 뷰 (CCTV 모드)

LinkIO Desktop 스타일 — 5컬럼 기본, 연결 상태 색상, 메모 표시,
자동 정렬, 5fps 그리드 갱신.
"""

import logging
from typing import Dict

from PyQt6.QtWidgets import (
    QScrollArea, QWidget, QGridLayout, QLabel, QVBoxLayout,
    QFrame, QSizePolicy, QHBoxLayout, QPushButton,
    QGraphicsDropShadowEffect, QLineEdit,
)
from PyQt6.QtCore import Qt, QTimer, QByteArray, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap, QColor, QPalette, QMouseEvent, QFont, QPainter

from config import settings
from core.pc_manager import PCManager
from core.agent_server import AgentServer
from core.pc_device import PCStatus

try:
    from version import __version__ as MANAGER_VERSION
except ImportError:
    MANAGER_VERSION = ''

logger = logging.getLogger(__name__)

# ── 카드 디자인 상수 ──────────────────────────────────────────
_CARD_BG = '#252526'
_CARD_BG_HOVER = '#2d2d30'
_CARD_RADIUS = 8
_CARD_BORDER = 2
_IMG_BG = '#1a1a1c'
_IMG_RADIUS = 4

_COLOR_ONLINE = '#4CAF50'
_COLOR_ERROR = '#f44336'
_COLOR_OFFLINE = '#555'
_COLOR_SELECTED = '#007acc'

_SHADOW_NORMAL = (12, 2, 50)     # blur, offset_y, alpha
_SHADOW_HOVER = (20, 4, 80)      # blur, offset_y, alpha


class PCThumbnailWidget(QFrame):
    """단일 PC 썸네일 위젯"""

    double_clicked = pyqtSignal(str)   # pc_name
    right_clicked = pyqtSignal(str, object)   # pc_name, QPoint (global pos)
    selected = pyqtSignal(str, bool)   # pc_name, is_selected
    update_requested = pyqtSignal(str)  # pc_name

    def __init__(self, pc_name: str, memo: str = '', parent=None):
        super().__init__(parent)
        self.pc_name = pc_name
        self._is_online = False
        self._is_selected = False
        self._status = PCStatus.OFFLINE
        self._hover = False

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(200, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # 드롭 섀도우
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(_SHADOW_NORMAL[0])
        self._shadow.setOffset(0, _SHADOW_NORMAL[1])
        self._shadow.setColor(QColor(0, 0, 0, _SHADOW_NORMAL[2]))
        self.setGraphicsEffect(self._shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(6)

        # ── 썸네일 이미지 ──
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(120)
        self.image_label.setStyleSheet(
            f"background-color: {_IMG_BG}; border-radius: {_IMG_RADIUS}px; color: #555;"
        )
        self.image_label.setText("연결 대기...")
        self.image_label.setFont(QFont("", 9))
        layout.addWidget(self.image_label, 1)

        # ── 하단 Row 1: 상태 점 + PC이름 + 모드 배지 ──
        row1 = QWidget()
        row1.setStyleSheet("background: transparent;")
        row1_layout = QHBoxLayout(row1)
        row1_layout.setContentsMargins(2, 0, 2, 0)
        row1_layout.setSpacing(6)

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(10, 10)
        self.status_dot.setStyleSheet(
            f"background-color: {_COLOR_OFFLINE}; border-radius: 5px;"
        )
        row1_layout.addWidget(self.status_dot)

        self.name_label = QLabel(pc_name)
        self.name_label.setFont(QFont("", 11, QFont.Weight.Bold))
        self.name_label.setStyleSheet("color: #e0e0e0; background: transparent;")
        row1_layout.addWidget(self.name_label)

        row1_layout.addStretch()

        # 연결 모드 배지 (LAN / WAN / UDP / 릴레이)
        self.mode_label = QLabel()
        self.mode_label.setFont(QFont("", 8, QFont.Weight.Bold))
        self.mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mode_label.setFixedHeight(20)
        self.mode_label.setVisible(False)
        row1_layout.addWidget(self.mode_label)

        # 핑 레이턴시 배지
        self.latency_label = QLabel()
        self.latency_label.setFont(QFont("", 8, QFont.Weight.Bold))
        self.latency_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.latency_label.setFixedHeight(20)
        self.latency_label.setVisible(False)
        row1_layout.addWidget(self.latency_label)

        layout.addWidget(row1)

        # ── 하단 Row 2: 버전 + 업데이트 버튼 + 메모 ──
        row2 = QWidget()
        row2.setStyleSheet("background: transparent;")
        row2_layout = QHBoxLayout(row2)
        row2_layout.setContentsMargins(18, 0, 2, 0)   # 18 = dot+spacing 인덴트
        row2_layout.setSpacing(6)

        self.version_label = QLabel()
        self.version_label.setFont(QFont("", 8))
        self.version_label.setStyleSheet("color: #888; background: transparent;")
        row2_layout.addWidget(self.version_label)

        self.update_btn = QPushButton("업데이트")
        self.update_btn.setFixedHeight(20)
        self.update_btn.setFont(QFont("", 8))
        self.update_btn.setStyleSheet(
            "QPushButton { background-color: #e67e22; color: white; border: none;"
            " border-radius: 4px; padding: 2px 8px; }"
            "QPushButton:hover { background-color: #d35400; }"
        )
        self.update_btn.setVisible(False)
        self.update_btn.clicked.connect(lambda: self.update_requested.emit(self.pc_name))
        row2_layout.addWidget(self.update_btn)

        row2_layout.addStretch()

        self.memo_label = QLabel(memo)
        self.memo_label.setFont(QFont("", 8))
        self.memo_label.setStyleSheet("color: #666; background: transparent;")
        self.memo_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        row2_layout.addWidget(self.memo_label)

        layout.addWidget(row2)

        # ── 하단 Row 3: CPU/RAM 미니 바 ──
        row3 = QWidget()
        row3.setStyleSheet("background: transparent;")
        row3.setFixedHeight(16)
        row3_layout = QHBoxLayout(row3)
        row3_layout.setContentsMargins(2, 0, 2, 0)
        row3_layout.setSpacing(4)

        self._cpu_bar = QLabel()
        self._cpu_bar.setFixedHeight(4)
        self._cpu_bar.setStyleSheet("background-color: #333; border-radius: 2px;")
        row3_layout.addWidget(self._cpu_bar)

        self._ram_bar = QLabel()
        self._ram_bar.setFixedHeight(4)
        self._ram_bar.setStyleSheet("background-color: #333; border-radius: 2px;")
        row3_layout.addWidget(self._ram_bar)

        self._perf_label = QLabel()
        self._perf_label.setFont(QFont("", 7))
        self._perf_label.setStyleSheet("color: #666; background: transparent;")
        self._perf_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row3_layout.addWidget(self._perf_label)

        layout.addWidget(row3)

        self._update_style()

    # ── 썸네일 갱신 ──────────────────────────────────────────

    def update_thumbnail(self, jpeg_data: bytes):
        pixmap = QPixmap()
        pixmap.loadFromData(QByteArray(jpeg_data))
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.image_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.image_label.setPixmap(scaled)

    # ── 상태 ─────────────────────────────────────────────────

    def set_status(self, status: PCStatus):
        self._status = status
        self._is_online = (status == PCStatus.ONLINE)
        self._update_style()
        if not self._is_online:
            self.image_label.clear()
            if status == PCStatus.ERROR:
                self.image_label.setText("오류")
            else:
                self.image_label.setText("오프라인")

    def set_selected(self, selected: bool):
        self._is_selected = selected
        self._update_style()

    def set_memo(self, memo: str):
        self.memo_label.setText(memo)

    # ── 버전 ─────────────────────────────────────────────────

    def update_version(self, agent_version: str, manager_version: str = ''):
        if not agent_version:
            self.version_label.setText('')
            self.version_label.setStyleSheet("color: #555; background: transparent;")
            self.update_btn.setVisible(False)
            return

        display = 'v?' if agent_version == '0.0.0' else f'v{agent_version}'
        needs_update = False
        is_latest = False
        if manager_version and agent_version != '0.0.0':
            try:
                av = tuple(int(x) for x in agent_version.split('.'))
                mv = tuple(int(x) for x in manager_version.split('.'))
                needs_update = av < mv
                is_latest = av >= mv
            except Exception:
                pass

        if is_latest:
            display += ' ✓'
        self.version_label.setText(display)

        if needs_update:
            self.version_label.setStyleSheet(
                "color: #e74c3c; font-weight: bold; background: transparent;"
            )
        elif is_latest:
            self.version_label.setStyleSheet("color: #2ecc71; background: transparent;")
        else:
            self.version_label.setStyleSheet("color: #888; background: transparent;")
        self.update_btn.setVisible(needs_update)

    def set_update_status(self, status: str, **kwargs):
        if status == 'checking':
            self.update_btn.setText("확인 중...")
            self.update_btn.setEnabled(False)
        elif status == 'downloading':
            pct = kwargs.get('progress', 0)
            self.update_btn.setText(f"다운로드 {pct}%")
            self.update_btn.setEnabled(False)
        elif status == 'restarting':
            self.update_btn.setText("재시작...")
            self.update_btn.setEnabled(False)
        elif status == 'up_to_date':
            self.update_btn.setText("최신")
            self.update_btn.setEnabled(False)
            self.update_btn.setStyleSheet(
                "QPushButton { background-color: #27ae60; color: white; border: none;"
                " border-radius: 4px; padding: 2px 8px; }"
            )
        elif status == 'failed':
            self.update_btn.setText("실패")
            self.update_btn.setEnabled(True)
            self.update_btn.setStyleSheet(
                "QPushButton { background-color: #e74c3c; color: white; border: none;"
                " border-radius: 4px; padding: 2px 8px; }"
                "QPushButton:hover { background-color: #c0392b; }"
            )
        else:
            self.update_btn.setText("업데이트")
            self.update_btn.setEnabled(True)
            self.update_btn.setStyleSheet(
                "QPushButton { background-color: #e67e22; color: white; border: none;"
                " border-radius: 4px; padding: 2px 8px; }"
                "QPushButton:hover { background-color: #d35400; }"
            )

    # ── 연결 모드 배지 ───────────────────────────────────────

    def update_mode(self, mode: str):
        MODE_STYLES = {
            'lan':     ('LAN',    '#27ae60', '#fff'),
            'udp_p2p': ('UDP',    '#8e44ad', '#fff'),
            'wan':     ('WAN',    '#2980b9', '#fff'),
            'relay':   ('릴레이', '#d35400', '#fff'),
        }
        if mode in MODE_STYLES:
            text, bg, fg = MODE_STYLES[mode]
            self.mode_label.setText(text)
            self.mode_label.setStyleSheet(
                f"QLabel {{ background-color: {bg}; color: {fg};"
                f" border-radius: 4px; padding: 2px 8px; }}"
            )
            self.mode_label.setVisible(True)
        else:
            self.mode_label.setVisible(False)

    # ── 성능 모니터 ────────────────────────────────────────

    def update_performance(self, cpu: float, ram: float):
        """CPU/RAM 사용률 업데이트 (미니 바 + 텍스트)"""
        def _bar_color(val):
            if val <= 60:
                return '#27ae60'
            elif val <= 85:
                return '#f39c12'
            return '#e74c3c'

        self._cpu_bar.setStyleSheet(
            f"background-color: {_bar_color(cpu)}; border-radius: 2px;"
            f" max-width: {max(2, int(cpu))}px;"
        )
        self._ram_bar.setStyleSheet(
            f"background-color: {_bar_color(ram)}; border-radius: 2px;"
            f" max-width: {max(2, int(ram))}px;"
        )
        self._perf_label.setText(f"C:{cpu:.0f}% R:{ram:.0f}%")
        self._perf_label.setStyleSheet("color: #888; background: transparent;")

    # ── 핑 레이턴시 ────────────────────────────────────────

    def update_latency(self, ms: int):
        """핑 레이턴시 업데이트"""
        if ms <= 50:
            bg = '#27ae60'  # 녹색
        elif ms <= 150:
            bg = '#f39c12'  # 노란색
        else:
            bg = '#e74c3c'  # 빨간색
        self.latency_label.setText(f"{ms}ms")
        self.latency_label.setStyleSheet(
            f"QLabel {{ background-color: {bg}; color: #fff;"
            f" border-radius: 4px; padding: 2px 6px; }}"
        )
        self.latency_label.setVisible(True)

    # ── 스타일 갱신 ──────────────────────────────────────────

    def _update_style(self):
        """연결 상태 + 선택 + 호버에 따라 카드 스타일 변경"""
        # 상태 점 색상
        if self._status == PCStatus.ONLINE:
            dot_color = _COLOR_ONLINE
        elif self._status == PCStatus.ERROR:
            dot_color = _COLOR_ERROR
        else:
            dot_color = _COLOR_OFFLINE
        self.status_dot.setStyleSheet(
            f"background-color: {dot_color}; border-radius: 5px;"
        )

        # 카드 테두리 (일관된 2px — 레이아웃 시프트 없음)
        if self._is_selected:
            border_color = _COLOR_SELECTED
        elif self._status == PCStatus.ONLINE:
            border_color = _COLOR_ONLINE
        elif self._status == PCStatus.ERROR:
            border_color = _COLOR_ERROR
        else:
            border_color = '#3e3e3e'

        bg = _CARD_BG_HOVER if self._hover else _CARD_BG

        self.setStyleSheet(f"""
            PCThumbnailWidget {{
                background-color: {bg};
                border: {_CARD_BORDER}px solid {border_color};
                border-radius: {_CARD_RADIUS}px;
            }}
        """)

        # 섀도우 강도
        if self._hover:
            blur, offset, alpha = _SHADOW_HOVER
        else:
            blur, offset, alpha = _SHADOW_NORMAL
        self._shadow.setBlurRadius(blur)
        self._shadow.setOffset(0, offset)
        self._shadow.setColor(QColor(0, 0, 0, alpha))

    # ── 마우스 이벤트 ────────────────────────────────────────

    def enterEvent(self, event):
        self._hover = True
        self._update_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._update_style()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self.double_clicked.emit(self.pc_name)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._is_selected = not self._is_selected
                self._update_style()
                self.selected.emit(self.pc_name, self._is_selected)
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit(self.pc_name, event.globalPosition().toPoint())
            event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        pm = self.image_label.pixmap()
        if pm and not pm.isNull():
            self.image_label.setPixmap(pm.scaled(
                self.image_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))


class PlaceholderSlotWidget(QFrame):
    """빈 슬롯 위젯 — 에이전트가 없는 그리드 칸"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(200, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet(f"""
            PlaceholderSlotWidget {{
                background-color: #1e1e1e;
                border: 2px dashed #383838;
                border-radius: {_CARD_RADIUS}px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel("+")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #444; font-size: 28px; background: transparent;")
        layout.addWidget(lbl)


class GridView(QWidget):
    """다중 PC 썸네일 그리드 (LinkIO 스타일)"""

    open_viewer = pyqtSignal(str)               # pc_name
    context_menu_requested = pyqtSignal(str, object)  # pc_name, QPoint
    selection_changed = pyqtSignal(list)         # 선택된 pc_name 목록

    def __init__(self, pc_manager: PCManager, agent_server: AgentServer):
        super().__init__()
        self.pc_manager = pc_manager
        self.agent_server = agent_server
        self._thumbnails: Dict[str, PCThumbnailWidget] = {}
        self._placeholders: list = []
        self._selected_pcs: set = set()
        self._push_agents: set = set()
        self._filter_text: str = ''

        # 메인 레이아웃 (검색 + 그리드)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── 검색 바 ──
        search_bar = QWidget()
        search_bar.setStyleSheet("background-color: #1a1a1a;")
        search_layout = QHBoxLayout(search_bar)
        search_layout.setContentsMargins(12, 8, 12, 4)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("PC 검색 (이름, 메모)...")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d30; color: #e0e0e0;
                border: 1px solid #3e3e3e; border-radius: 6px;
                padding: 6px 12px; font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #007acc;
            }
        """)
        self._search_input.textChanged.connect(self._on_filter_changed)
        search_layout.addWidget(self._search_input)

        main_layout.addWidget(search_bar)

        # ── 스크롤 영역 ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { background-color: #1a1a1a; border: none; }")

        self._container = QWidget()
        self._container.setStyleSheet("background-color: #1a1a1a;")
        self._grid = QGridLayout(self._container)
        self._grid.setSpacing(12)
        self._grid.setContentsMargins(12, 12, 12, 12)
        self._scroll.setWidget(self._container)

        main_layout.addWidget(self._scroll)

        # 시그널 연결
        pc_manager.signals.devices_reloaded.connect(self.rebuild_grid)
        pc_manager.signals.device_added.connect(lambda _: self.rebuild_grid())
        pc_manager.signals.device_removed.connect(lambda _: self.rebuild_grid())
        pc_manager.signals.device_status_changed.connect(self._on_status_changed)
        agent_server.thumbnail_received.connect(self._on_thumbnail_received)
        agent_server.agent_connected.connect(self._on_agent_connected)
        agent_server.agent_disconnected.connect(self._on_agent_disconnected)
        agent_server.connection_mode_changed.connect(self._on_connection_mode_changed)
        agent_server.update_status_received.connect(self._on_update_status)
        agent_server.latency_measured.connect(self._on_latency_measured)
        agent_server.performance_received.connect(self._on_performance_received)

        # 썸네일 갱신 타이머
        frame_speed = settings.get('grid_view.frame_speed', 5)
        interval = max(200, 1000 // max(1, frame_speed))
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._request_all_thumbnails)
        self._refresh_timer.start(interval)

        # 핑 타이머 (10초마다)
        self._ping_timer = QTimer(self)
        self._ping_timer.timeout.connect(self._ping_all)
        self._ping_timer.start(10000)

        # 성능 모니터링 타이머 (30초마다)
        self._perf_timer = QTimer(self)
        self._perf_timer.timeout.connect(self._request_all_performance)
        self._perf_timer.start(30000)

    def _on_filter_changed(self, text: str):
        """검색어 변경 시 그리드 필터링"""
        self._filter_text = text.strip().lower()
        self.rebuild_grid()

    def rebuild_grid(self):
        """그리드 재구성"""
        for widget in self._thumbnails.values():
            self._grid.removeWidget(widget)
            widget.deleteLater()
        self._thumbnails.clear()

        for ph in self._placeholders:
            self._grid.removeWidget(ph)
            ph.deleteLater()
        self._placeholders.clear()

        all_pcs = self.pc_manager.get_all_pcs()

        # 검색 필터 적용
        if self._filter_text:
            pcs = [
                pc for pc in all_pcs
                if self._filter_text in pc.name.lower()
                or self._filter_text in getattr(pc.info, 'memo', '').lower()
            ]
        else:
            pcs = all_pcs
        columns = self._calculate_columns()
        count = len(pcs)

        for i, pc in enumerate(pcs):
            row = i // columns
            col = i % columns

            memo = getattr(pc.info, 'memo', '')
            thumb = PCThumbnailWidget(pc.name, memo)
            thumb.set_status(pc.status)
            thumb.double_clicked.connect(self.open_viewer.emit)
            thumb.right_clicked.connect(self.context_menu_requested.emit)
            thumb.selected.connect(self._on_pc_selected)
            thumb.update_requested.connect(self._on_update_requested)

            agent_version = getattr(pc.info, 'agent_version', '')
            if not agent_version and pc.is_online:
                agent_version = '0.0.0'
            thumb.update_version(agent_version, MANAGER_VERSION)

            conn_mode = getattr(pc.info, 'connection_mode', '')
            thumb.update_mode(conn_mode)

            if pc.name in self._selected_pcs:
                thumb.set_selected(True)

            if pc.last_thumbnail:
                thumb.update_thumbnail(pc.last_thumbnail)

            self._grid.addWidget(thumb, row, col)
            self._thumbnails[pc.name] = thumb

        # 마지막 행 빈 슬롯을 플레이스홀더로 채우기
        remainder = count % columns
        ph_count = (columns - remainder) % columns if count > 0 else columns
        for j in range(ph_count):
            i = count + j
            row = i // columns
            col = i % columns
            ph = PlaceholderSlotWidget()
            self._grid.addWidget(ph, row, col)
            self._placeholders.append(ph)

    def _calculate_columns(self) -> int:
        user_cols = settings.get('grid_view.columns', 5)
        if user_cols > 0:
            return user_cols

        width = self._scroll.viewport().width()
        if width <= 0:
            return 5
        col_width = 280
        return max(1, width // col_width)

    def _on_status_changed(self, pc_name: str):
        thumb = self._thumbnails.get(pc_name)
        pc = self.pc_manager.get_pc(pc_name)
        if thumb and pc:
            thumb.set_status(pc.status)
            # 상태 변경 시 버전 라벨도 갱신 (업데이트 후 재접속 반영)
            agent_version = getattr(pc.info, 'agent_version', '')
            if not agent_version and pc.is_online:
                agent_version = '0.0.0'
            thumb.update_version(agent_version, MANAGER_VERSION)

    def _on_thumbnail_received(self, agent_id: str, jpeg_data: bytes):
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if pc:
            thumb = self._thumbnails.get(pc.name)
            if thumb:
                thumb.update_thumbnail(jpeg_data)

    def _on_agent_connected(self, agent_id: str, agent_ip: str):
        """에이전트 연결 시 push 모드 시작 + 버전 갱신"""
        push_interval = settings.get('screen.thumbnail_interval', 1000) / 1000.0
        push_interval = max(0.2, min(push_interval, 5.0))
        self.agent_server.start_thumbnail_push(agent_id, push_interval)
        self._push_agents.add(agent_id)

        # 연결 시 버전 라벨 즉시 갱신 (업데이트 후 재접속 반영)
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if pc:
            thumb = self._thumbnails.get(pc.name)
            if thumb:
                agent_version = getattr(pc.info, 'agent_version', '') or '0.0.0'
                thumb.update_version(agent_version, MANAGER_VERSION)

    def _on_agent_disconnected(self, agent_id: str):
        self._push_agents.discard(agent_id)
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if pc:
            thumb = self._thumbnails.get(pc.name)
            if thumb:
                thumb.update_mode('')

    def _on_connection_mode_changed(self, agent_id: str, mode: str):
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if pc:
            thumb = self._thumbnails.get(pc.name)
            if thumb:
                thumb.update_mode(mode)

    def _request_all_thumbnails(self):
        """push 모드가 아닌 PC들에 대해 폴링 요청"""
        for pc in self.pc_manager.get_online_pcs():
            if not pc.is_streaming and pc.agent_id not in self._push_agents:
                self.agent_server.request_thumbnail(pc.agent_id)

    def _on_pc_selected(self, pc_name: str, is_selected: bool):
        if is_selected:
            self._selected_pcs.add(pc_name)
        else:
            self._selected_pcs.discard(pc_name)
        self.selection_changed.emit(list(self._selected_pcs))

    def _on_update_requested(self, pc_name: str):
        pc = self.pc_manager.get_pc(pc_name)
        if pc:
            logger.info(f"[업데이트] {pc_name} ({pc.agent_id}) 원격 업데이트 요청")
            thumb = self._thumbnails.get(pc_name)
            if thumb:
                thumb.set_update_status('checking')
            self.agent_server.send_update_request(pc.agent_id)

    def _ping_all(self):
        """모든 연결된 에이전트에 핑 전송"""
        self.agent_server.ping_all_agents()

    def _request_all_performance(self):
        """모든 연결된 에이전트에 성능 정보 요청"""
        self.agent_server.request_all_performance()

    def _on_performance_received(self, agent_id: str, data: dict):
        """성능 데이터 수신 → 카드에 CPU/RAM 바 표시"""
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if pc:
            thumb = self._thumbnails.get(pc.name)
            if thumb:
                cpu = data.get('cpu', 0)
                ram = data.get('ram', 0)
                thumb.update_performance(cpu, ram)

    def _on_latency_measured(self, agent_id: str, ms: int):
        """핑 응답 수신 → 카드에 레이턴시 표시"""
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if pc:
            thumb = self._thumbnails.get(pc.name)
            if thumb:
                thumb.update_latency(ms)

    _last_update_log: dict = {}  # 클래스 변수: 에이전트별 마지막 로그 상태

    def _on_update_status(self, agent_id: str, status_dict: dict):
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if not pc:
            return
        thumb = self._thumbnails.get(pc.name)
        if not thumb:
            return
        status = status_dict.get('status', '')
        # downloading 상태는 진행률 변경 시에만 로그
        prev = self._last_update_log.get(agent_id)
        pct = status_dict.get('progress', 0)
        if status != 'downloading' or prev != pct:
            logger.info(f"[업데이트] {pc.name} 상태: {status}"
                        + (f" ({pct}%)" if status == 'downloading' else ""))
            self._last_update_log[agent_id] = pct
        thumb.set_update_status(status, **{
            k: v for k, v in status_dict.items() if k != 'type' and k != 'status'
        })

    def get_selected_agent_ids(self) -> list:
        result = []
        for pc_name in self._selected_pcs:
            pc = self.pc_manager.get_pc(pc_name)
            if pc:
                result.append(pc.agent_id)
        return result

    def select_all(self):
        for name, thumb in self._thumbnails.items():
            self._selected_pcs.add(name)
            thumb.set_selected(True)
        self.selection_changed.emit(list(self._selected_pcs))

    def deselect_all(self):
        for thumb in self._thumbnails.values():
            thumb.set_selected(False)
        self._selected_pcs.clear()
        self.selection_changed.emit([])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._thumbnails:
            self.rebuild_grid()
