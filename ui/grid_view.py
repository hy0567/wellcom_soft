"""다중 PC 썸네일 그리드 뷰 (CCTV 모드)

LinkIO Desktop 스타일 — 설정 가능한 컬럼/비율/폰트,
연결 상태 색상, 메모 표시, 검색 필터, 자동 정렬.
라이트/다크 테마 자동 감지.
"""

import logging
from typing import Dict

from PyQt6.QtWidgets import (
    QScrollArea, QWidget, QGridLayout, QLabel, QVBoxLayout,
    QFrame, QSizePolicy, QHBoxLayout, QPushButton,
    QGraphicsDropShadowEffect, QLineEdit,
    QCheckBox, QSpinBox, QComboBox,
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

# ── 상태 색상 (테마 공통) ─────────────────────────────────────
_COLOR_ONLINE = '#22c55e'
_COLOR_ERROR = '#ef4444'
_COLOR_OFFLINE = '#9ca3af'
_COLOR_SELECTED = '#3b82f6'

ASPECT_RATIOS = {
    '16:9': (16, 9),
    '16:10': (16, 10),
    '4:3': (4, 3),
    '3:2': (3, 2),
    '1:1': (1, 1),
}


def _theme():
    """현재 테마에 맞는 색상 반환"""
    theme = settings.get('general.theme', 'light')
    if theme == 'dark':
        return {
            'card_bg': '#252526', 'card_hover': '#2d2d30',
            'img_bg': '#1a1a1c', 'grid_bg': '#1a1a1a',
            'bar_bg': '#252525', 'bar_border': '#333',
            'text': '#e0e0e0', 'text2': '#aaa', 'text3': '#777',
            'border': '#3e3e3e', 'border_light': '#333',
            'input_bg': '#2d2d30', 'input_border': '#3e3e3e',
            'input_focus': '#007acc', 'shadow_alpha': 40,
            'placeholder_bg': '#1e1e1e', 'placeholder_border': '#383838',
        }
    return {
        'card_bg': '#ffffff', 'card_hover': '#f8fafc',
        'img_bg': '#f1f5f9', 'grid_bg': '#f1f5f9',
        'bar_bg': '#ffffff', 'bar_border': '#e2e8f0',
        'text': '#1e293b', 'text2': '#64748b', 'text3': '#94a3b8',
        'border': '#e2e8f0', 'border_light': '#f1f5f9',
        'input_bg': '#f8fafc', 'input_border': '#cbd5e1',
        'input_focus': '#3b82f6', 'shadow_alpha': 25,
        'placeholder_bg': '#f8fafc', 'placeholder_border': '#e2e8f0',
    }


class PCThumbnailWidget(QFrame):
    """단일 PC 썸네일 위젯 — 동적 크기 + 테마 지원"""

    double_clicked = pyqtSignal(str)
    right_clicked = pyqtSignal(str, object)
    selected = pyqtSignal(str, bool)
    update_requested = pyqtSignal(str)

    def __init__(self, pc_name: str, memo: str = '', parent=None, *,
                 show_name=True, show_memo=True, font_size=9):
        super().__init__(parent)
        self.pc_name = pc_name
        self._is_online = False
        self._is_selected = False
        self._status = PCStatus.OFFLINE
        self._hover = False
        self._font_size = font_size
        self._show_name = show_name
        self._show_memo = show_memo
        self._colors = _theme()

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # 드롭 섀도우
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(12)
        self._shadow.setOffset(0, 2)
        self._shadow.setColor(QColor(0, 0, 0, self._colors['shadow_alpha']))
        self.setGraphicsEffect(self._shadow)

        c = self._colors
        fs = font_size
        fs_small = max(6, fs - 2)
        fs_tiny = max(6, fs - 3)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 3)
        layout.setSpacing(2)

        # ── 썸네일 이미지 ──
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet(
            f"background-color: {c['img_bg']}; border-radius: 4px;"
            f" color: {c['text3']};"
        )
        self.image_label.setText("대기")
        self.image_label.setFont(QFont("", fs_small))
        layout.addWidget(self.image_label, 1)

        # ── Row 1: 상태 점 + PC이름 + 모드/레이턴시 배지 ──
        show_info = show_name or show_memo
        self._row1 = QWidget()
        self._row1.setStyleSheet("background: transparent;")
        row1_layout = QHBoxLayout(self._row1)
        row1_layout.setContentsMargins(2, 1, 2, 0)
        row1_layout.setSpacing(3)

        dot_size = max(6, min(10, fs))
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(dot_size, dot_size)
        self.status_dot.setToolTip("연결 상태: 오프라인")
        self.status_dot.setStyleSheet(
            f"background-color: {_COLOR_OFFLINE}; border-radius: {dot_size // 2}px;"
        )
        row1_layout.addWidget(self.status_dot)

        self.name_label = QLabel(pc_name)
        self.name_label.setFont(QFont("Segoe UI", fs, QFont.Weight.DemiBold))
        self.name_label.setStyleSheet(f"color: {c['text']}; background: transparent;")
        self.name_label.setVisible(show_name)
        row1_layout.addWidget(self.name_label)

        row1_layout.addStretch()

        self.mode_label = QLabel()
        self.mode_label.setFont(QFont("", fs_tiny, QFont.Weight.Bold))
        self.mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mode_label.setFixedHeight(max(14, fs + 4))
        self.mode_label.setToolTip("연결 방식")
        self.mode_label.setVisible(False)
        row1_layout.addWidget(self.mode_label)

        self.latency_label = QLabel()
        self.latency_label.setFont(QFont("", fs_tiny, QFont.Weight.Bold))
        self.latency_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.latency_label.setFixedHeight(max(14, fs + 4))
        self.latency_label.setToolTip("네트워크 지연시간")
        self.latency_label.setVisible(False)
        row1_layout.addWidget(self.latency_label)

        self._row1.setVisible(show_info)
        if show_info:
            self._row1.setFixedHeight(max(16, fs + 6))
        layout.addWidget(self._row1)

        # ── Row 2: 버전 + 업데이트 + 메모 ──
        self._row2 = QWidget()
        self._row2.setStyleSheet("background: transparent;")
        row2_layout = QHBoxLayout(self._row2)
        row2_layout.setContentsMargins(dot_size + 5, 0, 2, 0)
        row2_layout.setSpacing(3)

        self.version_label = QLabel()
        self.version_label.setFont(QFont("", fs_small))
        self.version_label.setStyleSheet(f"color: {c['text3']}; background: transparent;")
        self.version_label.setToolTip("에이전트 버전")
        row2_layout.addWidget(self.version_label)

        self.update_btn = QPushButton("업데이트")
        self.update_btn.setFixedHeight(max(14, fs + 4))
        self.update_btn.setFont(QFont("Segoe UI", max(6, fs_tiny)))
        self.update_btn.setStyleSheet(
            "QPushButton { background-color: #f97316; color: white; border: none;"
            " border-radius: 3px; padding: 0 4px; }"
            "QPushButton:hover { background-color: #ea580c; }"
        )
        self.update_btn.setVisible(False)
        self.update_btn.setToolTip("클릭하여 에이전트를 최신 버전으로 업데이트")
        self.update_btn.clicked.connect(lambda: self.update_requested.emit(self.pc_name))
        row2_layout.addWidget(self.update_btn)

        row2_layout.addStretch()

        self.memo_label = QLabel(memo)
        self.memo_label.setFont(QFont("", fs_small))
        self.memo_label.setStyleSheet(f"color: {c['text3']}; background: transparent;")
        self.memo_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.memo_label.setVisible(show_memo and bool(memo))
        row2_layout.addWidget(self.memo_label)

        self._row2.setVisible(show_info)
        if show_info:
            self._row2.setFixedHeight(max(14, fs + 4))
        layout.addWidget(self._row2)

        # ── Row 3: CPU/RAM 미니 바 ──
        self._row3 = QWidget()
        self._row3.setStyleSheet("background: transparent;")
        self._row3.setFixedHeight(max(10, fs))
        row3_layout = QHBoxLayout(self._row3)
        row3_layout.setContentsMargins(2, 0, 2, 0)
        row3_layout.setSpacing(3)

        self._cpu_bar = QLabel()
        self._cpu_bar.setFixedHeight(3)
        self._cpu_bar.setToolTip("CPU 사용률")
        self._cpu_bar.setStyleSheet(
            f"background-color: {c['border']}; border-radius: 1px;"
        )
        row3_layout.addWidget(self._cpu_bar)

        self._ram_bar = QLabel()
        self._ram_bar.setFixedHeight(3)
        self._ram_bar.setToolTip("RAM 사용률")
        self._ram_bar.setStyleSheet(
            f"background-color: {c['border']}; border-radius: 1px;"
        )
        row3_layout.addWidget(self._ram_bar)

        self._perf_label = QLabel()
        self._perf_label.setFont(QFont("", max(6, fs_tiny)))
        self._perf_label.setStyleSheet(f"color: {c['text3']}; background: transparent;")
        self._perf_label.setToolTip("CPU / RAM 사용률")
        self._perf_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        row3_layout.addWidget(self._perf_label)

        self._row3.setVisible(show_info)
        layout.addWidget(self._row3)

        # 카드 툴팁
        tip_lines = [f"PC: {pc_name}"]
        if memo:
            tip_lines.append(f"메모: {memo}")
        tip_lines.append("더블클릭: 원격 제어 | Ctrl+클릭: 선택 | 우클릭: 메뉴")
        self.setToolTip('\n'.join(tip_lines))

        self._update_style()

    # ── 썸네일 갱신 ──

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

    # ── 상태 ──

    def set_status(self, status: PCStatus):
        self._status = status
        self._is_online = (status == PCStatus.ONLINE)
        self._update_style()

        status_tips = {
            PCStatus.ONLINE: "연결 상태: 온라인",
            PCStatus.ERROR: "연결 상태: 오류 발생",
            PCStatus.OFFLINE: "연결 상태: 오프라인",
        }
        self.status_dot.setToolTip(status_tips.get(status, "연결 상태: 알 수 없음"))

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
        if self._show_memo:
            self.memo_label.setVisible(bool(memo))

    # ── 버전 ──

    def update_version(self, agent_version: str, manager_version: str = ''):
        if not agent_version:
            self.version_label.setText('')
            self.version_label.setToolTip("에이전트 버전 정보 없음")
            self.update_btn.setVisible(False)
            return

        if agent_version == '0.0.0':
            display = 'v?'
            self.version_label.setToolTip("에이전트 버전을 확인하는 중...")
        else:
            display = f'v{agent_version}'
            self.version_label.setToolTip(f"에이전트 버전: {agent_version}")
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
                "color: #ef4444; font-weight: bold; background: transparent;"
            )
            self.version_label.setToolTip(
                f"에이전트 v{agent_version} (최신: v{manager_version}) - 업데이트 필요"
            )
        elif is_latest:
            self.version_label.setStyleSheet("color: #22c55e; background: transparent;")
        else:
            self.version_label.setStyleSheet("color: #888; background: transparent;")
        self.update_btn.setVisible(needs_update)

    def set_update_status(self, status: str, **kwargs):
        fs_tiny = max(6, self._font_size - 3)
        base_style = f"font-size: {max(7, fs_tiny + 1)}px;"
        if status == 'checking':
            self.update_btn.setText("확인중")
            self.update_btn.setToolTip("업데이트 확인 중...")
            self.update_btn.setEnabled(False)
        elif status == 'downloading':
            pct = kwargs.get('progress', 0)
            self.update_btn.setText(f"{pct}%")
            self.update_btn.setToolTip(f"다운로드 중... {pct}%")
            self.update_btn.setEnabled(False)
        elif status == 'restarting':
            self.update_btn.setText("재시작")
            self.update_btn.setToolTip("에이전트 재시작 중...")
            self.update_btn.setEnabled(False)
        elif status == 'up_to_date':
            self.update_btn.setText("최신")
            self.update_btn.setToolTip("최신 버전입니다")
            self.update_btn.setEnabled(False)
            self.update_btn.setStyleSheet(
                f"QPushButton {{ background-color: #22c55e; color: white; border: none;"
                f" border-radius: 3px; padding: 0 4px; {base_style} }}"
            )
        elif status == 'failed':
            self.update_btn.setText("실패")
            self.update_btn.setToolTip("업데이트 실패 - 클릭하여 재시도")
            self.update_btn.setEnabled(True)
            self.update_btn.setStyleSheet(
                f"QPushButton {{ background-color: #ef4444; color: white; border: none;"
                f" border-radius: 3px; padding: 0 4px; {base_style} }}"
                f"QPushButton:hover {{ background-color: #dc2626; }}"
            )
        else:
            self.update_btn.setText("업데이트")
            self.update_btn.setToolTip("클릭하여 에이전트를 최신 버전으로 업데이트")
            self.update_btn.setEnabled(True)
            self.update_btn.setStyleSheet(
                f"QPushButton {{ background-color: #f97316; color: white; border: none;"
                f" border-radius: 3px; padding: 0 4px; {base_style} }}"
                f"QPushButton:hover {{ background-color: #ea580c; }}"
            )

    # ── 연결 모드 배지 ──

    def update_mode(self, mode: str):
        MODE_STYLES = {
            'lan':     ('LAN',  '#22c55e', '#fff', '같은 네트워크 (LAN 직접 연결)'),
            'udp_p2p': ('P2P',  '#a855f7', '#fff', 'UDP P2P 직접 연결 (NAT 통과)'),
            'wan':     ('WAN',  '#3b82f6', '#fff', '외부 네트워크 (WAN 연결)'),
            'relay':   ('릴레이', '#f97316', '#fff', '릴레이 서버 경유 연결'),
        }
        if mode in MODE_STYLES:
            text, bg, fg, tip = MODE_STYLES[mode]
            self.mode_label.setText(text)
            self.mode_label.setToolTip(tip)
            self.mode_label.setStyleSheet(
                f"QLabel {{ background-color: {bg}; color: {fg};"
                f" border-radius: 3px; padding: 1px 4px; }}"
            )
            self.mode_label.setVisible(True)
        else:
            self.mode_label.setVisible(False)

    # ── 성능 모니터 ──

    def update_performance(self, cpu: float, ram: float):
        def _bar_color(val):
            if val <= 60:
                return '#22c55e'
            elif val <= 85:
                return '#f59e0b'
            return '#ef4444'

        self._cpu_bar.setToolTip(f"CPU 사용률: {cpu:.0f}%")
        self._cpu_bar.setStyleSheet(
            f"background-color: {_bar_color(cpu)}; border-radius: 1px;"
            f" max-width: {max(2, int(cpu))}px;"
        )
        self._ram_bar.setToolTip(f"RAM 사용률: {ram:.0f}%")
        self._ram_bar.setStyleSheet(
            f"background-color: {_bar_color(ram)}; border-radius: 1px;"
            f" max-width: {max(2, int(ram))}px;"
        )
        self._perf_label.setText(f"C:{cpu:.0f}% R:{ram:.0f}%")
        self._perf_label.setToolTip(f"CPU: {cpu:.1f}% | RAM: {ram:.1f}%")

    # ── 핑 레이턴시 ──

    def update_latency(self, ms: int):
        if ms <= 50:
            bg = '#22c55e'
            quality = '양호'
        elif ms <= 150:
            bg = '#f59e0b'
            quality = '보통'
        else:
            bg = '#ef4444'
            quality = '느림'
        self.latency_label.setText(f"{ms}ms")
        self.latency_label.setToolTip(f"네트워크 지연: {ms}ms ({quality})")
        self.latency_label.setStyleSheet(
            f"QLabel {{ background-color: {bg}; color: #fff;"
            f" border-radius: 3px; padding: 1px 4px; }}"
        )
        self.latency_label.setVisible(True)

    # ── 스타일 갱신 ──

    def _update_style(self):
        c = self._colors
        fs = self._font_size
        dot_size = max(6, min(10, fs))
        dot_r = dot_size // 2

        if self._status == PCStatus.ONLINE:
            dot_color = _COLOR_ONLINE
        elif self._status == PCStatus.ERROR:
            dot_color = _COLOR_ERROR
        else:
            dot_color = _COLOR_OFFLINE
        self.status_dot.setStyleSheet(
            f"background-color: {dot_color}; border-radius: {dot_r}px;"
        )

        if self._is_selected:
            border_color = _COLOR_SELECTED
            bw = 2
        elif self._status == PCStatus.ONLINE:
            border_color = _COLOR_ONLINE
            bw = 1
        elif self._status == PCStatus.ERROR:
            border_color = _COLOR_ERROR
            bw = 1
        else:
            border_color = c['border']
            bw = 1

        bg = c['card_hover'] if self._hover else c['card_bg']

        self.setStyleSheet(f"""
            PCThumbnailWidget {{
                background-color: {bg};
                border: {bw}px solid {border_color};
                border-radius: 6px;
            }}
        """)

        if self._hover:
            self._shadow.setBlurRadius(16)
            self._shadow.setOffset(0, 3)
            self._shadow.setColor(QColor(0, 0, 0, c['shadow_alpha'] + 15))
        else:
            self._shadow.setBlurRadius(12)
            self._shadow.setOffset(0, 2)
            self._shadow.setColor(QColor(0, 0, 0, c['shadow_alpha']))

    # ── 마우스 이벤트 ──

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
    """빈 슬롯 위젯"""

    def __init__(self, parent=None):
        super().__init__(parent)
        c = _theme()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(f"""
            PlaceholderSlotWidget {{
                background-color: {c['placeholder_bg']};
                border: 1px dashed {c['placeholder_border']};
                border-radius: 6px;
            }}
        """)


