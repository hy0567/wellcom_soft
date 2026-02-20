"""다중 PC 썸네일 그리드 뷰 (CCTV 모드)

모든 PC의 썸네일을 그리드로 표시하고 주기적으로 갱신한다.
"""

import logging
from typing import Dict

from PyQt6.QtWidgets import (
    QScrollArea, QWidget, QGridLayout, QLabel, QVBoxLayout,
    QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, QByteArray, pyqtSignal, QSize
from PyQt6.QtGui import QPixmap, QColor, QPalette, QMouseEvent

from config import settings
from core.pc_manager import PCManager
from core.agent_server import AgentServer

logger = logging.getLogger(__name__)


class PCThumbnailWidget(QFrame):
    """단일 PC 썸네일 위젯"""

    double_clicked = pyqtSignal(str)  # pc_name

    def __init__(self, pc_name: str, parent=None):
        super().__init__(parent)
        self.pc_name = pc_name
        self._is_online = False

        self.setFrameShape(QFrame.Shape.Box)
        self.setLineWidth(2)
        self._update_border_color()
        self.setMinimumSize(280, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # 썸네일 이미지
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(160)
        self.image_label.setStyleSheet("background-color: #1a1a1a;")
        self.image_label.setText("연결 대기...")
        self.image_label.setStyleSheet(
            "background-color: #1a1a1a; color: #666; font-size: 11px;"
        )
        layout.addWidget(self.image_label)

        # PC 이름 + 상태
        self.name_label = QLabel(pc_name)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet("font-weight: bold; font-size: 11px; padding: 2px;")
        layout.addWidget(self.name_label)

    def update_thumbnail(self, jpeg_data: bytes):
        """썸네일 업데이트"""
        pixmap = QPixmap()
        pixmap.loadFromData(QByteArray(jpeg_data))
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.image_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.image_label.setPixmap(scaled)

    def set_online(self, online: bool):
        self._is_online = online
        self._update_border_color()
        if not online:
            self.image_label.clear()
            self.image_label.setText("오프라인")

    def _update_border_color(self):
        if self._is_online:
            self.setStyleSheet("PCThumbnailWidget { border: 2px solid #4CAF50; }")
        else:
            self.setStyleSheet("PCThumbnailWidget { border: 2px solid #555; }")

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self.double_clicked.emit(self.pc_name)
        event.accept()


class GridView(QScrollArea):
    """다중 PC 썸네일 그리드"""

    open_viewer = pyqtSignal(str)  # pc_name

    def __init__(self, pc_manager: PCManager, agent_server: AgentServer):
        super().__init__()
        self.pc_manager = pc_manager
        self.agent_server = agent_server
        self._thumbnails: Dict[str, PCThumbnailWidget] = {}

        # 스크롤 영역 설정
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._container = QWidget()
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

        # 썸네일 갱신 타이머
        interval = settings.get('screen.thumbnail_interval', 3000)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._request_all_thumbnails)
        self._refresh_timer.start(interval)

    def rebuild_grid(self):
        """그리드 재구성"""
        # 기존 위젯 제거
        for widget in self._thumbnails.values():
            self._grid.removeWidget(widget)
            widget.deleteLater()
        self._thumbnails.clear()

        pcs = self.pc_manager.get_all_pcs()
        columns = self._calculate_columns()

        for i, pc in enumerate(pcs):
            row = i // columns
            col = i % columns

            thumb = PCThumbnailWidget(pc.name)
            thumb.set_online(pc.is_online)
            thumb.double_clicked.connect(self.open_viewer.emit)

            if pc.last_thumbnail:
                thumb.update_thumbnail(pc.last_thumbnail)

            self._grid.addWidget(thumb, row, col)
            self._thumbnails[pc.name] = thumb

    def _calculate_columns(self) -> int:
        """위젯 너비 기반 컬럼 수 계산"""
        user_cols = settings.get('grid_view.columns', 0)
        if user_cols > 0:
            return user_cols

        width = self.viewport().width()
        if width <= 0:
            return 4
        col_width = 300  # 썸네일 + 여백
        cols = max(1, width // col_width)
        return cols

    def _on_status_changed(self, pc_name: str):
        """PC 상태 변경"""
        thumb = self._thumbnails.get(pc_name)
        pc = self.pc_manager.get_pc(pc_name)
        if thumb and pc:
            thumb.set_online(pc.is_online)

    def _on_thumbnail_received(self, agent_id: str, jpeg_data: bytes):
        """썸네일 수신"""
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if pc:
            thumb = self._thumbnails.get(pc.name)
            if thumb:
                thumb.update_thumbnail(jpeg_data)

    def _request_all_thumbnails(self):
        """온라인 PC들에 썸네일 요청"""
        for pc in self.pc_manager.get_online_pcs():
            if not pc.is_streaming:  # 스트리밍 중인 PC는 스킵
                self.agent_server.request_thumbnail(pc.agent_id)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 리사이즈 시 컬럼 수 재계산
        if self._thumbnails:
            self.rebuild_grid()
