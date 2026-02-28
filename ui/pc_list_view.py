"""PC 전체 목록 뷰 — 기기 정보를 한줄씩 테이블로 표시

QTableWidget 기반, 정렬 가능, 실시간 갱신, 그룹 이동용 선택 지원.
GridView와 동일한 시그널 인터페이스 제공.
"""

import logging
from typing import Dict, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QLineEdit, QLabel, QFrame, QAbstractItemView,
    QPushButton,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QBrush, QFont, QMouseEvent

from config import settings
from core.pc_manager import PCManager
from core.agent_server import AgentServer
from core.pc_device import PCStatus

try:
    from version import __version__ as MANAGER_VERSION
except ImportError:
    MANAGER_VERSION = ''

logger = logging.getLogger(__name__)

# 상태 색상
_COLOR_ONLINE = QColor('#22c55e')
_COLOR_ERROR = QColor('#ef4444')
_COLOR_OFFLINE = QColor('#9ca3af')

# 컬럼 정의 (업데이트 컬럼 추가)
COLUMNS = [
    ('상태', 50),
    ('PC이름', 120),
    ('호스트명', 100),
    ('IP', 120),
    ('공인IP', 120),
    ('OS', 160),
    ('CPU', 140),
    ('코어', 40),
    ('RAM', 50),
    ('GPU', 140),
    ('모드', 60),
    ('버전', 70),
    ('업데이트', 65),
    ('그룹', 70),
    ('메모', 120),
]

# 컬럼 인덱스 상수
COL_STATUS = 0
COL_NAME = 1
COL_HOSTNAME = 2
COL_IP = 3
COL_PUBLIC_IP = 4
COL_OS = 5
COL_CPU = 6
COL_CORES = 7
COL_RAM = 8
COL_GPU = 9
COL_MODE = 10
COL_VERSION = 11
COL_UPDATE = 12
COL_GROUP = 13
COL_MEMO = 14


def _theme():
    """현재 테마에 맞는 색상 반환"""
    theme = settings.get('general.theme', 'light')
    if theme == 'dark':
        return {
            'bg': '#1e1e1e', 'alt_bg': '#252526',
            'header_bg': '#2d2d30', 'header_text': '#e0e0e0',
            'text': '#e0e0e0', 'text2': '#aaa',
            'border': '#3e3e3e', 'selection': '#264f78',
            'bar_bg': '#252525', 'bar_border': '#333',
            'input_bg': '#2d2d30', 'input_border': '#3e3e3e',
            'input_focus': '#007acc', 'grid_line': '#333',
        }
    return {
        'bg': '#ffffff', 'alt_bg': '#f8fafc',
        'header_bg': '#f1f5f9', 'header_text': '#1e293b',
        'text': '#1e293b', 'text2': '#64748b',
        'border': '#e2e8f0', 'selection': '#dbeafe',
        'bar_bg': '#ffffff', 'bar_border': '#e2e8f0',
        'input_bg': '#f8fafc', 'input_border': '#cbd5e1',
        'input_focus': '#3b82f6', 'grid_line': '#e2e8f0',
    }


