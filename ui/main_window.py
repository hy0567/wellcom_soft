"""WellcomSOFT 메인 윈도우"""

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QSplitter, QTreeWidget, QTreeWidgetItem,
    QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QStatusBar, QLabel, QMenu, QMessageBox,
    QInputDialog,
)
from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QAction, QIcon, QColor, QBrush

from config import settings, WINDOW_TITLE, WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT
from core.pc_manager import PCManager
from core.agent_server import AgentServer
from ui.viewer_widget import ViewerWidget
from ui.grid_view import GridView

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """WellcomSOFT 메인 윈도우"""

    def __init__(self, agent_server: AgentServer, pc_manager: PCManager):
        super().__init__()
        self.agent_server = agent_server
        self.pc_manager = pc_manager

        self._viewer_tabs = {}  # pc_name → tab_index

        self._init_ui()
        self._connect_signals()
        self._load_settings()
        self._start_timers()

        # 서버에서 PC 목록 로드 (로컬 DB + 서버 동기화)
        self.pc_manager.load_from_db()
        self.pc_manager.load_from_server()

    def _init_ui(self):
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        # 메뉴바
        self._create_menus()

        # 툴바
        self._create_toolbar()

        # 중앙 위젯
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # 스플리터: 좌측 트리 + 우측 탭
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter)

        # 좌측: PC 트리
        self._create_tree()
        self.splitter.addWidget(self.tree)

        # 우측: 탭 위젯
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._on_tab_close)
        self.splitter.addWidget(self.tabs)

        # 그리드 뷰 탭 (기본)
        self.grid_view = GridView(self.pc_manager, self.agent_server)
        self.grid_view.open_viewer.connect(self._open_viewer)
        self.tabs.addTab(self.grid_view, "모니터링")
        self.tabs.tabBar().setTabButton(0, self.tabs.tabBar().ButtonPosition.RightSide, None)

        # 스플리터 비율
        self.splitter.setSizes([250, 1150])

        # 상태 바
        self.status_label = QLabel("준비")
        self.statusBar().addWidget(self.status_label, 1)
        self.agent_count_label = QLabel("에이전트: 0/0")
        self.statusBar().addPermanentWidget(self.agent_count_label)

    def _create_menus(self):
        menubar = self.menuBar()

        # 파일 메뉴
        file_menu = menubar.addMenu("파일(&F)")

        quit_action = QAction("종료(&Q)", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # PC 메뉴
        pc_menu = menubar.addMenu("PC(&P)")

        add_pc_action = QAction("PC 추가(&A)...", self)
        add_pc_action.triggered.connect(self._add_pc_dialog)
        pc_menu.addAction(add_pc_action)

        remove_pc_action = QAction("PC 제거(&R)", self)
        remove_pc_action.triggered.connect(self._remove_selected_pc)
        pc_menu.addAction(remove_pc_action)

        pc_menu.addSeparator()

        refresh_action = QAction("새로고침(&F)", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self._manual_refresh)
        pc_menu.addAction(refresh_action)

        # 도구 메뉴
        tools_menu = menubar.addMenu("도구(&T)")

        broadcast_cmd_action = QAction("명령 실행(&C)...", self)
        broadcast_cmd_action.triggered.connect(self._broadcast_command_dialog)
        tools_menu.addAction(broadcast_cmd_action)

    def _create_toolbar(self):
        toolbar = QToolBar("메인 도구")
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.action_refresh = QAction("새로고침", self)
        self.action_refresh.setShortcut("F5")
        self.action_refresh.triggered.connect(self._manual_refresh)
        toolbar.addAction(self.action_refresh)

        toolbar.addSeparator()

        self.action_add_pc = QAction("PC 추가", self)
        self.action_add_pc.triggered.connect(self._add_pc_dialog)
        toolbar.addAction(self.action_add_pc)

    def _create_tree(self):
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["PC 목록"])
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_tree_double_click)
        self.tree.setMinimumWidth(200)

    def _connect_signals(self):
        signals = self.pc_manager.signals
        signals.device_added.connect(self._refresh_tree)
        signals.device_removed.connect(self._refresh_tree)
        signals.device_renamed.connect(self._refresh_tree)
        signals.device_status_changed.connect(self._on_status_changed)
        signals.devices_reloaded.connect(self._refresh_tree)

    def _load_settings(self):
        w = settings.get('window.width', WINDOW_MIN_WIDTH)
        h = settings.get('window.height', WINDOW_MIN_HEIGHT)
        x = settings.get('window.x', 100)
        y = settings.get('window.y', 100)
        self.setGeometry(x, y, w, h)
        if settings.get('window.maximized', False):
            self.showMaximized()

    def _start_timers(self):
        # 상태 바 갱신
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status_bar)
        self._status_timer.start(2000)

        # 서버 동기화 (30초 간격)
        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._sync_from_server)
        self._sync_timer.start(30000)

    # ==================== 트리 관리 ====================

    def _refresh_tree(self, *args):
        """PC 트리 재구성"""
        self.tree.clear()
        groups = {}

        for pc in self.pc_manager.get_all_pcs():
            group_name = pc.group
            if group_name not in groups:
                group_item = QTreeWidgetItem(self.tree, [group_name])
                group_item.setExpanded(True)
                group_item.setData(0, Qt.ItemDataRole.UserRole, 'group')
                groups[group_name] = group_item

            pc_item = QTreeWidgetItem(groups[group_name], [pc.name])
            pc_item.setData(0, Qt.ItemDataRole.UserRole, 'pc')
            pc_item.setData(0, Qt.ItemDataRole.UserRole + 1, pc.name)

            # 온라인/오프라인 색상
            if pc.is_online:
                pc_item.setForeground(0, QBrush(QColor(76, 175, 80)))
            else:
                pc_item.setForeground(0, QBrush(QColor(158, 158, 158)))

        self._update_status_bar()

    def _on_status_changed(self, pc_name: str):
        """개별 PC 상태 변경 시 트리 색상 업데이트"""
        self._refresh_tree()

    def _on_tree_double_click(self, item: QTreeWidgetItem, column: int):
        """트리 더블클릭 → 뷰어 열기"""
        item_type = item.data(0, Qt.ItemDataRole.UserRole)
        if item_type == 'pc':
            pc_name = item.data(0, Qt.ItemDataRole.UserRole + 1)
            self._open_viewer(pc_name)

    def _on_tree_context_menu(self, pos):
        """트리 우클릭 메뉴"""
        item = self.tree.itemAt(pos)
        if not item:
            return

        item_type = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)

        if item_type == 'pc':
            pc_name = item.data(0, Qt.ItemDataRole.UserRole + 1)

            open_action = menu.addAction("원격 제어")
            open_action.triggered.connect(lambda: self._open_viewer(pc_name))

            menu.addSeparator()

            rename_action = menu.addAction("이름 변경")
            rename_action.triggered.connect(lambda: self._rename_pc(pc_name))

            move_action = menu.addAction("그룹 이동")
            move_action.triggered.connect(lambda: self._move_pc_group(pc_name))

            menu.addSeparator()

            remove_action = menu.addAction("제거")
            remove_action.triggered.connect(lambda: self._remove_pc(pc_name))

        elif item_type == 'group':
            group_name = item.text(0)
            rename_action = menu.addAction("그룹 이름 변경")
            rename_action.triggered.connect(lambda: self._rename_group(group_name))

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ==================== 뷰어 관리 ====================

    def _open_viewer(self, pc_name: str):
        """원격 뷰어 탭 열기"""
        # 이미 열려있으면 탭 전환
        if pc_name in self._viewer_tabs:
            idx = self._viewer_tabs[pc_name]
            if idx < self.tabs.count():
                self.tabs.setCurrentIndex(idx)
                return

        pc = self.pc_manager.get_pc(pc_name)
        if not pc:
            return

        if not pc.is_online:
            QMessageBox.warning(self, "오프라인", f"{pc_name}이(가) 오프라인 상태입니다.")
            return

        # 뷰어 위젯 생성
        viewer = ViewerWidget(pc, self.agent_server)
        idx = self.tabs.addTab(viewer, f"제어: {pc_name}")
        self._viewer_tabs[pc_name] = idx
        self.tabs.setCurrentIndex(idx)

        # 스트리밍 시작
        self.agent_server.start_streaming(
            pc.agent_id,
            fps=settings.get('screen.stream_fps', 15),
            quality=settings.get('screen.stream_quality', 60),
        )

        # 스트림 프레임 시그널 연결
        self.agent_server.screen_frame_received.connect(viewer.on_frame_received)

    def _on_tab_close(self, index: int):
        """탭 닫기"""
        widget = self.tabs.widget(index)
        if isinstance(widget, ViewerWidget):
            pc_name = widget.pc_name
            # 스트리밍 중지
            pc = self.pc_manager.get_pc(pc_name)
            if pc:
                self.agent_server.stop_streaming(pc.agent_id)
            self._viewer_tabs.pop(pc_name, None)
            # 시그널 해제
            try:
                self.agent_server.screen_frame_received.disconnect(widget.on_frame_received)
            except TypeError:
                pass

        self.tabs.removeTab(index)

        # 탭 인덱스 재계산
        self._viewer_tabs.clear()
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, ViewerWidget):
                self._viewer_tabs[w.pc_name] = i

    # ==================== PC 관리 다이얼로그 ====================

    def _add_pc_dialog(self):
        """PC 수동 추가"""
        name, ok = QInputDialog.getText(self, "PC 추가", "PC 이름:")
        if not ok or not name.strip():
            return

        agent_id, ok = QInputDialog.getText(self, "PC 추가", "Agent ID (호스트명):")
        if not ok or not agent_id.strip():
            return

        pc = self.pc_manager.add_pc(name=name.strip(), agent_id=agent_id.strip())
        if not pc:
            QMessageBox.warning(self, "오류", "PC 추가에 실패했습니다. 이름이 중복될 수 있습니다.")

    def _remove_selected_pc(self):
        """선택된 PC 제거"""
        item = self.tree.currentItem()
        if not item:
            return

        item_type = item.data(0, Qt.ItemDataRole.UserRole)
        if item_type == 'pc':
            pc_name = item.data(0, Qt.ItemDataRole.UserRole + 1)
            self._remove_pc(pc_name)

    def _remove_pc(self, pc_name: str):
        reply = QMessageBox.question(
            self, "PC 제거",
            f"'{pc_name}'을(를) 제거하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.pc_manager.remove_pc(pc_name)

    def _rename_pc(self, pc_name: str):
        new_name, ok = QInputDialog.getText(
            self, "이름 변경", "새 이름:", text=pc_name
        )
        if ok and new_name.strip() and new_name.strip() != pc_name:
            if not self.pc_manager.rename_pc(pc_name, new_name.strip()):
                QMessageBox.warning(self, "오류", "이름 변경 실패")

    def _move_pc_group(self, pc_name: str):
        groups = self.pc_manager.get_groups()
        group, ok = QInputDialog.getItem(
            self, "그룹 이동", "대상 그룹:", groups, 0, True
        )
        if ok and group:
            # 새 그룹이면 DB에 추가
            if group not in groups:
                self.pc_manager.db.add_group(group)
            self.pc_manager.move_pc_to_group(pc_name, group)

    def _rename_group(self, group_name: str):
        new_name, ok = QInputDialog.getText(
            self, "그룹 이름 변경", "새 그룹 이름:", text=group_name
        )
        if ok and new_name.strip() and new_name.strip() != group_name:
            for pc in self.pc_manager.get_pcs_by_group(group_name):
                self.pc_manager.move_pc_to_group(pc.name, new_name.strip())
            self._refresh_tree()

    # ==================== 도구 ====================

    def _broadcast_command_dialog(self):
        """명령 실행 다이얼로그"""
        command, ok = QInputDialog.getText(
            self, "원격 명령 실행", "명령어:"
        )
        if not ok or not command.strip():
            return

        online_pcs = self.pc_manager.get_online_pcs()
        if not online_pcs:
            QMessageBox.information(self, "알림", "온라인 PC가 없습니다.")
            return

        agent_ids = [pc.agent_id for pc in online_pcs]
        self.agent_server.broadcast_command(agent_ids, command.strip())
        self.status_label.setText(f"명령 전송: {command.strip()} → {len(agent_ids)}대")

    # ==================== 서버 동기화 ====================

    def _manual_refresh(self):
        """수동 새로고침 (서버 동기화 + 트리 재구성)"""
        self._sync_from_server()
        self._refresh_tree()

    def _sync_from_server(self):
        """서버에서 에이전트 목록 동기화"""
        try:
            self.pc_manager.load_from_server()
        except Exception as e:
            logger.debug(f"서버 동기화 실패: {e}")

    # ==================== 상태 바 ====================

    def _update_status_bar(self):
        from api_client import api_client
        stats = self.pc_manager.get_statistics()
        self.agent_count_label.setText(
            f"에이전트: {stats['online']}/{stats['total']}"
        )
        username = api_client.username or '미로그인'
        server_port = settings.get('agent_server.port', 9877)
        self.status_label.setText(
            f"사용자: {username} | "
            f"서버 포트: {server_port} | "
            f"연결: {self.agent_server.connected_count}대"
        )

    # ==================== 윈도우 이벤트 ====================

    def closeEvent(self, event):
        """종료 시 설정 저장"""
        if not self.isMaximized():
            geo = self.geometry()
            settings.set('window.x', geo.x(), auto_save=False)
            settings.set('window.y', geo.y(), auto_save=False)
            settings.set('window.width', geo.width(), auto_save=False)
            settings.set('window.height', geo.height(), auto_save=False)
        settings.set('window.maximized', self.isMaximized())

        # 스트리밍 중지
        for pc_name in list(self._viewer_tabs.keys()):
            pc = self.pc_manager.get_pc(pc_name)
            if pc:
                self.agent_server.stop_streaming(pc.agent_id)

        event.accept()
