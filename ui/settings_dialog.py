"""설정 다이얼로그

LinkIO Desktop의 MenuSettings + groups_config 기반.
화면, 멀컨, 그리드, 서버, 업데이트 설정을 통합 관리.
"""

import logging
from PyQt6.QtWidgets import (
    QDialog, QTabWidget, QWidget, QFormLayout, QVBoxLayout, QHBoxLayout,
    QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox, QLineEdit,
    QPushButton, QGroupBox, QLabel, QSlider,
)
from PyQt6.QtCore import Qt

from config import settings

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """설정 다이얼로그"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WellcomSOFT 설정")
        self.setMinimumSize(500, 480)

        layout = QVBoxLayout(self)

        # 탭 위젯
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # 각 탭 생성
        self.tabs.addTab(self._create_screen_tab(), "화면")
        self.tabs.addTab(self._create_multi_control_tab(), "멀티컨트롤")
        self.tabs.addTab(self._create_grid_tab(), "그리드 뷰")
        self.tabs.addTab(self._create_desktop_tab(), "뷰어")
        self.tabs.addTab(self._create_general_tab(), "일반")

        # 버튼
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_apply = QPushButton("적용")
        self.btn_apply.clicked.connect(self._apply)
        btn_layout.addWidget(self.btn_apply)

        self.btn_ok = QPushButton("확인")
        self.btn_ok.clicked.connect(self._ok)
        btn_layout.addWidget(self.btn_ok)

        self.btn_cancel = QPushButton("취소")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        layout.addLayout(btn_layout)

    def _create_screen_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 스트리밍
        stream_group = QGroupBox("스트리밍 설정")
        form = QFormLayout(stream_group)

        self.spin_stream_fps = QSpinBox()
        self.spin_stream_fps.setRange(1, 60)
        self.spin_stream_fps.setValue(settings.get('screen.stream_fps', 15))
        self.spin_stream_fps.setSuffix(" FPS")
        form.addRow("스트리밍 FPS:", self.spin_stream_fps)

        self.spin_stream_quality = QSpinBox()
        self.spin_stream_quality.setRange(10, 100)
        self.spin_stream_quality.setValue(settings.get('screen.stream_quality', 60))
        self.spin_stream_quality.setSuffix(" %")
        form.addRow("스트리밍 화질:", self.spin_stream_quality)

        layout.addWidget(stream_group)

        # 썸네일
        thumb_group = QGroupBox("썸네일 설정")
        form2 = QFormLayout(thumb_group)

        self.spin_thumb_interval = QSpinBox()
        self.spin_thumb_interval.setRange(200, 10000)
        self.spin_thumb_interval.setValue(settings.get('screen.thumbnail_interval', 1000))
        self.spin_thumb_interval.setSuffix(" ms")
        self.spin_thumb_interval.setSingleStep(100)
        form2.addRow("갱신 간격:", self.spin_thumb_interval)

        self.spin_thumb_quality = QSpinBox()
        self.spin_thumb_quality.setRange(10, 100)
        self.spin_thumb_quality.setValue(settings.get('screen.thumbnail_quality', 40))
        self.spin_thumb_quality.setSuffix(" %")
        form2.addRow("썸네일 화질:", self.spin_thumb_quality)

        self.spin_thumb_width = QSpinBox()
        self.spin_thumb_width.setRange(160, 1920)
        self.spin_thumb_width.setValue(settings.get('screen.thumbnail_width', 320))
        self.spin_thumb_width.setSuffix(" px")
        self.spin_thumb_width.setSingleStep(40)
        form2.addRow("썸네일 너비:", self.spin_thumb_width)

        layout.addWidget(thumb_group)
        layout.addStretch()
        return widget

    def _create_multi_control_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 랜덤 좌표
        pos_group = QGroupBox("랜덤 좌표 오프셋")
        form = QFormLayout(pos_group)

        self.spin_rand_x = QSpinBox()
        self.spin_rand_x.setRange(0, 50)
        self.spin_rand_x.setValue(settings.get('multi_control.random_pos_x', 3))
        self.spin_rand_x.setSuffix(" px")
        form.addRow("X 오프셋:", self.spin_rand_x)

        self.spin_rand_y = QSpinBox()
        self.spin_rand_y.setRange(0, 50)
        self.spin_rand_y.setValue(settings.get('multi_control.random_pos_y', 3))
        self.spin_rand_y.setSuffix(" px")
        form.addRow("Y 오프셋:", self.spin_rand_y)

        layout.addWidget(pos_group)

        # 랜덤 딜레이
        delay_group = QGroupBox("랜덤 딜레이")
        form2 = QFormLayout(delay_group)

        self.spin_delay_min = QSpinBox()
        self.spin_delay_min.setRange(0, 10000)
        self.spin_delay_min.setValue(settings.get('multi_control.random_delay_min', 300))
        self.spin_delay_min.setSuffix(" ms")
        self.spin_delay_min.setSingleStep(50)
        form2.addRow("최소 딜레이:", self.spin_delay_min)

        self.spin_delay_max = QSpinBox()
        self.spin_delay_max.setRange(0, 30000)
        self.spin_delay_max.setValue(settings.get('multi_control.random_delay_max', 2000))
        self.spin_delay_max.setSuffix(" ms")
        self.spin_delay_max.setSingleStep(100)
        form2.addRow("최대 딜레이:", self.spin_delay_max)

        layout.addWidget(delay_group)

        # 입력 간격
        input_group = QGroupBox("입력 설정")
        form3 = QFormLayout(input_group)

        self.spin_input_delay = QDoubleSpinBox()
        self.spin_input_delay.setRange(0.001, 1.0)
        self.spin_input_delay.setValue(settings.get('multi_control.input_delay', 0.01))
        self.spin_input_delay.setSuffix(" 초")
        self.spin_input_delay.setSingleStep(0.01)
        self.spin_input_delay.setDecimals(3)
        form3.addRow("기본 입력 간격:", self.spin_input_delay)

        layout.addWidget(input_group)
        layout.addStretch()
        return widget

    def _create_grid_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 레이아웃 설정
        layout_group = QGroupBox("레이아웃")
        form = QFormLayout(layout_group)

        self.spin_columns = QSpinBox()
        self.spin_columns.setRange(1, 20)
        self.spin_columns.setValue(settings.get('grid_view.columns', 5))
        form.addRow("컬럼 수:", self.spin_columns)

        self.combo_aspect = QComboBox()
        self.combo_aspect.addItems(['16:9', '16:10', '4:3', '3:2', '1:1'])
        current_ratio = settings.get('grid_view.aspect_ratio', '16:9')
        idx = self.combo_aspect.findText(current_ratio)
        if idx >= 0:
            self.combo_aspect.setCurrentIndex(idx)
        form.addRow("썸네일 비율:", self.combo_aspect)

        self.spin_scale = QSpinBox()
        self.spin_scale.setRange(50, 200)
        self.spin_scale.setValue(settings.get('grid_view.scale_factor', 100))
        self.spin_scale.setSuffix(" %")
        form.addRow("축척:", self.spin_scale)

        layout.addWidget(layout_group)

        # 표시 설정
        display_group = QGroupBox("표시")
        form2 = QFormLayout(display_group)

        self.spin_grid_fps = QSpinBox()
        self.spin_grid_fps.setRange(1, 30)
        self.spin_grid_fps.setValue(settings.get('grid_view.frame_speed', 5))
        self.spin_grid_fps.setSuffix(" FPS")
        form2.addRow("그리드 FPS:", self.spin_grid_fps)

        self.spin_font_size = QSpinBox()
        self.spin_font_size.setRange(5, 16)
        self.spin_font_size.setValue(settings.get('grid_view.font_size', 9))
        self.spin_font_size.setSuffix(" pt")
        form2.addRow("폰트 크기:", self.spin_font_size)

        self.chk_show_name = QCheckBox("PC 이름 표시")
        self.chk_show_name.setChecked(settings.get('grid_view.show_name', True))
        form2.addRow(self.chk_show_name)

        self.chk_show_memo = QCheckBox("메모 표시")
        self.chk_show_memo.setChecked(settings.get('grid_view.show_memo', True))
        form2.addRow(self.chk_show_memo)

        self.chk_show_title = QCheckBox("타이틀 표시")
        self.chk_show_title.setChecked(settings.get('grid_view.show_title', True))
        form2.addRow(self.chk_show_title)

        layout.addWidget(display_group)
        layout.addStretch()
        return widget

    def _create_desktop_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        group = QGroupBox("뷰어 설정")
        form = QFormLayout(group)

        self.chk_side_menu = QCheckBox("사이드 메뉴바 표시")
        self.chk_side_menu.setChecked(settings.get('desktop_widget.side_menu', True))
        form.addRow(self.chk_side_menu)

        self.chk_title_bar = QCheckBox("타이틀바 표시")
        self.chk_title_bar.setChecked(settings.get('desktop_widget.title_bar', True))
        form.addRow(self.chk_title_bar)

        self.chk_sound_mute = QCheckBox("사운드 음소거")
        self.chk_sound_mute.setChecked(settings.get('desktop_widget.sound_mute', True))
        form.addRow(self.chk_sound_mute)

        layout.addWidget(group)
        layout.addStretch()
        return widget

    def _create_general_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 테마
        theme_group = QGroupBox("외관")
        form = QFormLayout(theme_group)

        self.combo_theme = QComboBox()
        self.combo_theme.addItem("다크", "dark")
        self.combo_theme.addItem("라이트", "light")
        current_theme = settings.get('general.theme', 'dark')
        idx = self.combo_theme.findData(current_theme)
        if idx >= 0:
            self.combo_theme.setCurrentIndex(idx)
        form.addRow("테마:", self.combo_theme)

        layout.addWidget(theme_group)

        # 서버
        server_group = QGroupBox("서버")
        form2 = QFormLayout(server_group)

        self.edit_api_url = QLineEdit(settings.get('server.api_url', ''))
        form2.addRow("API URL:", self.edit_api_url)

        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(settings.get('agent_server.port', 9877))
        form2.addRow("에이전트 포트:", self.spin_port)

        layout.addWidget(server_group)

        # 업데이트
        update_group = QGroupBox("업데이트")
        form3 = QFormLayout(update_group)

        self.chk_auto_update = QCheckBox("시작 시 업데이트 확인")
        self.chk_auto_update.setChecked(settings.get('update.auto_check', True))
        form3.addRow(self.chk_auto_update)

        layout.addWidget(update_group)
        layout.addStretch()
        return widget

    def _apply(self):
        """설정 적용"""
        # 화면
        settings.set('screen.stream_fps', self.spin_stream_fps.value(), auto_save=False)
        settings.set('screen.stream_quality', self.spin_stream_quality.value(), auto_save=False)
        settings.set('screen.thumbnail_interval', self.spin_thumb_interval.value(), auto_save=False)
        settings.set('screen.thumbnail_quality', self.spin_thumb_quality.value(), auto_save=False)
        settings.set('screen.thumbnail_width', self.spin_thumb_width.value(), auto_save=False)

        # 멀컨
        settings.set('multi_control.random_pos_x', self.spin_rand_x.value(), auto_save=False)
        settings.set('multi_control.random_pos_y', self.spin_rand_y.value(), auto_save=False)
        settings.set('multi_control.random_delay_min', self.spin_delay_min.value(), auto_save=False)
        settings.set('multi_control.random_delay_max', self.spin_delay_max.value(), auto_save=False)
        settings.set('multi_control.input_delay', self.spin_input_delay.value(), auto_save=False)

        # 그리드
        settings.set('grid_view.columns', self.spin_columns.value(), auto_save=False)
        settings.set('grid_view.aspect_ratio', self.combo_aspect.currentText(), auto_save=False)
        settings.set('grid_view.scale_factor', self.spin_scale.value(), auto_save=False)
        settings.set('grid_view.frame_speed', self.spin_grid_fps.value(), auto_save=False)
        settings.set('grid_view.font_size', self.spin_font_size.value(), auto_save=False)
        settings.set('grid_view.show_name', self.chk_show_name.isChecked(), auto_save=False)
        settings.set('grid_view.show_memo', self.chk_show_memo.isChecked(), auto_save=False)
        settings.set('grid_view.show_title', self.chk_show_title.isChecked(), auto_save=False)

        # 뷰어
        settings.set('desktop_widget.side_menu', self.chk_side_menu.isChecked(), auto_save=False)
        settings.set('desktop_widget.title_bar', self.chk_title_bar.isChecked(), auto_save=False)
        settings.set('desktop_widget.sound_mute', self.chk_sound_mute.isChecked(), auto_save=False)

        # 일반
        settings.set('general.theme', self.combo_theme.currentData(), auto_save=False)
        settings.set('server.api_url', self.edit_api_url.text().strip(), auto_save=False)
        settings.set('agent_server.port', self.spin_port.value(), auto_save=False)
        settings.set('update.auto_check', self.chk_auto_update.isChecked(), auto_save=False)

        settings.save()

    def _ok(self):
        self._apply()
        self.accept()