class PCListView(QWidget):
    """PC 전체 목록 뷰 — 테이블 형태"""

    # GridView와 동일한 시그널 인터페이스
    open_viewer = pyqtSignal(str)               # pc_name
    context_menu_requested = pyqtSignal(str, object)  # pc_name, global_pos
    selection_changed = pyqtSignal(list)         # [pc_name, ...]

    def __init__(self, pc_manager: PCManager, agent_server: AgentServer):
        super().__init__()
        self.pc_manager = pc_manager
        self.agent_server = agent_server
        self._selected_pcs: set = set()
        self._row_map: Dict[str, int] = {}  # pc_name → row index
        self._update_status: Dict[str, dict] = {}  # agent_id → {status, ...}

        self._setup_ui()
        self._connect_signals()

        # 주기적 갱신 (상태 변경 반영)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_statuses)
        self._refresh_timer.start(5000)

    def _setup_ui(self):
        c = _theme()
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 검색 바
        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet(f"""
            QWidget {{ background-color: {c['bar_bg']}; }}
            QWidget {{ border-bottom: 1px solid {c['bar_border']}; }}
            QLabel {{ color: {c['text2']}; font-size: 11px; background: transparent;
                      border: none; }}
            QLineEdit {{ background-color: {c['input_bg']}; color: {c['text']};
                         border: 1px solid {c['input_border']}; border-radius: 4px;
                         padding: 3px 8px; font-size: 11px; }}
            QLineEdit:focus {{ border: 1px solid {c['input_focus']}; }}
            QPushButton {{ font-size: 11px; padding: 3px 10px; border-radius: 4px; }}
        """)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(10, 4, 10, 4)
        bar_layout.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("PC 검색...")
        self._search_input.setFixedWidth(200)
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_filter_changed)
        bar_layout.addWidget(self._search_input)

        # 전체 업데이트 버튼
        self._update_all_btn = QPushButton("전체 업데이트")
        self._update_all_btn.setFixedHeight(24)
        self._update_all_btn.setStyleSheet(
            "QPushButton { background: #f97316; color: white; border: none; "
            "border-radius: 4px; font-weight: bold; padding: 3px 12px; }"
            "QPushButton:hover { background: #ea580c; }"
            "QPushButton:disabled { background: #9ca3af; }"
        )
        self._update_all_btn.setToolTip("온라인이고 업데이트가 필요한 모든 에이전트를 업데이트")
        self._update_all_btn.clicked.connect(self._on_update_all)
        bar_layout.addWidget(self._update_all_btn)

        bar_layout.addStretch()

        self._count_label = QLabel()
        self._count_label.setStyleSheet(f"color: {c['text2']}; font-size: 11px;")
        bar_layout.addWidget(self._count_label)

        main_layout.addWidget(bar)

        # 테이블
        self._table = QTableWidget()
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels([col[0] for col in COLUMNS])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(True)
        self._table.setWordWrap(False)

        # 컬럼 너비
        header = self._table.horizontalHeader()
        for i, (name, width) in enumerate(COLUMNS):
            self._table.setColumnWidth(i, width)
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        # 스타일
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {c['bg']};
                alternate-background-color: {c['alt_bg']};
                color: {c['text']};
                gridline-color: {c['grid_line']};
                border: none;
                font-size: 12px;
            }}
            QTableWidget::item {{
                padding: 4px 8px;
                border: none;
            }}
            QTableWidget::item:selected {{
                background-color: {c['selection']};
            }}
            QHeaderView::section {{
                background-color: {c['header_bg']};
                color: {c['header_text']};
                padding: 6px 8px;
                border: none;
                border-right: 1px solid {c['border']};
                border-bottom: 1px solid {c['border']};
                font-size: 11px;
                font-weight: bold;
            }}
        """)

        # 이벤트 연결
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.itemSelectionChanged.connect(self._on_selection_change)

        main_layout.addWidget(self._table)

    def _connect_signals(self):
        self.pc_manager.signals.devices_reloaded.connect(self.rebuild_list)
        self.pc_manager.signals.device_added.connect(lambda _: self.rebuild_list())
        self.pc_manager.signals.device_removed.connect(lambda _: self.rebuild_list())
        self.pc_manager.signals.device_status_changed.connect(self._on_status_changed)
        self.agent_server.agent_connected.connect(
            lambda agent_id, ip: self._on_agent_event(agent_id))
        self.agent_server.agent_disconnected.connect(
            lambda agent_id: self._on_agent_event(agent_id))
        self.agent_server.connection_mode_changed.connect(
            lambda agent_id, mode: self._on_agent_event(agent_id))
        self.agent_server.agent_info_received.connect(
            lambda agent_id, info: self._on_agent_event(agent_id))
        # 업데이트 상태 수신
        self.agent_server.update_status_received.connect(self._on_update_status)

    # ==================== 테이블 구성 ====================

    def rebuild_list(self):
        """전체 PC 목록 테이블 재구성"""
        self._table.setSortingEnabled(False)

        filter_text = self._search_input.text().strip().lower()
        all_pcs = self.pc_manager.get_all_pcs()

        if filter_text:
            pcs = [
                pc for pc in all_pcs
                if filter_text in pc.name.lower()
                or filter_text in getattr(pc.info, 'memo', '').lower()
                or filter_text in getattr(pc.info, 'hostname', '').lower()
                or filter_text in getattr(pc.info, 'ip', '').lower()
            ]
        else:
            pcs = all_pcs

        self._table.setRowCount(len(pcs))
        self._row_map.clear()

        for row, pc in enumerate(pcs):
            self._row_map[pc.name] = row
            self._fill_row(row, pc)

        self._table.setSortingEnabled(True)

        online = sum(1 for pc in pcs if pc.is_online)
        self._count_label.setText(f"전체 {len(pcs)}대 / 온라인 {online}대")

    def _fill_row(self, row: int, pc):
        """테이블 행 하나 채우기"""
        info = pc.info

        # 상태
        status_text = '온라인' if pc.is_online else '오프라인'
        status_color = _COLOR_ONLINE if pc.is_online else _COLOR_OFFLINE
        if pc.status == PCStatus.ERROR:
            status_text = '오류'
            status_color = _COLOR_ERROR
        item = QTableWidgetItem(status_text)
        item.setForeground(QBrush(status_color))
        item.setFont(QFont("", 11, QFont.Weight.Bold))
        item.setData(Qt.ItemDataRole.UserRole, pc.name)
        self._table.setItem(row, COL_STATUS, item)

        # PC이름
        name_item = QTableWidgetItem(pc.name)
        name_item.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        name_item.setData(Qt.ItemDataRole.UserRole, pc.name)
        self._table.setItem(row, COL_NAME, name_item)

        # 호스트명
        self._set_text_item(row, COL_HOSTNAME, getattr(info, 'hostname', ''), pc.name)

        # IP
        self._set_text_item(row, COL_IP, getattr(info, 'ip', ''), pc.name)

        # 공인IP
        self._set_text_item(row, COL_PUBLIC_IP, getattr(info, 'public_ip', ''), pc.name)

        # OS
        os_info = getattr(info, 'os_info', '')
        if os_info:
            parts = os_info.split()
            if len(parts) > 3:
                os_info = ' '.join(parts[:3])
        self._set_text_item(row, COL_OS, os_info, pc.name)

        # CPU
        self._set_text_item(row, COL_CPU, getattr(info, 'cpu_model', ''), pc.name)

        # 코어
        cores = getattr(info, 'cpu_cores', 0)
        self._set_text_item(row, COL_CORES, str(cores) if cores else '', pc.name)

        # RAM
        ram = getattr(info, 'ram_gb', 0.0)
        ram_text = f"{ram}GB" if ram else ''
        self._set_text_item(row, COL_RAM, ram_text, pc.name)

        # GPU
        self._set_text_item(row, COL_GPU, getattr(info, 'gpu_model', ''), pc.name)

        # 모드
        mode = getattr(info, 'connection_mode', '')
        mode_display = {
            'lan': 'LAN', 'wan': 'WAN', 'relay': 'Relay',
            'udp_p2p': 'P2P',
        }.get(mode, '')
        mode_item = QTableWidgetItem(mode_display)
        if mode_display:
            mode_colors = {
                'LAN': QColor('#22c55e'), 'WAN': QColor('#3b82f6'),
                'Relay': QColor('#f97316'), 'P2P': QColor('#8b5cf6'),
            }
            mode_item.setForeground(QBrush(mode_colors.get(mode_display, QColor('#9ca3af'))))
            mode_item.setFont(QFont("", 10, QFont.Weight.Bold))
        mode_item.setData(Qt.ItemDataRole.UserRole, pc.name)
        self._table.setItem(row, COL_MODE, mode_item)

        # 버전
        version = getattr(info, 'agent_version', '')
        needs_update = bool(version and MANAGER_VERSION and version != MANAGER_VERSION)
        ver_item = QTableWidgetItem(version)
        if needs_update:
            ver_item.setForeground(QBrush(QColor('#f97316')))
            ver_item.setToolTip(f"최신: {MANAGER_VERSION}")
        ver_item.setData(Qt.ItemDataRole.UserRole, pc.name)
        self._table.setItem(row, COL_VERSION, ver_item)

        # 업데이트 버튼 셀
        update_text = ''
        update_color = QColor('#9ca3af')
        agent_id = pc.agent_id
        us = self._update_status.get(agent_id, {})
        us_status = us.get('status', '')

        if us_status == 'downloading':
            pct = us.get('progress', 0)
            update_text = f'{pct}%'
            update_color = QColor('#3b82f6')
        elif us_status == 'checking':
            update_text = '확인중'
            update_color = QColor('#3b82f6')
        elif us_status == 'restarting':
            update_text = '재시작'
            update_color = QColor('#22c55e')
        elif us_status == 'up_to_date':
            update_text = '최신'
            update_color = QColor('#22c55e')
        elif us_status == 'failed':
            update_text = '실패'
            update_color = QColor('#ef4444')
        elif needs_update and pc.is_online:
            update_text = '업데이트'
            update_color = QColor('#f97316')
        elif version and version == MANAGER_VERSION:
            update_text = '최신'
            update_color = QColor('#22c55e')

        up_item = QTableWidgetItem(update_text)
        up_item.setForeground(QBrush(update_color))
        up_item.setFont(QFont("", 10, QFont.Weight.Bold))
        up_item.setData(Qt.ItemDataRole.UserRole, pc.name)
        up_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, COL_UPDATE, up_item)

        # 그룹
        self._set_text_item(row, COL_GROUP, getattr(info, 'group', 'default'), pc.name)

        # 메모
        self._set_text_item(row, COL_MEMO, getattr(info, 'memo', ''), pc.name)

        # 행 높이
        self._table.setRowHeight(row, 32)

    def _set_text_item(self, row: int, col: int, text: str, pc_name: str):
        """일반 텍스트 셀 설정"""
        item = QTableWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, pc_name)
        self._table.setItem(row, col, item)

    # ==================== 이벤트 핸들러 ====================

    def _on_double_click(self, index):
        row = index.row()
        col = index.column()

        item = self._table.item(row, COL_STATUS)
        if not item:
            return
        pc_name = item.data(Qt.ItemDataRole.UserRole)
        if not pc_name:
            return

        # 업데이트 컬럼 더블클릭 → 업데이트 실행
        if col == COL_UPDATE:
            self._trigger_update(pc_name)
            return

        # 그 외 → 뷰어 열기
        self.open_viewer.emit(pc_name)

    def _on_context_menu(self, pos):
        item = self._table.itemAt(pos)
        if item:
            pc_name = item.data(Qt.ItemDataRole.UserRole)
            if pc_name:
                global_pos = self._table.viewport().mapToGlobal(pos)
                self.context_menu_requested.emit(pc_name, global_pos)

    def _on_selection_change(self):
        self._selected_pcs.clear()
        for item in self._table.selectedItems():
            pc_name = item.data(Qt.ItemDataRole.UserRole)
            if pc_name:
                self._selected_pcs.add(pc_name)
        self.selection_changed.emit(list(self._selected_pcs))

    def _on_filter_changed(self, text: str):
        self.rebuild_list()

    def _on_status_changed(self, pc_name: str):
        """단일 PC 상태 변경 → 해당 행만 업데이트"""
        pc = self.pc_manager.get_pc(pc_name)
        if not pc:
            return
        row = self._row_map.get(pc_name)
        if row is not None and row < self._table.rowCount():
            self._table.setSortingEnabled(False)
            self._fill_row(row, pc)
            self._table.setSortingEnabled(True)
        else:
            self.rebuild_list()

    def _on_agent_event(self, agent_id: str):
        """에이전트 이벤트 → 해당 PC 행 업데이트"""
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if pc:
            self._on_status_changed(pc.name)

    def _on_update_status(self, agent_id: str, status_dict: dict):
        """에이전트 업데이트 상태 수신"""
        self._update_status[agent_id] = status_dict
        status = status_dict.get('status', '')
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if pc:
            pct = status_dict.get('progress', 0)
            logger.info(f"[업데이트] {pc.name} 상태: {status}"
                        + (f" ({pct}%)" if status == 'downloading' else ""))
            self._on_status_changed(pc.name)

    def _trigger_update(self, pc_name: str):
        """단일 PC 업데이트 트리거"""
        pc = self.pc_manager.get_pc(pc_name)
        if not pc or not pc.is_online:
            return
        version = getattr(pc.info, 'agent_version', '')
        if version and MANAGER_VERSION and version == MANAGER_VERSION:
            return  # 이미 최신
        logger.info(f"[업데이트] {pc_name} ({pc.agent_id}) 원격 업데이트 요청")
        self._update_status[pc.agent_id] = {'status': 'checking'}
        self._on_status_changed(pc_name)
        self.agent_server.send_update_request(pc.agent_id)

    def _on_update_all(self):
        """온라인이고 업데이트 필요한 모든 에이전트 업데이트"""
        count = 0
        for pc in self.pc_manager.get_all_pcs():
            if not pc.is_online:
                continue
            version = getattr(pc.info, 'agent_version', '')
            if version and MANAGER_VERSION and version != MANAGER_VERSION:
                self._trigger_update(pc.name)
                count += 1
        if count:
            logger.info(f"[업데이트] 전체 업데이트 요청: {count}대")
        else:
            logger.info("[업데이트] 업데이트 필요한 에이전트 없음")

    def _refresh_statuses(self):
        """주기적 상태 갱신"""
        online = sum(1 for pc in self.pc_manager.get_all_pcs() if pc.is_online)
        total = len(self.pc_manager.get_all_pcs())
        self._count_label.setText(f"전체 {total}대 / 온라인 {online}대")

    # ==================== 공개 메서드 (GridView 호환) ====================

    def get_selected_agent_ids(self) -> list:
        result = []
        for pc_name in self._selected_pcs:
            pc = self.pc_manager.get_pc(pc_name)
            if pc:
                result.append(pc.agent_id)
        return result

    def select_all(self):
        self._table.selectAll()

    def deselect_all(self):
        self._table.clearSelection()
        self._selected_pcs.clear()
        self.selection_changed.emit([])
