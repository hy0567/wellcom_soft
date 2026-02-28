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
    QInputDialog, QApplication, QSpinBox, QSystemTrayIcon,
)
from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QAction, QColor, QBrush, QFont, QShortcut, QKeySequence, QIcon

from config import settings, WINDOW_TITLE, WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT, ICON_PATH
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

        self.multi_label = QLabel("")
        self.statusBar().addPermanentWidget(self.multi_label)

        self.agent_count_label = QLabel("에이전트: 0/0")
        self.statusBar().addPermanentWidget(self.agent_count_label)

        from version import __version__
        self.version_label = QLabel(f"v{__version__}")
        self.version_label.setStyleSheet("color: #888; margin-right: 4px;")
        self.statusBar().addPermanentWidget(self.version_label)

        # 시스템 트레이
        self._setup_tray()

        # 단축키
        QShortcut(QKeySequence("Ctrl+1"), self, self._toggle_multi_control)
        QShortcut(QKeySequence("Ctrl+2"), self, self._toggle_group_control)

    def _create_menus(self):
        menubar = self.menuBar()

        # 파일
        file_menu = menubar.addMenu("파일(&F)")
        quit_action = QAction("종료(&Q)", self)
        quit_action.triggered.connect(self._tray_quit)
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

        toolbar.addSeparator()
        toolbar.addWidget(QLabel("  컬럼: "))
        self._col_spin = QSpinBox()
        self._col_spin.setRange(3, 20)
        self._col_spin.setValue(settings.get('grid_view.columns', 5))
        self._col_spin.setFixedWidth(55)
        self._col_spin.setToolTip("한 줄에 표시할 PC 수 (3~20)")
        self._col_spin.valueChanged.connect(self._on_columns_changed)
        toolbar.addWidget(self._col_spin)

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
        # 연결 모드 변경 시 상태 바 즉시 갱신
        self.agent_server.connection_mode_changed.connect(
            lambda agent_id, mode: self._update_status_bar()
        )
        self.agent_server.agent_connected.connect(
            lambda agent_id, ip: self._update_status_bar()
        )
        self.agent_server.agent_disconnected.connect(
            lambda agent_id: self._update_status_bar()
        )

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
        info_action = menu.addAction("PC 정보")
        info_action.triggered.connect(lambda: self._show_pc_info(pc_name))
        menu.addSeparator()

        # 전원 관리 서브메뉴
        pc = self.pc_manager.get_pc(pc_name)
        if pc and pc.is_online:
            power_menu = menu.addMenu("전원 관리")
            shutdown_action = power_menu.addAction("종료")
            shutdown_action.triggered.connect(
                lambda: self._send_power_action(pc_name, 'shutdown'))
            restart_action = power_menu.addAction("재시작")
            restart_action.triggered.connect(
                lambda: self._send_power_action(pc_name, 'restart'))
            logoff_action = power_menu.addAction("로그오프")
            logoff_action.triggered.connect(
                lambda: self._send_power_action(pc_name, 'logoff'))
            sleep_action = power_menu.addAction("절전")
            sleep_action.triggered.connect(
                lambda: self._send_power_action(pc_name, 'sleep'))
            menu.addSeparator()

        # Wake-on-LAN (오프라인 PC에도 표시)
        if pc and not pc.is_online and getattr(pc.info, 'mac_address', ''):
            wol_action = menu.addAction("Wake-on-LAN")
            wol_action.triggered.connect(lambda: self._send_wol(pc_name))
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

    def _on_columns_changed(self, value: int):
        """툴바 컬럼 스피너 변경 → 그리드 즉시 재구성"""
        settings.set('grid_view.columns', value)
        self.grid_view.rebuild_grid()

    def _show_pc_info(self, pc_name: str):
        """PC 정보 팝업 표시"""
        from ui.pc_info_dialog import PCInfoDialog
        pc = self.pc_manager.get_pc(pc_name)
        if pc:
            PCInfoDialog(pc, self.agent_server, self).exec()

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
            last_seen = pc.last_seen_str or "알 수 없음"
            mode = self.agent_server.get_connection_mode(pc.agent_id)
            reason = {
                "lan": "LAN 직접 연결이 끊겼습니다",
                "wan": "WAN 직접 연결이 끊겼습니다",
                "relay": "서버 릴레이 연결이 끊겼습니다",
            }.get(mode, "에이전트가 응답하지 않습니다")
            QMessageBox.warning(
                self, "오프라인",
                f"'{pc_name}'이(가) 오프라인 상태입니다.\n\n"
                f"상태: {reason}\n"
                f"마지막 접속: {last_seen}"
            )
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

    def _send_wol(self, pc_name: str):
        """Wake-on-LAN 매직 패킷 전송"""
        pc = self.pc_manager.get_pc(pc_name)
        if not pc or not getattr(pc.info, 'mac_address', ''):
            QMessageBox.warning(self, "WoL 실패", f"'{pc_name}'의 MAC 주소를 알 수 없습니다.")
            return
        from core.wol import send_wol
        success = send_wol(pc.info.mac_address)
        if success:
            self.status_label.setText(f"WoL 전송: {pc_name} ({pc.info.mac_address})")
        else:
            QMessageBox.warning(self, "WoL 실패", "매직 패킷 전송에 실패했습니다.")

    def _send_power_action(self, pc_name: str, action: str):
        """전원 관리 명령 전송"""
        labels = {'shutdown': '종료', 'restart': '재시작', 'logoff': '로그오프', 'sleep': '절전'}
        label = labels.get(action, action)
        reply = QMessageBox.question(
            self, "전원 관리",
            f"'{pc_name}'에 {label} 명령을 보내시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        pc = self.pc_manager.get_pc(pc_name)
        if pc and pc.agent_id:
            self.agent_server.send_power_action(pc.agent_id, action)
            self.status_label.setText(f"전원 명령: {label} → {pc_name}")

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
        from core.agent_server import ConnectionMode
        stats = self.pc_manager.get_statistics()
        self.agent_count_label.setText(f"에이전트: {stats['online']}/{stats['total']}")
        username = api_client.username or '미로그인'

        # 연결 모드별 카운트
        connections = self.agent_server._connections
        lan_count = sum(1 for c in connections.values() if c.mode == ConnectionMode.LAN)
        wan_count = sum(1 for c in connections.values() if c.mode == ConnectionMode.WAN)
        relay_count = sum(1 for c in connections.values() if c.mode == ConnectionMode.RELAY)
        total_conn = lan_count + wan_count + relay_count

        mode_parts = []
        if lan_count:
            mode_parts.append(f"LAN:{lan_count}")
        if wan_count:
            mode_parts.append(f"WAN:{wan_count}")
        if relay_count:
            mode_parts.append(f"릴레이:{relay_count}")
        mode_str = f"({', '.join(mode_parts)})" if mode_parts else ""

        self.status_label.setText(
            f"사용자: {username} | "
            f"연결: {total_conn}대 {mode_str}"
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

    # ==================== 시스템 트레이 ====================

    def _setup_tray(self):
        """시스템 트레이 아이콘 설정"""
        self._tray_icon = QSystemTrayIcon(self)
        self._really_quit = False  # True면 실제 종료, False면 트레이로 최소화

        if ICON_PATH:
            self._tray_icon.setIcon(QIcon(ICON_PATH))
        else:
            self._tray_icon.setIcon(self.style().standardIcon(
                self.style().StandardPixmap.SP_ComputerIcon
            ))

        # 트레이 메뉴
        tray_menu = QMenu()
        show_action = tray_menu.addAction("열기")
        show_action.triggered.connect(self._tray_show)
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("종료")
        quit_action.triggered.connect(self._tray_quit)
        self._tray_icon.setContextMenu(tray_menu)

        # 더블클릭 → 복원
        self._tray_icon.activated.connect(self._on_tray_activated)

        self._tray_icon.setToolTip(WINDOW_TITLE)
        self._tray_icon.show()

        # 에이전트 연결/해제 알림
        self.agent_server.agent_connected.connect(self._tray_notify_connected)
        self.agent_server.agent_disconnected.connect(self._tray_notify_disconnected)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._tray_show()

    def _tray_show(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _tray_quit(self):
        self._really_quit = True
        self.close()

    def _tray_notify_connected(self, agent_id: str, ip: str):
        if self._tray_icon.isVisible() and self.isHidden():
            pc = self.pc_manager.get_pc_by_agent_id(agent_id)
            name = pc.name if pc else agent_id
            self._tray_icon.showMessage(
                "에이전트 연결", f"{name} 접속됨 ({ip})",
                QSystemTrayIcon.MessageIcon.Information, 3000
            )

    def _tray_notify_disconnected(self, agent_id: str):
        if self._tray_icon.isVisible() and self.isHidden():
            pc = self.pc_manager.get_pc_by_agent_id(agent_id)
            name = pc.name if pc else agent_id
            self._tray_icon.showMessage(
                "에이전트 해제", f"{name} 연결 끊김",
                QSystemTrayIcon.MessageIcon.Warning, 3000
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

        # 트레이로 최소화 (실제 종료가 아닌 경우)
        if not self._really_quit and self._tray_icon.isVisible():
            event.ignore()
            self.hide()
            self._tray_icon.showMessage(
                WINDOW_TITLE, "트레이로 최소화되었습니다.",
                QSystemTrayIcon.MessageIcon.Information, 2000
            )
            return

        for dw in list(self._desktop_widgets.values()):
            dw.close()

        self._tray_icon.hide()
        event.accept()
