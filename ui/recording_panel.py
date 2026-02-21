"""녹화/재생 관리 다이얼로그

녹화 목록, 재생 설정, 녹화 시작/중지.
"""

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QSpinBox, QCheckBox, QGroupBox,
    QFormLayout, QMessageBox, QInputDialog,
)
from PyQt6.QtCore import Qt

from core.recorder import Recorder, Player, RecordingManager, Recording

logger = logging.getLogger(__name__)


class RecordingDialog(QDialog):
    """녹화/재생 관리 다이얼로그"""

    def __init__(self, recording_manager: RecordingManager,
                 recorder: Recorder, player: Player,
                 agent_id: str = "", parent=None):
        super().__init__(parent)
        self.manager = recording_manager
        self.recorder = recorder
        self.player = player
        self.agent_id = agent_id

        self.setWindowTitle("녹화/재생 관리")
        self.setMinimumSize(500, 450)
        self._init_ui()
        self._refresh_list()
        self._connect_signals()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 녹화 상태
        status_group = QGroupBox("녹화")
        status_layout = QHBoxLayout(status_group)

        self.status_label = QLabel("대기")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()

        self.btn_record = QPushButton("녹화 시작")
        self.btn_record.clicked.connect(self._toggle_recording)
        status_layout.addWidget(self.btn_record)

        layout.addWidget(status_group)

        # 녹화 목록
        layout.addWidget(QLabel("녹화 목록:"))
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        # 도구 버튼
        btn_layout = QHBoxLayout()

        self.btn_play = QPushButton("재생")
        self.btn_play.clicked.connect(self._play)
        btn_layout.addWidget(self.btn_play)

        self.btn_stop = QPushButton("재생 중지")
        self.btn_stop.clicked.connect(self._stop_playback)
        btn_layout.addWidget(self.btn_stop)

        self.btn_rename = QPushButton("이름 변경")
        self.btn_rename.clicked.connect(self._rename)
        btn_layout.addWidget(self.btn_rename)

        self.btn_delete = QPushButton("삭제")
        self.btn_delete.clicked.connect(self._delete)
        btn_layout.addWidget(self.btn_delete)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # 재생 설정
        play_group = QGroupBox("재생 설정")
        play_form = QFormLayout(play_group)

        self.spin_repeat = QSpinBox()
        self.spin_repeat.setRange(0, 9999)
        self.spin_repeat.setValue(1)
        self.spin_repeat.setSpecialValueText("무한")
        play_form.addRow("반복 횟수:", self.spin_repeat)

        self.chk_random_delay = QCheckBox("랜덤 딜레이 적용")
        play_form.addRow(self.chk_random_delay)

        layout.addWidget(play_group)

        # 닫기
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _connect_signals(self):
        self.recorder.recording_started.connect(self._on_recording_started)
        self.recorder.recording_stopped.connect(self._on_recording_stopped)
        self.recorder.event_recorded.connect(self._on_event_recorded)
        self.player.playback_started.connect(self._on_playback_started)
        self.player.playback_stopped.connect(self._on_playback_stopped)
        self.manager.recordings_changed.connect(self._refresh_list)

    def _refresh_list(self):
        self.list_widget.clear()
        for rec in self.manager.get_recordings():
            duration = f"{rec.duration:.1f}초" if rec.duration > 0 else "?"
            events = len(rec.events)
            item = QListWidgetItem(
                f"{rec.name}  —  {events}개 이벤트, {duration}"
            )
            item.setData(Qt.ItemDataRole.UserRole, rec.name)
            self.list_widget.addItem(item)

    def _toggle_recording(self):
        if self.recorder.is_recording:
            recording = self.recorder.stop()
            if recording:
                self.manager.add_recording(recording)
        else:
            name, ok = QInputDialog.getText(self, "녹화", "녹화 이름:")
            if ok and name.strip():
                self.recorder.start(name.strip())

    def _play(self):
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.information(self, "알림", "재생할 녹화를 선택하세요.")
            return

        if not self.agent_id:
            QMessageBox.warning(self, "오류", "대상 PC가 선택되지 않았습니다.")
            return

        name = item.data(Qt.ItemDataRole.UserRole)
        recording = self.manager.get_recording(name)
        if not recording:
            return

        repeat = self.spin_repeat.value()
        random_delay = self.chk_random_delay.isChecked()

        self.player.play(recording, self.agent_id, repeat, random_delay)

    def _stop_playback(self):
        self.player.stop(agent_id=self.agent_id)

    def _rename(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        old_name = item.data(Qt.ItemDataRole.UserRole)
        new_name, ok = QInputDialog.getText(
            self, "이름 변경", "새 이름:", text=old_name
        )
        if ok and new_name.strip() and new_name.strip() != old_name:
            self.manager.rename_recording(old_name, new_name.strip())

    def _delete(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, "삭제 확인", f"'{name}' 녹화를 삭제하시겠습니까?"
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.manager.delete_recording(name)

    # ==================== 시그널 핸들러 ====================

    def _on_recording_started(self, name: str):
        self.status_label.setText(f"녹화 중: {name}")
        self.status_label.setStyleSheet("color: #f44336; font-weight: bold;")
        self.btn_record.setText("녹화 중지")

    def _on_recording_stopped(self, name: str):
        self.status_label.setText("대기")
        self.status_label.setStyleSheet("")
        self.btn_record.setText("녹화 시작")

    def _on_event_recorded(self, count: int):
        self.status_label.setText(f"녹화 중: {count}개 이벤트")

    def _on_playback_started(self, name: str):
        self.btn_play.setEnabled(False)
        self.btn_play.setText(f"재생 중: {name}")

    def _on_playback_stopped(self, name: str):
        self.btn_play.setEnabled(True)
        self.btn_play.setText("재생")
