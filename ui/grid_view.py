"""다중 PC 썸네일 그리드 뷰 (CCTV 모드)

LinkIO Desktop 스타일 — 5컬럼 기본, 연결 상태 색상, 메모 표시,
자동 정렬, 5fps 그리드 갱신.
"""

import logging
from typing import Dict

from PyQt6.QtWidgets import (
    QScrollArea, QWidget, QGridLayout, QLabel, QVBoxLayout,
    QFrame, QSizePolicy, QHBoxLayout, QPushButton,
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


class PCThumbnailWidget(QFrame):
    """단일 PC 썸네일 위젯 (LinkIO 스타일)"""

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

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(200, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)

        # 썸네일 이미지
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(100)
        self.image_label.setStyleSheet("background-color: #111; border-radius: 2px;")
        self.image_label.setText("연결 대기...")
        self.image_label.setFont(QFont("", 9))
        layout.addWidget(self.image_label, 1)

        # 하단: PC 이름 + 메모
        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(4, 2, 4, 2)
        bottom_layout.setSpacing(4)

        self.name_label = QLabel(pc_name)
        self.name_label.setFont(QFont("", 10, QFont.Weight.Bold))
        bottom_layout.addWidget(self.name_label)

        self.version_label = QLabel()
        self.version_label.setFont(QFont("", 8))
        self.version_label.setStyleSheet("color: #888;")
        bottom_layout.addWidget(self.version_label)

        # 연결 모드 배지 (LAN / WAN / 릴레이)
        self.mode_label = QLabel()
        self.mode_label.setFont(QFont("", 8, QFont.Weight.Bold))
        self.mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mode_label.setFixedHeight(16)
        self.mode_label.setContentsMargins(4, 0, 4, 0)
        self.mode_label.setVisible(False)
        bottom_layout.addWidget(self.mode_label)

        self.update_btn = QPushButton("업데이트")
        self.update_btn.setFixedHeight(18)
        self.update_btn.setFont(QFont("", 8))
        self.update_btn.setStyleSheet(
            "QPushButton { background-color: #e67e22; color: white; border: none;"
            " border-radius: 3px; padding: 0 6px; }"
            "QPushButton:hover { background-color: #d35400; }"
        )
        self.update_btn.setVisible(False)
        self.update_btn.clicked.connect(lambda: self.update_requested.emit(self.pc_name))
        bottom_layout.addWidget(self.update_btn)

        self.memo_label = QLabel(memo)
        self.memo_label.setFont(QFont("", 8))
        self.memo_label.setStyleSheet("color: #888;")
        self.memo_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bottom_layout.addWidget(self.memo_label)

        layout.addWidget(bottom)

        self._update_style()

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

    def update_version(self, agent_version: str, manager_version: str = ''):
        """버전 배지 갱신 — 구버전이면 빨간 배지 + 업데이트 버튼, 최신이면 초록 배지"""
        if not agent_version:
            self.version_label.setText('')
            self.version_label.setStyleSheet("color: #555;")
            self.update_btn.setVisible(False)
            return

        # '0.0.0'은 버전 미보고 에이전트 플레이스홀더
        display = 'v?' if agent_version == '0.0.0' else f'v{agent_version}'
        self.version_label.setText(display)
        needs_update = False
        if manager_version:
            try:
                av = tuple(int(x) for x in agent_version.split('.'))
                mv = tuple(int(x) for x in manager_version.split('.'))
                needs_update = av < mv
            except Exception:
                pass

        if needs_update:
            self.version_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
        else:
            self.version_label.setStyleSheet("color: #2ecc71;")
        self.update_btn.setVisible(needs_update)

    def set_update_status(self, status: str, **kwargs):
        """업데이트 진행 상태 표시"""
        if status == 'checking':
            self.update_btn.setText("확인 중...")
            self.update_btn.setEnabled(False)
        elif status == 'downloading':
            pct = kwargs.get('progress', 0)
            self.update_btn.setText(f"다운로드 {pct}%")
            self.update_btn.setEnabled(False)
        elif status == 'restarting':
            ver = kwargs.get('version', '')
            self.update_btn.setText(f"재시작...")
            self.update_btn.setEnabled(False)
        elif status == 'up_to_date':
            self.update_btn.setText("최신")
            self.update_btn.setEnabled(False)
            self.update_btn.setStyleSheet(
                "QPushButton { background-color: #27ae60; color: white; border: none;"
                " border-radius: 3px; padding: 0 6px; }"
            )
        elif status == 'failed':
            self.update_btn.setText("실패")
            self.update_btn.setEnabled(True)
            self.update_btn.setStyleSheet(
                "QPushButton { background-color: #e74c3c; color: white; border: none;"
                " border-radius: 3px; padding: 0 6px; }"
                "QPushButton:hover { background-color: #c0392b; }"
            )
        else:
            # 원래 상태로 복원
            self.update_btn.setText("업데이트")
            self.update_btn.setEnabled(True)
            self.update_btn.setStyleSheet(
                "QPushButton { background-color: #e67e22; color: white; border: none;"
                " border-radius: 3px; padding: 0 6px; }"
                "QPushButton:hover { background-color: #d35400; }"
            )

    def update_mode(self, mode: str):
        """연결 모드 배지 갱신 (lan / wan / relay / '')"""
        MODE_STYLES = {
            'lan':   ('LAN',  '#27ae60', '#fff'),   # 초록
            'wan':   ('WAN',  '#2980b9', '#fff'),   # 파랑
            'relay': ('릴레이', '#d35400', '#fff'),  # 주황
        }
        if mode in MODE_STYLES:
            text, bg, fg = MODE_STYLES[mode]
            self.mode_label.setText(text)
            self.mode_label.setStyleSheet(
                f"QLabel {{ background-color: {bg}; color: {fg};"
                f" border-radius: 3px; padding: 0 4px; }}"
            )
            self.mode_label.setVisible(True)
        else:
            self.mode_label.setVisible(False)

    def _update_style(self):
        """연결 상태 + 선택 상태에 따라 테두리 색상 변경"""
        if self._is_selected:
            border_color = '#007acc'
            border_width = 3
        elif self._status == PCStatus.ONLINE:
            border_color = '#4CAF50'
            border_width = 2
        elif self._status == PCStatus.ERROR:
            border_color = '#f44336'
            border_width = 2
        else:
            border_color = '#3e3e3e'
            border_width = 1

        self.setStyleSheet(f"""
            PCThumbnailWidget {{
                background-color: #1e1e1e;
                border: {border_width}px solid {border_color};
                border-radius: 4px;
            }}
        """)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self.double_clicked.emit(self.pc_name)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            # Ctrl+클릭 = 선택 토글
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
        # 리사이즈 시 썸네일 다시 스케일링 (마지막 pixmap이 있으면)
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
        self.setMinimumSize(200, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("""
            PlaceholderSlotWidget {
                background-color: #1e1e1e;
                border: 2px dashed #2a2a2a;
                border-radius: 6px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel("＋")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #2a2a2a; font-size: 32px; background: transparent;")
        layout.addWidget(lbl)


class GridView(QScrollArea):
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

        # 스크롤 영역 설정
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background-color: #1a1a1a;")

        self._container = QWidget()
        self._container.setStyleSheet("background-color: #1a1a1a;")
        self._grid = QGridLayout(self._container)
        self._grid.setSpacing(6)
        self._grid.setContentsMargins(6, 6, 6, 6)
        self.setWidget(self._container)

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

        # 썸네일 갱신 타이머
        frame_speed = settings.get('grid_view.frame_speed', 5)
        interval = max(200, 1000 // max(1, frame_speed))
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._request_all_thumbnails)
        self._refresh_timer.start(interval)

    def rebuild_grid(self):
        """그리드 재구성"""
        for widget in self._thumbnails.values():
            self._grid.removeWidget(widget)
            widget.deleteLater()
        self._thumbnails.clear()

        # 플레이스홀더 정리
        for ph in self._placeholders:
            self._grid.removeWidget(ph)
            ph.deleteLater()
        self._placeholders.clear()

        pcs = self.pc_manager.get_all_pcs()
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
            # 온라인이지만 버전 미보고 에이전트 → 구버전으로 간주
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
        # 에이전트가 0개이면 columns 개, 아니면 마지막 행의 나머지 칸 수만큼
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

        width = self.viewport().width()
        if width <= 0:
            return 5
        col_width = 280
        return max(1, width // col_width)

    def _on_status_changed(self, pc_name: str):
        thumb = self._thumbnails.get(pc_name)
        pc = self.pc_manager.get_pc(pc_name)
        if thumb and pc:
            thumb.set_status(pc.status)

    def _on_thumbnail_received(self, agent_id: str, jpeg_data: bytes):
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if pc:
            thumb = self._thumbnails.get(pc.name)
            if thumb:
                thumb.update_thumbnail(jpeg_data)

    def _on_agent_connected(self, agent_id: str, agent_ip: str):
        """에이전트 연결 시 push 모드 시작"""
        push_interval = settings.get('screen.thumbnail_interval', 1000) / 1000.0
        push_interval = max(0.2, min(push_interval, 5.0))
        self.agent_server.start_thumbnail_push(agent_id, push_interval)
        self._push_agents.add(agent_id)

    def _on_agent_disconnected(self, agent_id: str):
        self._push_agents.discard(agent_id)
        # 연결 해제 시 모드 배지 숨김
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if pc:
            thumb = self._thumbnails.get(pc.name)
            if thumb:
                thumb.update_mode('')

    def _on_connection_mode_changed(self, agent_id: str, mode: str):
        """연결 모드 변경 시 배지 갱신 (lan / wan / relay)"""
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
        """에이전트 원격 업데이트 요청"""
        pc = self.pc_manager.get_pc(pc_name)
        if pc:
            logger.info(f"[업데이트] {pc_name} ({pc.agent_id}) 원격 업데이트 요청")
            # 버튼 즉시 피드백
            thumb = self._thumbnails.get(pc_name)
            if thumb:
                thumb.set_update_status('checking')
            self.agent_server.send_update_request(pc.agent_id)

    def _on_update_status(self, agent_id: str, status_dict: dict):
        """에이전트 업데이트 상태 수신"""
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if not pc:
            return
        thumb = self._thumbnails.get(pc.name)
        if not thumb:
            return
        status = status_dict.get('status', '')
        logger.info(f"[업데이트] {pc.name} 상태: {status}")
        thumb.set_update_status(status, **{
            k: v for k, v in status_dict.items() if k != 'type' and k != 'status'
        })

    def get_selected_agent_ids(self) -> list:
        """선택된 PC들의 agent_id 목록"""
        result = []
        for pc_name in self._selected_pcs:
            pc = self.pc_manager.get_pc(pc_name)
            if pc:
                result.append(pc.agent_id)
        return result

    def select_all(self):
        """전체 선택"""
        for name, thumb in self._thumbnails.items():
            self._selected_pcs.add(name)
            thumb.set_selected(True)
        self.selection_changed.emit(list(self._selected_pcs))

    def deselect_all(self):
        """전체 해제"""
        for thumb in self._thumbnails.values():
            thumb.set_selected(False)
        self._selected_pcs.clear()
        self.selection_changed.emit([])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._thumbnails:
            self.rebuild_grid()
