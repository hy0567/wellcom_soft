"""WellcomSOFT 메인 윈도우 (v2.0 — LinkIO Desktop 스타일)

그리드 뷰 중심 레이아웃, DesktopWidget 별도 창,
멀컨/그룹컨트롤 툴바, 설정 다이얼로그, 다크/라이트 테마.
"""

import logging
from typing import Dict, Optional

from PyQt6.QtWidgets import (
    QMainWindow, QSplitter, QTreeWidget, QTreeWidgetItem,
    QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QLabel, QMenu, QMessageBox,
    QInputDialog, QApplication,
)
from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QAction, QColor, QBrush, QFont, QShortcut, QKeySequence

from config import settings, WINDOW_TITLE, WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT
from core.pc_manager import PCManager
from core.agent_server import AgentServer
from core.multi_control import MultiControlManager
from core.pc_device import PCStatus
from core.script_engine import ScriptEngine
from core.key_mapper import KeyMapper
from core.recorder import Recorder, Player, RecordingManager
from ui.grid_view import GridView
from ui.desktop_widget import DesktopWidget
from ui.settings_dialog import SettingsDialog
from ui.themes import get_theme_stylesheet

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """WellcomSOFT 메인 윈도우 (v2.0)"""

    def __init__(self, agent_server: AgentServer, pc_manager: PCManager):
        super().__init__()
        self.agent_server = agent_server
        self.pc_manager = pc_manager
        self.multi_control = MultiControlManager(agent_server)
        self.script_engine = ScriptEngine(agent_server)
        self.key_mapper = KeyMapper(agent_server)
        self.recorder = Recorder()
        self.player = Player(agent_server)
        self.recording_manager = RecordingManager()

        self._desktop_widgets: Dict[str, DesktopWidget] = {}

        self._init_ui()
        self._connect_signals()
        self._load_settings()
        self._start_timers()
        self._apply_theme()

        # 서버에서 PC 목록 로드
        self.pc_manager.load_from_db()
        self.pc_manager.load_from_server()

    def _init_ui(self):
        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        self._create_menus()
        self._create_toolbar()

        # 중앙 위젯
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # 스플리터: 좌측 트리 + 우측 그리드
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.splitter)

        # 좌측: PC 트리
        self._create_tree()
        self.splitter.addWidget(self.tree)

        # 우측: 그리드 뷰 (기본 화면)
        self.grid_view = GridView(self.pc_manager, self.agent_server)
        self.grid_view.open_viewer.connect(self._open_viewer)
        self.grid_view.context_menu_requested.connect(self._on_grid_context_menu)
        self.grid_view.selection_changed.connect(self._on_selection_changed)
        self.splitter.addWidget(self.grid_view)

        self.splitter.setSizes([220, 1180])

        # 상태 바
        self.status_label = QLabel("준비")
        self.statusBar().addWidget(self.status_label, 1)

        # v2.0.9 — WS 연결 상태 인디케이터
        self._ws_status_label = QLabel("● WS 연결 중")
        self._ws_status_label.setStyleSheet(
            "color: #FFA726; padding: 0 6px; font-size: 11px; font-weight: bold;"
        )
        self.statusBar().addPermanentWidget(self._ws_status_label)

        self.multi_label = QLabel("")
        self.statusBar().addPermanentWidget(self.multi_label)

        self.agent_count_label = QLabel("에이전트: 0/0")
        self.statusBar().addPermanentWidget(self.agent_count_label)

        # 단축키
        QShortcut(QKeySequence("Ctrl+1"), self, self._toggle_multi_control)
        QShortcut(QKeySequence("Ctrl+2"), self, self._toggle_group_control)

    def _create_menus(self):
        menubar = self.menuBar()

        # 파일
        file_menu = menubar.addMenu("파일(&F)")
        quit_action = QAction("종료(&Q)", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # PC
        pc_menu = menubar.addMenu("PC(&P)")

        add_pc_action = QAction("PC 추가(&A)...", self)
        add_pc_action.triggered.connect(self._add_pc_dialog)
        pc_menu.addAction(add_pc_action)

        remove_pc_action = QAction("PC 제거(&R)", self)
        remove_pc_action.triggered.connect(self._remove_selected_pc)
        pc_menu.addAction(remove_pc_action)

        pc_menu.addSeparator()

        select_all_action = QAction("전체 선택(&S)", self)
        select_all_action.setShortcut("Ctrl+A")
        select_all_action.triggered.connect(self._select_all_pcs)
        pc_menu.addAction(select_all_action)

        deselect_action = QAction("선택 해제(&D)", self)
        deselect_action.triggered.connect(lambda: self.grid_view.deselect_all())
        pc_menu.addAction(deselect_action)

        pc_menu.addSeparator()

        refresh_action = QAction("새로고침(&F)", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self._manual_refresh)
        pc_menu.addAction(refresh_action)

        # 제어
        control_menu = menubar.addMenu("제어(&C)")

        multi_action = QAction("멀티컨트롤(&M)  Ctrl+1", self)
        multi_action.triggered.connect(self._toggle_multi_control)
        control_menu.addAction(multi_action)

        group_action = QAction("그룹컨트롤(&G)  Ctrl+2", self)
        group_action.triggered.connect(self._toggle_group_control)
        control_menu.addAction(group_action)

        control_menu.addSeparator()

        broadcast_cmd_action = QAction("명령 실행(&C)...", self)
        broadcast_cmd_action.triggered.connect(self._broadcast_command_dialog)
        control_menu.addAction(broadcast_cmd_action)

        # 자동화
        auto_menu = menubar.addMenu("자동화(&A)")

        script_action = QAction("스크립트 관리(&S)...", self)
        script_action.triggered.connect(self._open_script_manager)
        auto_menu.addAction(script_action)

        keymap_action = QAction("키매핑 관리(&K)...", self)
        keymap_action.triggered.connect(self._open_keymap_editor)
        auto_menu.addAction(keymap_action)

        record_action = QAction("녹화/재생(&R)...", self)
        record_action.triggered.connect(self._open_recording_manager)
        auto_menu.addAction(record_action)

        # 도구
        tools_menu = menubar.addMenu("도구(&T)")

        settings_action = QAction("설정(&S)...", self)
        settings_action.triggered.connect(self._open_settings)
        tools_menu.addAction(settings_action)

        # 도움말
        help_menu = menubar.addMenu("도움말(&H)")

        update_action = QAction("업데이트 확인(&U)...", self)
        update_action.triggered.connect(self._check_for_updates)
        help_menu.addAction(update_action)

        help_menu.addSeparator()

        about_action = QAction("WellcomSOFT 정보(&A)", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

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

        self.action_multi = QAction("멀컨", self)
        self.action_multi.setCheckable(True)
        self.action_multi.setToolTip("멀티컨트롤 (Ctrl+1)")
        self.action_multi.triggered.connect(self._toggle_multi_control)
        toolbar.addAction(self.action_multi)

        self.action_group = QAction("그룹", self)
        self.action_group.setCheckable(True)
        self.action_group.setToolTip("그룹컨트롤 (Ctrl+2)")
        self.action_group.triggered.connect(self._toggle_group_control)
        toolbar.addAction(self.action_group)

        toolbar.addSeparator()

        self.action_select_all = QAction("전체선택", self)
        self.action_select_all.triggered.connect(self._select_all_pcs)
        toolbar.addAction(self.action_select_all)

        toolbar.addSeparator()

        self.action_settings = QAction("설정", self)
        self.action_settings.triggered.connect(self._open_settings)
        toolbar.addAction(self.action_settings)

    def _create_tree(self):
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["PC 목록"])
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_tree_double_click)
        self.tree.setMinimumWidth(180)
        self.tree.setMaximumWidth(300)

    def _connect_signals(self):
        signals = self.pc_manager.signals
        signals.device_added.connect(self._refresh_tree)
        signals.device_removed.connect(self._refresh_tree)
        signals.device_renamed.connect(self._refresh_tree)
        signals.device_status_changed.connect(self._on_status_changed)
        signals.devices_reloaded.connect(self._refresh_tree)
        self.multi_control.mode_changed.connect(self._on_multi_mode_changed)

    def _load_settings(self):
        w = settings.get('window.width', WINDOW_MIN_WIDTH)
        h = settings.get('window.height', WINDOW_MIN_HEIGHT)
        x = settings.get('window.x', 100)
        y = settings.get('window.y', 100)
        self.setGeometry(x, y, w, h)
        if settings.get('window.maximized', False):
            self.showMaximized()

    def _start_timers(self):
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status_bar)
        self._status_timer.start(2000)

        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._sync_from_server)
        self._sync_timer.start(30000)

    def _apply_theme(self):
        theme = settings.get('general.theme', 'dark')
        qss = get_theme_stylesheet(theme)
        QApplication.instance().setStyleSheet(qss)

    # ==================== 트리 관리 ====================

    def _refresh_tree(self, *args):
        self.tree.clear()
        groups = {}

        for pc in self.pc_manager.get_all_pcs():
            group_name = pc.group
            if group_name not in groups:
                group_item = QTreeWidgetItem(self.tree, [group_name])
                group_item.setExpanded(True)
                group_item.setData(0, Qt.ItemDataRole.UserRole, 'group')
                groups[group_name] = group_item

            display = pc.name
            memo = getattr(pc.info, 'memo', '')
            if memo:
                display = f"{pc.name}  [{memo}]"

            pc_item = QTreeWidgetItem(groups[group_name], [display])
            pc_item.setData(0, Qt.ItemDataRole.UserRole, 'pc')
            pc_item.setData(0, Qt.ItemDataRole.UserRole + 1, pc.name)

            if pc.status == PCStatus.ONLINE:
                pc_item.setForeground(0, QBrush(QColor(76, 175, 80)))
            elif pc.status == PCStatus.ERROR:
                pc_item.setForeground(0, QBrush(QColor(244, 67, 54)))
            elif pc.status == PCStatus.CONNECTING:
                pc_item.setForeground(0, QBrush(QColor(255, 193, 7)))
            else:
                pc_item.setForeground(0, QBrush(QColor(158, 158, 158)))

        self._update_status_bar()

    def _on_status_changed(self, pc_name: str):
        self._refresh_tree()

    def _on_tree_double_click(self, item: QTreeWidgetItem, column: int):
        item_type = item.data(0, Qt.ItemDataRole.UserRole)
        if item_type == 'pc':
            pc_name = item.data(0, Qt.ItemDataRole.UserRole + 1)
            self._open_viewer(pc_name)

    def _on_tree_context_menu(self, pos):
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
            memo_action = menu.addAction("메모 편집")
            memo_action.triggered.connect(lambda: self._edit_memo(pc_name))
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

    def _on_grid_context_menu(self, pc_name: str, pos):
        menu = QMenu(self)
        open_action = menu.addAction("원격 제어")
        open_action.triggered.connect(lambda: self._open_viewer(pc_name))
        menu.addSeparator()
        rename_action = menu.addAction("이름 변경")
        rename_action.triggered.connect(lambda: self._rename_pc(pc_name))
        memo_action = menu.addAction("메모 편집")
        memo_action.triggered.connect(lambda: self._edit_memo(pc_name))
        move_action = menu.addAction("그룹 이동")
        move_action.triggered.connect(lambda: self._move_pc_group(pc_name))
        menu.addSeparator()
        remove_action = menu.addAction("제거")
        remove_action.triggered.connect(lambda: self._remove_pc(pc_name))
        menu.exec(pos)

    # ==================== 뷰어 관리 (별도 창) ====================

    def _open_viewer(self, pc_name: str):
        if pc_name in self._desktop_widgets:
            dw = self._desktop_widgets[pc_name]
            dw.raise_()
            dw.activateWindow()
            return

        pc = self.pc_manager.get_pc(pc_name)
        if not pc:
            return

        if not pc.is_online:
            QMessageBox.warning(self, "오프라인", f"{pc_name}이(가) 오프라인 상태입니다.")
            return

        pc_list = [p.name for p in self.pc_manager.get_all_pcs() if p.is_online]

        dw = DesktopWidget(pc, self.agent_server, self.multi_control, pc_list)
        dw.closed.connect(self._on_desktop_widget_closed)
        dw.navigate_request.connect(self._on_navigate_request)

        # LinkIO 스타일: 독립 팝업창으로 열기
        dw.setWindowFlags(
            dw.windowFlags() | Qt.WindowType.Window
        )
        dw.show()
        dw.raise_()
        dw.activateWindow()

        self._desktop_widgets[pc_name] = dw

    def _on_desktop_widget_closed(self, pc_name: str):
        self._desktop_widgets.pop(pc_name, None)

    def _on_navigate_request(self, current_pc_name: str, direction: int):
        online_pcs = [p.name for p in self.pc_manager.get_all_pcs() if p.is_online]
        if not online_pcs or current_pc_name not in online_pcs:
            return
        idx = online_pcs.index(current_pc_name)
        new_idx = (idx + direction) % len(online_pcs)
        new_pc_name = online_pcs[new_idx]
        if new_pc_name == current_pc_name:
            return
        dw = self._desktop_widgets.get(current_pc_name)
        if dw:
            dw.close()
        self._open_viewer(new_pc_name)

    # ==================== 멀컨 ====================

    def _toggle_multi_control(self):
        self.multi_control.toggle_multi_control()
        agent_ids = self.grid_view.get_selected_agent_ids()
        self.multi_control.set_selected_agents(agent_ids)

    def _toggle_group_control(self):
        item = self.tree.currentItem()
        group_name = ''
        if item:
            item_type = item.data(0, Qt.ItemDataRole.UserRole)
            if item_type == 'group':
                group_name = item.text(0)
            elif item_type == 'pc':
                pc_name = item.data(0, Qt.ItemDataRole.UserRole + 1)
                pc = self.pc_manager.get_pc(pc_name)
                if pc:
                    group_name = pc.group

        self.multi_control.toggle_group_control(group_name)
        if self.multi_control.is_active and group_name:
            pcs = self.pc_manager.get_pcs_by_group(group_name)
            agent_ids = [p.agent_id for p in pcs if p.is_online]
            self.multi_control.set_selected_agents(agent_ids)

    def _on_multi_mode_changed(self, mode: str):
        self.action_multi.setChecked(mode == 'multi')
        self.action_group.setChecked(mode == 'group')
        if mode == 'off':
            self.multi_label.setText("")
        elif mode == 'multi':
            self.multi_label.setText(f"멀컨: {len(self.multi_control.selected_agents)}대")
        elif mode == 'group':
            self.multi_label.setText(f"그룹: {len(self.multi_control.selected_agents)}대")

    def _on_selection_changed(self, selected_pc_names: list):
        if self.multi_control.is_active:
            agent_ids = self.grid_view.get_selected_agent_ids()
            self.multi_control.set_selected_agents(agent_ids)
            mode_text = "멀컨" if self.multi_control.mode == 'multi' else "그룹"
            self.multi_label.setText(f"{mode_text}: {len(agent_ids)}대")

    def _select_all_pcs(self):
        self.grid_view.select_all()

    # ==================== PC 관리 ====================

    def _add_pc_dialog(self):
        name, ok = QInputDialog.getText(self, "PC 추가", "PC 이름:")
        if not ok or not name.strip():
            return
        agent_id, ok = QInputDialog.getText(self, "PC 추가", "Agent ID (호스트명):")
        if not ok or not agent_id.strip():
            return
        pc = self.pc_manager.add_pc(name=name.strip(), agent_id=agent_id.strip())
        if not pc:
            QMessageBox.warning(self, "오류", "PC 추가에 실패했습니다.")

    def _remove_selected_pc(self):
        item = self.tree.currentItem()
        if not item:
            return
        item_type = item.data(0, Qt.ItemDataRole.UserRole)
        if item_type == 'pc':
            pc_name = item.data(0, Qt.ItemDataRole.UserRole + 1)
            self._remove_pc(pc_name)

    def _remove_pc(self, pc_name: str):
        reply = QMessageBox.question(
            self, "PC 제거", f"'{pc_name}'을(를) 제거하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.pc_manager.remove_pc(pc_name)

    def _rename_pc(self, pc_name: str):
        new_name, ok = QInputDialog.getText(self, "이름 변경", "새 이름:", text=pc_name)
        if ok and new_name.strip() and new_name.strip() != pc_name:
            if not self.pc_manager.rename_pc(pc_name, new_name.strip()):
                QMessageBox.warning(self, "오류", "이름 변경 실패")

    def _edit_memo(self, pc_name: str):
        pc = self.pc_manager.get_pc(pc_name)
        if not pc:
            return
        current_memo = getattr(pc.info, 'memo', '')
        memo, ok = QInputDialog.getText(self, "메모 편집", f"{pc_name} 메모:", text=current_memo)
        if ok:
            pc.info.memo = memo.strip()
            db_row = self.pc_manager.db.get_pc_by_name(pc_name)
            if db_row:
                self.pc_manager.db.update_pc(db_row['id'], memo=memo.strip())
            self._refresh_tree()
            self.grid_view.rebuild_grid()

    def _move_pc_group(self, pc_name: str):
        groups = self.pc_manager.get_groups()
        group, ok = QInputDialog.getItem(self, "그룹 이동", "대상 그룹:", groups, 0, True)
        if ok and group:
            if group not in groups:
                self.pc_manager.db.add_group(group)
            self.pc_manager.move_pc_to_group(pc_name, group)

    def _rename_group(self, group_name: str):
        new_name, ok = QInputDialog.getText(self, "그룹 이름 변경", "새 그룹 이름:", text=group_name)
        if ok and new_name.strip() and new_name.strip() != group_name:
            for pc in self.pc_manager.get_pcs_by_group(group_name):
                self.pc_manager.move_pc_to_group(pc.name, new_name.strip())
            self._refresh_tree()

    # ==================== 자동화 (스크립트/키매핑/녹화) ====================

    def _open_script_manager(self):
        from ui.script_editor import ScriptListDialog
        dlg = ScriptListDialog(self.script_engine, self)
        dlg.exec()

    def _open_keymap_editor(self):
        from ui.keymap_editor import KeymapEditorDialog
        dlg = KeymapEditorDialog(self.key_mapper, self)
        dlg.exec()

    def _open_recording_manager(self):
        from ui.recording_panel import RecordingDialog
        # 현재 선택된 PC의 agent_id
        agent_id = ""
        item = self.tree.currentItem()
        if item:
            item_type = item.data(0, Qt.ItemDataRole.UserRole)
            if item_type == 'pc':
                pc_name = item.data(0, Qt.ItemDataRole.UserRole + 1)
                pc = self.pc_manager.get_pc(pc_name)
                if pc:
                    agent_id = pc.agent_id

        dlg = RecordingDialog(
            self.recording_manager, self.recorder, self.player,
            agent_id, self,
        )
        dlg.exec()

    # ==================== 설정 ====================

    def _open_settings(self):
        dialog = SettingsDialog(self)
        if dialog.exec():
            self._apply_theme()
            self.grid_view.rebuild_grid()

    # ==================== 도구 ====================

    def _broadcast_command_dialog(self):
        command, ok = QInputDialog.getText(self, "원격 명령 실행", "명령어:")
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
        self._sync_from_server()
        self._refresh_tree()
        self.grid_view.rebuild_grid()

    def _sync_from_server(self):
        try:
            self.pc_manager.load_from_server()
        except Exception as e:
            logger.debug(f"서버 동기화 실패: {e}")

    # ==================== 상태 바 ====================

    def _update_status_bar(self):
        from api_client import api_client
        stats = self.pc_manager.get_statistics()
        self.agent_count_label.setText(f"에이전트: {stats['online']}/{stats['total']}")
        username = api_client.username or '미로그인'

        # v2.0.9 — WS 연결 상태
        ws_connected = self.agent_server._ws is not None
        connected_count = self.agent_server.connected_count
        if ws_connected:
            self._ws_status_label.setText(f"● WS 연결됨 ({connected_count}대)")
            self._ws_status_label.setStyleSheet(
                "color: #4CAF50; padding: 0 6px; font-size: 11px; font-weight: bold;"
            )
        else:
            self._ws_status_label.setText("● WS 연결 끊김")
            self._ws_status_label.setStyleSheet(
                "color: #F44336; padding: 0 6px; font-size: 11px; font-weight: bold;"
            )

        self.status_label.setText(
            f"사용자: {username} | "
            f"WS릴레이: {connected_count}대"
        )

    # ==================== 업데이트 / 정보 ====================

    def _check_for_updates(self):
        try:
            from pathlib import Path
            from version import __version__, __github_repo__
            from updater import UpdateChecker
            from updater.update_dialog import UpdateNotifyDialog, UpdateDialog

            base_dir = settings.get('base_dir', '')
            if not base_dir:
                import main as main_mod
                base_dir = getattr(main_mod, 'BASE_DIR', '')
            if not base_dir:
                import os
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

            token = settings.get('update.github_token', '')
            checker = UpdateChecker(
                Path(base_dir), __github_repo__, token or None,
                running_version=__version__,
            )
            has_update, release_info = checker.check_update()
            if not has_update or not release_info:
                QMessageBox.information(
                    self, "업데이트 확인",
                    f"현재 최신 버전입니다.\n\n현재 버전: v{__version__}"
                )
                return
            notify = UpdateNotifyDialog(checker.get_current_version(), release_info)
            result = notify.exec()
            if result == 0:
                return
            dlg = UpdateDialog(release_info)
            dlg.start_update(checker)
            dlg.exec()
            if dlg.is_success:
                QMessageBox.information(self, "업데이트 완료", "프로그램을 재시작합니다.")
                from main import _restart_application
                _restart_application()
        except Exception as e:
            logger.warning(f"업데이트 확인 실패: {e}")
            QMessageBox.warning(self, "업데이트 오류", f"업데이트 확인 중 오류:\n\n{e}")

    def _show_about(self):
        try:
            from version import __version__, __app_name__
        except ImportError:
            __version__ = "?"
            __app_name__ = "WellcomSOFT"

        QMessageBox.about(
            self, f"{__app_name__} 정보",
            f"<h3>{__app_name__}</h3>"
            f"<p>버전: v{__version__}</p>"
            f"<p>소프트웨어 기반 다중 PC 원격 관리 시스템</p>"
            f"<p>LinkIO Desktop 기반 리빌드</p>"
            f"<hr>"
            f"<p>2025 WellcomSOFT. All rights reserved.</p>"
        )

    # ==================== 윈도우 이벤트 ====================

    def closeEvent(self, event):
        if not self.isMaximized():
            geo = self.geometry()
            settings.set('window.x', geo.x(), auto_save=False)
            settings.set('window.y', geo.y(), auto_save=False)
            settings.set('window.width', geo.width(), auto_save=False)
            settings.set('window.height', geo.height(), auto_save=False)
        settings.set('window.maximized', self.isMaximized())

        for dw in list(self._desktop_widgets.values()):
            dw.close()

        event.accept()
