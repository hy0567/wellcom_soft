"""키매핑 에디터 다이얼로그

키매핑 프로파일 관리, 매핑 추가/편집/삭제.
"""

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QComboBox, QLineEdit, QGroupBox,
    QFormLayout, QMessageBox, QInputDialog, QHeaderView,
)
from PyQt6.QtCore import Qt

from core.key_mapper import KeyMapper, KeyMapping, KeymapProfile, KeyActionType

logger = logging.getLogger(__name__)


class MappingEditDialog(QDialog):
    """단일 매핑 편집 다이얼로그"""

    def __init__(self, mapping: Optional[KeyMapping] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("키매핑 편집" if mapping else "키매핑 추가")
        self.setMinimumWidth(400)
        self.result_mapping: Optional[KeyMapping] = None

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.trigger_edit = QLineEdit(mapping.trigger if mapping else "")
        self.trigger_edit.setPlaceholderText("예: F1, Ctrl+Shift+A")
        form.addRow("트리거 키:", self.trigger_edit)

        self.type_combo = QComboBox()
        for at in KeyActionType:
            self.type_combo.addItem(at.value, at)
        if mapping:
            idx = self.type_combo.findData(mapping.action_type)
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("동작 타입:", self.type_combo)

        self.data_edit = QLineEdit()
        self.data_edit.setPlaceholderText("동작 데이터 (JSON)")
        if mapping:
            import json
            self.data_edit.setText(json.dumps(mapping.action_data, ensure_ascii=False))
        form.addRow("동작 데이터:", self.data_edit)

        self.desc_edit = QLineEdit(mapping.description if mapping else "")
        form.addRow("설명:", self.desc_edit)

        layout.addLayout(form)

        # 안내
        self.hint_label = QLabel()
        self.hint_label.setStyleSheet("color: #888; font-size: 11px;")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)
        self._on_type_changed()

        # 버튼
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_ok = QPushButton("확인")
        btn_ok.clicked.connect(self._ok)
        btn_layout.addWidget(btn_ok)

        btn_cancel = QPushButton("취소")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        layout.addLayout(btn_layout)

    def _on_type_changed(self):
        action_type = self.type_combo.currentData()
        hints = {
            KeyActionType.KEY: '{"key": "enter", "modifiers": ["ctrl"]}',
            KeyActionType.CLICK: '{"x": 500, "y": 300}',
            KeyActionType.TEXT: '{"text": "Hello World"}',
            KeyActionType.COMMAND: '{"command": "ipconfig"}',
            KeyActionType.SCRIPT: '{"script_name": "my_script"}',
        }
        self.hint_label.setText(f"예시: {hints.get(action_type, '')}")

    def _ok(self):
        trigger = self.trigger_edit.text().strip()
        if not trigger:
            QMessageBox.warning(self, "오류", "트리거 키를 입력하세요.")
            return

        action_type = self.type_combo.currentData()
        data_text = self.data_edit.text().strip()

        try:
            import json
            action_data = json.loads(data_text) if data_text else {}
        except json.JSONDecodeError:
            QMessageBox.warning(self, "오류", "동작 데이터가 올바른 JSON이 아닙니다.")
            return

        self.result_mapping = KeyMapping(
            trigger=trigger,
            action_type=action_type,
            action_data=action_data,
            description=self.desc_edit.text().strip(),
        )
        self.accept()