class GridView(QWidget):
    """다중 PC 썸네일 그리드 (LinkIO 스타일) — 설정 바 + 테마 지원"""

    open_viewer = pyqtSignal(str)
    context_menu_requested = pyqtSignal(str, object)
    selection_changed = pyqtSignal(list)

    def __init__(self, pc_manager: PCManager, agent_server: AgentServer):
        super().__init__()
        self.pc_manager = pc_manager
        self.agent_server = agent_server
        self._thumbnails: Dict[str, PCThumbnailWidget] = {}
        self._placeholders: list = []
        self._selected_pcs: set = set()
        self._push_agents: set = set()

        self._setup_ui()
        self._connect_signals()

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

        # 리사이즈 디바운스
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._on_resize_done)

    def _setup_ui(self):
        c = _theme()
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── 설정 바 ──
        self._settings_bar = self._create_settings_bar(c)
        main_layout.addWidget(self._settings_bar)

        # ── 스크롤 영역 ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {c['grid_bg']}; border: none; }}"
        )

        self._container = QWidget()
        self._container.setStyleSheet(f"background-color: {c['grid_bg']};")
        self._grid = QGridLayout(self._container)
        self._grid.setSpacing(8)
        self._grid.setContentsMargins(8, 8, 8, 8)
        self._scroll.setWidget(self._container)

        main_layout.addWidget(self._scroll)

    def _create_settings_bar(self, c):
        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet(f"""
            QWidget {{ background-color: {c['bar_bg']}; }}
            QWidget {{ border-bottom: 1px solid {c['bar_border']}; }}
            QLabel {{ color: {c['text2']}; font-size: 11px; background: transparent;
                      border: none; }}
            QCheckBox {{ color: {c['text']}; font-size: 11px; spacing: 4px; border: none; }}
            QCheckBox::indicator {{ width: 15px; height: 15px; }}
            QSpinBox {{ background-color: {c['input_bg']}; color: {c['text']};
                        border: 1px solid {c['input_border']}; border-radius: 4px;
                        padding: 2px 4px; font-size: 11px; min-width: 42px; }}
            QSpinBox:focus {{ border: 1px solid {c['input_focus']}; }}
            QComboBox {{ background-color: {c['input_bg']}; color: {c['text']};
                         border: 1px solid {c['input_border']}; border-radius: 4px;
                         padding: 2px 6px; font-size: 11px; }}
            QComboBox:focus {{ border: 1px solid {c['input_focus']}; }}
            QLineEdit {{ background-color: {c['input_bg']}; color: {c['text']};
                         border: 1px solid {c['input_border']}; border-radius: 4px;
                         padding: 3px 8px; font-size: 11px; }}
            QLineEdit:focus {{ border: 1px solid {c['input_focus']}; }}
        """)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        # 검색
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("PC 검색...")
        self._search_input.setFixedWidth(130)
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_filter_changed)
        layout.addWidget(self._search_input)

        self._add_separator(layout, c)

        # 이름표시
        self._chk_name = QCheckBox("이름표시")
        self._chk_name.setChecked(settings.get('grid_view.show_name', True))
        self._chk_name.stateChanged.connect(self._on_display_changed)
        layout.addWidget(self._chk_name)

        # 메모표시
        self._chk_memo = QCheckBox("메모표시")
        self._chk_memo.setChecked(settings.get('grid_view.show_memo', True))
        self._chk_memo.stateChanged.connect(self._on_display_changed)
        layout.addWidget(self._chk_memo)

        self._add_separator(layout, c)

        # 폰트 사이즈
        layout.addWidget(QLabel("폰트"))
        self._spin_font = QSpinBox()
        self._spin_font.setRange(5, 16)
        self._spin_font.setValue(settings.get('grid_view.font_size', 9))
        self._spin_font.setFixedWidth(52)
        self._spin_font.valueChanged.connect(self._on_display_changed)
        layout.addWidget(self._spin_font)

        self._add_separator(layout, c)

        # 열
        layout.addWidget(QLabel("열"))
        self._spin_cols = QSpinBox()
        self._spin_cols.setRange(1, 20)
        self._spin_cols.setValue(settings.get('grid_view.columns', 5))
        self._spin_cols.setFixedWidth(52)
        self._spin_cols.valueChanged.connect(self._on_columns_changed)
        layout.addWidget(self._spin_cols)

        # 비율
        layout.addWidget(QLabel("비율"))
        self._combo_ratio = QComboBox()
        self._combo_ratio.addItems(['16:9', '16:10', '4:3', '3:2', '1:1'])
        current = settings.get('grid_view.aspect_ratio', '16:9')
        idx = self._combo_ratio.findText(current)
        if idx >= 0:
            self._combo_ratio.setCurrentIndex(idx)
        self._combo_ratio.setFixedWidth(74)
        self._combo_ratio.currentTextChanged.connect(self._on_ratio_changed)
        layout.addWidget(self._combo_ratio)

        layout.addStretch()
        return bar

    def _add_separator(self, layout, c):
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"QFrame {{ color: {c['bar_border']}; border: none; }}")
        sep.setFixedHeight(20)
        layout.addWidget(sep)

    def _connect_signals(self):
        self.pc_manager.signals.devices_reloaded.connect(self.rebuild_grid)
        self.pc_manager.signals.device_added.connect(lambda _: self.rebuild_grid())
        self.pc_manager.signals.device_removed.connect(lambda _: self.rebuild_grid())
        self.pc_manager.signals.device_status_changed.connect(self._on_status_changed)
        self.agent_server.thumbnail_received.connect(self._on_thumbnail_received)
        self.agent_server.agent_connected.connect(self._on_agent_connected)
        self.agent_server.agent_disconnected.connect(self._on_agent_disconnected)
        self.agent_server.connection_mode_changed.connect(self._on_connection_mode_changed)
        self.agent_server.update_status_received.connect(self._on_update_status)
        self.agent_server.latency_measured.connect(self._on_latency_measured)
        self.agent_server.performance_received.connect(self._on_performance_received)

    # ==================== 그리드 구성 ====================

    def _get_display_options(self):
        return {
            'show_name': self._chk_name.isChecked(),
            'show_memo': self._chk_memo.isChecked(),
            'font_size': self._spin_font.value(),
        }

    def _calculate_cell_height(self, columns: int) -> int:
        ratio_text = self._combo_ratio.currentText()
        ratio_w, ratio_h = ASPECT_RATIOS.get(ratio_text, (16, 9))
        font_size = self._spin_font.value()
        show_info = self._chk_name.isChecked() or self._chk_memo.isChecked()

        viewport_width = self._scroll.viewport().width()
        spacing = self._grid.spacing()
        margins = self._grid.contentsMargins()
        avail = viewport_width - margins.left() - margins.right() - spacing * max(0, columns - 1)
        cell_width = max(40, avail // max(1, columns))

        image_height = max(30, int(cell_width * ratio_h / ratio_w))

        if show_info:
            row1_h = max(16, font_size + 6)
            row2_h = max(14, font_size + 4)
            row3_h = max(10, font_size)
            info_height = row1_h + row2_h + row3_h + 4
        else:
            info_height = 0

        return image_height + info_height + 9  # margins + spacing

    def rebuild_grid(self):
        for widget in self._thumbnails.values():
            self._grid.removeWidget(widget)
            widget.deleteLater()
        self._thumbnails.clear()

        for ph in self._placeholders:
            self._grid.removeWidget(ph)
            ph.deleteLater()
        self._placeholders.clear()

        columns = self._spin_cols.value()
        opts = self._get_display_options()
        cell_height = self._calculate_cell_height(columns)
        filter_text = self._search_input.text().strip().lower()

        all_pcs = self.pc_manager.get_all_pcs()
        if filter_text:
            pcs = [
                pc for pc in all_pcs
                if filter_text in pc.name.lower()
                or filter_text in getattr(pc.info, 'memo', '').lower()
            ]
        else:
            pcs = all_pcs

        count = len(pcs)

        for i, pc in enumerate(pcs):
            row = i // columns
            col = i % columns

            memo = getattr(pc.info, 'memo', '')
            thumb = PCThumbnailWidget(
                pc.name, memo,
                show_name=opts['show_name'],
                show_memo=opts['show_memo'],
                font_size=opts['font_size'],
            )
            thumb.setFixedHeight(cell_height)
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

        # 마지막 행 빈 슬롯
        if count > 0:
            remainder = count % columns
            ph_count = (columns - remainder) % columns
        else:
            ph_count = columns

        for j in range(ph_count):
            i = count + j
            row = i // columns
            col = i % columns
            ph = PlaceholderSlotWidget()
            ph.setFixedHeight(cell_height)
            self._grid.addWidget(ph, row, col)
            self._placeholders.append(ph)

        # ── 좌측 상단 정렬: 열 균등 분배 + 하단 여백 stretch ──
        for c in range(columns):
            self._grid.setColumnStretch(c, 1)
        total_items = count + ph_count
        last_row = (total_items - 1) // columns if total_items > 0 else 0
        for r in range(last_row + 1):
            self._grid.setRowStretch(r, 0)
        self._grid.setRowStretch(last_row + 1, 1)

    # ==================== 설정 변경 핸들러 ====================

    def _on_columns_changed(self, value: int):
        settings.set('grid_view.columns', value)
        self.rebuild_grid()

    def _on_ratio_changed(self, text: str):
        settings.set('grid_view.aspect_ratio', text)
        self.rebuild_grid()

    def _on_display_changed(self):
        settings.set('grid_view.show_name', self._chk_name.isChecked(), auto_save=False)
        settings.set('grid_view.show_memo', self._chk_memo.isChecked(), auto_save=False)
        settings.set('grid_view.font_size', self._spin_font.value())
        self.rebuild_grid()

    def _on_filter_changed(self, text: str):
        self.rebuild_grid()

    # ==================== 이벤트 핸들러 ====================

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
        self.agent_server.ping_all_agents()

    def _request_all_performance(self):
        self.agent_server.request_all_performance()

    def _on_performance_received(self, agent_id: str, data: dict):
        pc = self.pc_manager.get_pc_by_agent_id(agent_id)
        if pc:
            thumb = self._thumbnails.get(pc.name)
            if thumb:
                thumb.update_performance(data.get('cpu', 0), data.get('ram', 0))

    def _on_latency_measured(self, agent_id: str, ms: int):
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

    # ==================== 공개 메서드 ====================

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

    def set_columns(self, value: int):
        self._spin_cols.setValue(value)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start(150)

    def _on_resize_done(self):
        if self._thumbnails:
            self.rebuild_grid()