class KeymapEditorDialog(QDialog):
    """키매핑 에디터 다이얼로그"""

    def __init__(self, key_mapper: KeyMapper, parent=None):
        super().__init__(parent)
        self.mapper = key_mapper
        self.setWindowTitle("키매핑 관리")
        self.setMinimumSize(700, 500)
        self._init_ui()
        self._refresh_profiles()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 프로파일 선택
        profile_layout = QHBoxLayout()
        profile_layout.addWidget(QLabel("프로파일:"))

        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(200)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        profile_layout.addWidget(self.profile_combo)

        self.btn_new_profile = QPushButton("새 프로파일")
        self.btn_new_profile.clicked.connect(self._new_profile)
        profile_layout.addWidget(self.btn_new_profile)

        self.btn_delete_profile = QPushButton("삭제")
        self.btn_delete_profile.clicked.connect(self._delete_profile)
        profile_layout.addWidget(self.btn_delete_profile)

        self.btn_activate = QPushButton("활성화")
        self.btn_activate.clicked.connect(self._activate_profile)
        profile_layout.addWidget(self.btn_activate)

        profile_layout.addStretch()
        layout.addLayout(profile_layout)

        # 매핑 테이블
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["트리거", "타입", "데이터", "설명"])
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        # 매핑 도구 버튼
        mapping_btn = QHBoxLayout()

        self.btn_add = QPushButton("매핑 추가")
        self.btn_add.clicked.connect(self._add_mapping)
        mapping_btn.addWidget(self.btn_add)

        self.btn_edit = QPushButton("매핑 편집")
        self.btn_edit.clicked.connect(self._edit_mapping)
        mapping_btn.addWidget(self.btn_edit)

        self.btn_remove = QPushButton("매핑 삭제")
        self.btn_remove.clicked.connect(self._remove_mapping)
        mapping_btn.addWidget(self.btn_remove)

        mapping_btn.addStretch()
        layout.addLayout(mapping_btn)

        # 닫기
        close_layout = QHBoxLayout()
        close_layout.addStretch()
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        close_layout.addWidget(close_btn)
        layout.addLayout(close_layout)

    def _refresh_profiles(self):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        active = self.mapper.get_active_profile()
        active_name = active.name if active else None

        for profile in self.mapper.get_profiles():
            label = profile.name
            if profile.name == active_name:
                label = f"{label}  (활성)"
            self.profile_combo.addItem(label, profile.name)

        self.profile_combo.blockSignals(False)
        self._on_profile_changed()

    def _on_profile_changed(self):
        profile_name = self.profile_combo.currentData()
        if not profile_name:
            self.table.setRowCount(0)
            return

        profile = self.mapper.get_profile(profile_name)
        if not profile:
            self.table.setRowCount(0)
            return

        self.table.setRowCount(len(profile.mappings))
        for i, m in enumerate(profile.mappings):
            self.table.setItem(i, 0, QTableWidgetItem(m.trigger))
            self.table.setItem(i, 1, QTableWidgetItem(m.action_type.value))
            import json
            self.table.setItem(i, 2, QTableWidgetItem(
                json.dumps(m.action_data, ensure_ascii=False)
            ))
            self.table.setItem(i, 3, QTableWidgetItem(m.description))

    def _new_profile(self):
        name, ok = QInputDialog.getText(self, "새 프로파일", "프로파일 이름:")
        if ok and name.strip():
            self.mapper.create_profile(name.strip())
            self._refresh_profiles()
            # 새 프로파일 선택
            idx = self.profile_combo.findData(name.strip())
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)

    def _delete_profile(self):
        name = self.profile_combo.currentData()
        if not name:
            return
        reply = QMessageBox.question(
            self, "삭제 확인", f"'{name}' 프로파일을 삭제하시겠습니까?"
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.mapper.delete_profile(name)
            self._refresh_profiles()

    def _activate_profile(self):
        name = self.profile_combo.currentData()
        if name:
            self.mapper.set_active_profile(name)
            self._refresh_profiles()

    def _add_mapping(self):
        profile_name = self.profile_combo.currentData()
        if not profile_name:
            QMessageBox.warning(self, "오류", "프로파일을 선택하세요.")
            return

        dlg = MappingEditDialog(parent=self)
        if dlg.exec() and dlg.result_mapping:
            self.mapper.add_mapping(profile_name, dlg.result_mapping)
            self._on_profile_changed()

    def _edit_mapping(self):
        profile_name = self.profile_combo.currentData()
        row = self.table.currentRow()
        if not profile_name or row < 0:
            return

        profile = self.mapper.get_profile(profile_name)
        if not profile or row >= len(profile.mappings):
            return

        mapping = profile.mappings[row]
        dlg = MappingEditDialog(mapping, self)
        if dlg.exec() and dlg.result_mapping:
            # 기존 매핑 교체
            self.mapper.remove_mapping(profile_name, mapping.trigger)
            self.mapper.add_mapping(profile_name, dlg.result_mapping)
            self._on_profile_changed()

    def _remove_mapping(self):
        profile_name = self.profile_combo.currentData()
        row = self.table.currentRow()
        if not profile_name or row < 0:
            return

        profile = self.mapper.get_profile(profile_name)
        if not profile or row >= len(profile.mappings):
            return

        trigger = profile.mappings[row].trigger
        self.mapper.remove_mapping(profile_name, trigger)
        self._on_profile_changed()
