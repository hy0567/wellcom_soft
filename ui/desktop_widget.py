"""DesktopWidget — 별도 창 원격 뷰어

LinkIO Desktop의 DesktopWidget 재현.
탭이 아닌 독립 QMainWindow로 열리며, 전체화면/사이드메뉴/단축키를 지원.

v2.0.1: 상태바(FPS/해상도/화질), 화질/FPS 조절, 특수키, 화면 비율 토글
"""

import logging
import os
import time
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QFileDialog, QInputDialog,
    QMessageBox, QApplication, QStatusBar, QLabel,
)
from PyQt6.QtCore import Qt, QByteArray, pyqtSignal, QTimer
from PyQt6.QtGui import QPainter, QPixmap, QKeyEvent, QMouseEvent, QWheelEvent, QFont

from config import settings
from core.pc_device import PCDevice
from core.agent_server import AgentServer
from core.multi_control import MultiControlManager
from core.h264_decoder import H264Decoder, HEADER_H264_KEYFRAME, HEADER_H264_DELTA
from ui.side_menu import SideMenu

logger = logging.getLogger(__name__)

# Qt 키코드 → 키 이름 매핑
_QT_KEY_MAP = {
    Qt.Key.Key_Return: 'enter', Qt.Key.Key_Enter: 'enter',
    Qt.Key.Key_Tab: 'tab', Qt.Key.Key_Space: 'space',
    Qt.Key.Key_Backspace: 'backspace', Qt.Key.Key_Delete: 'delete',
    Qt.Key.Key_Escape: 'escape',
    Qt.Key.Key_Up: 'up', Qt.Key.Key_Down: 'down',
    Qt.Key.Key_Left: 'left', Qt.Key.Key_Right: 'right',
    Qt.Key.Key_Home: 'home', Qt.Key.Key_End: 'end',
    Qt.Key.Key_PageUp: 'pageup', Qt.Key.Key_PageDown: 'pagedown',
    Qt.Key.Key_Insert: 'insert',
    Qt.Key.Key_F1: 'f1', Qt.Key.Key_F2: 'f2', Qt.Key.Key_F3: 'f3',
    Qt.Key.Key_F4: 'f4', Qt.Key.Key_F5: 'f5', Qt.Key.Key_F6: 'f6',
    Qt.Key.Key_F7: 'f7', Qt.Key.Key_F8: 'f8', Qt.Key.Key_F9: 'f9',
    Qt.Key.Key_F10: 'f10', Qt.Key.Key_F11: 'f11', Qt.Key.Key_F12: 'f12',
    Qt.Key.Key_CapsLock: 'capslock', Qt.Key.Key_NumLock: 'numlock',
    Qt.Key.Key_ScrollLock: 'scrolllock', Qt.Key.Key_Print: 'printscreen',
    Qt.Key.Key_Pause: 'pause', Qt.Key.Key_Menu: 'menu',
}


class RemoteScreenWidget(QWidget):
    """원격 화면 렌더링 위젯 (최적화)

    v2.0.1: Fit/Stretch 화면 비율 토글 지원
    """

    # 화면 비율 모드
    MODE_FIT = 'fit'          # 비율 유지 (레터박스)
    MODE_STRETCH = 'stretch'  # 창에 맞춤 (비율 무시)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._scaled_pixmap = QPixmap()  # 스케일 캐시
        self._scale = 1.0
        self._scale_x = 1.0   # Stretch 모드용 X 스케일
        self._scale_y = 1.0   # Stretch 모드용 Y 스케일
        self._offset_x = 0
        self._offset_y = 0
        self._last_widget_size = (0, 0)
        self._aspect_mode = self.MODE_FIT
        self.setMinimumSize(640, 480)
        self.setStyleSheet("background-color: #000;")

        # 더블 버퍼링 활성화
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    @property
    def current_pixmap(self) -> QPixmap:
        return self._pixmap

    @property
    def aspect_mode(self) -> str:
        return self._aspect_mode

    def set_aspect_mode(self, mode: str):
        """화면 비율 모드 변경"""
        if mode in (self.MODE_FIT, self.MODE_STRETCH):
            self._aspect_mode = mode
            self._rebuild_scaled()
            self.update()

    def update_frame(self, jpeg_data: bytes):
        """JPEG 프레임 업데이트"""
        self._pixmap.loadFromData(QByteArray(jpeg_data))
        self._rebuild_scaled()
        self.update()

    def update_frame_qimage(self, qimage):
        """QImage 프레임 업데이트 (H.264 디코더용, v2.0.2)"""
        self._pixmap = QPixmap.fromImage(qimage)
        self._rebuild_scaled()
        self.update()

    def _rebuild_scaled(self):
        """스케일된 이미지 캐시 재생성"""
        if self._pixmap.isNull():
            return
        pw, ph = self._pixmap.width(), self._pixmap.height()
        ww, wh = self.width(), self.height()
        if ww <= 0 or wh <= 0 or pw <= 0 or ph <= 0:
            return

        if self._aspect_mode == self.MODE_STRETCH:
            # Stretch: 비율 무시, 창 전체에 맞춤
            self._scale_x = ww / pw
            self._scale_y = wh / ph
            self._scale = min(self._scale_x, self._scale_y)  # 참고용
            self._offset_x = 0
            self._offset_y = 0
            self._scaled_pixmap = self._pixmap.scaled(
                ww, wh,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        else:
            # Fit: 비율 유지 (레터박스)
            self._scale = min(ww / pw, wh / ph)
            scaled_w = int(pw * self._scale)
            scaled_h = int(ph * self._scale)
            self._offset_x = (ww - scaled_w) // 2
            self._offset_y = (wh - scaled_h) // 2
            self._scale_x = self._scale
            self._scale_y = self._scale
            self._scaled_pixmap = self._pixmap.scaled(
                ww, wh,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )

        self._last_widget_size = (ww, wh)

    def map_to_remote(self, local_x: int, local_y: int, screen_w: int, screen_h: int):
        """로컬 좌표 → 원격 좌표"""
        if self._pixmap.isNull():
            return 0, 0

        if self._aspect_mode == self.MODE_STRETCH:
            # Stretch: 독립 스케일
            if self._scale_x == 0 or self._scale_y == 0:
                return 0, 0
            rx = int(local_x / self._scale_x)
            ry = int(local_y / self._scale_y)
        else:
            # Fit: 오프셋 + 단일 스케일
            if self._scale == 0:
                return 0, 0
            rx = int((local_x - self._offset_x) / self._scale)
            ry = int((local_y - self._offset_y) / self._scale)

        return max(0, min(rx, screen_w - 1)), max(0, min(ry, screen_h - 1))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if not self._scaled_pixmap.isNull():
            x = (self.width() - self._scaled_pixmap.width()) // 2
            y = (self.height() - self._scaled_pixmap.height()) // 2
            painter.drawPixmap(x, y, self._scaled_pixmap)

    def resizeEvent(self, event):
        new_size = (self.width(), self.height())
        if new_size != self._last_widget_size:
            self._rebuild_scaled()
        super().resizeEvent(event)


class DesktopWidget(QMainWindow):
    """별도 창 원격 뷰어 (LinkIO DesktopWidget 재현)

    v2.0.1: 상태바(FPS/해상도/화질), 화질/FPS 실시간 조절, 특수키, 화면 비율 토글
    """

    # 시그널
    closed = pyqtSignal(str)                # pc_name
    navigate_request = pyqtSignal(str, int) # pc_name, direction (-1=prev, +1=next)

    def __init__(self, pc: PCDevice, agent_server: AgentServer,
                 multi_control: Optional[MultiControlManager] = None,
                 pc_list: list = None):
        super().__init__()
        self._pc = pc
        self._server = agent_server
        self._multi_control = multi_control
        self._pc_list = pc_list or []   # 전체 PC 이름 목록 (방향키 전환용)

        self._is_fullscreen = False
        self._normal_geometry = None

        # 마우스 이동 이벤트 쓰로틀링 (서버 릴레이 고려)
        self._last_mouse_move_time = 0.0
        self._mouse_move_interval = 0.033  # ~30fps 마우스 이동 제한 (릴레이 최적)

        # v2.0.7 — 1:1 제어 품질 향상 (LinkIO 참고: 60fps/8Mbps)
        self._frame_count = 0
        self._current_fps = 0
        self._current_quality = settings.get('screen.stream_quality', 70)
        self._current_target_fps = settings.get('screen.stream_fps', 30)
        self._is_stretch = False   # 화면 비율 모드

        # v2.0.2 — H.264 디코더
        self._h264_decoder: Optional[H264Decoder] = None
        self._stream_codec = 'mjpeg'  # 실제 사용 코덱 ('mjpeg' 또는 'h264')

        self._init_ui()
        self._connect_signals()
        self._load_geometry()

        # FPS 측정 타이머 (1초마다)
        self._fps_timer = QTimer(self)
        self._fps_timer.timeout.connect(self._update_fps_display)
        self._fps_timer.start(1000)

        # 스트리밍 시작 (v2.0.2: H.264 코덱 협상)
        preferred_codec = settings.get('screen.stream_codec', 'h264')
        keyframe_interval = settings.get('screen.keyframe_interval', 60)

        # H.264 디코더 사용 가능 여부 미리 확인 — 불가 시 MJPEG으로 바로 요청
        if preferred_codec == 'h264':
            test_decoder = H264Decoder()
            if not test_decoder.is_available:
                logger.info(f"[{pc.name}] H.264 디코더 미지원 — MJPEG으로 요청")
                preferred_codec = 'mjpeg'
            test_decoder.close()

        self._server.start_streaming(
            pc.agent_id,
            fps=self._current_target_fps,
            quality=self._current_quality,
            codec=preferred_codec,
            keyframe_interval=keyframe_interval,
        )

        # 팝업 스타일: 독립 창으로 최상위에 활성화
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.raise_()
        self.activateWindow()

    @property
    def pc_name(self) -> str:
        return self._pc.name

    def _init_ui(self):
        self.setWindowTitle(f"제어: {self._pc.name}")
        self.setMinimumSize(800, 600)

        # 중앙 위젯
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 원격 화면
        self._screen = RemoteScreenWidget()
        self._screen.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._screen.setMouseTracking(True)
        layout.addWidget(self._screen, 1)

        # 사이드 메뉴
        self._side_menu = SideMenu()
        self._side_menu.fullscreen_clicked.connect(self.toggle_fullscreen)
        self._side_menu.clipboard_sync_clicked.connect(self._sync_clipboard)
        self._side_menu.file_send_clicked.connect(self._send_file)
        self._side_menu.screenshot_clicked.connect(self._save_screenshot)
        self._side_menu.command_clicked.connect(self._execute_command)
        self._side_menu.close_clicked.connect(self.close)

        # v2.0.1 — 사이드 메뉴 새 버튼 연결
        self._side_menu.quality_up_clicked.connect(lambda: self._adjust_quality(10))
        self._side_menu.quality_down_clicked.connect(lambda: self._adjust_quality(-10))
        self._side_menu.fps_up_clicked.connect(lambda: self._adjust_fps(5))
        self._side_menu.fps_down_clicked.connect(lambda: self._adjust_fps(-5))
        self._side_menu.ctrl_alt_del_clicked.connect(self._send_ctrl_alt_del)
        self._side_menu.alt_tab_clicked.connect(self._send_alt_tab)
        self._side_menu.win_key_clicked.connect(self._send_win_key)
        self._side_menu.ratio_toggle_clicked.connect(self._toggle_aspect_ratio)

        if settings.get('desktop_widget.side_menu', True):
            layout.addWidget(self._side_menu)
        else:
            self._side_menu.hide()

        # 키보드/마우스 이벤트를 screen 위젯에서 캡처
        self._screen.installEventFilter(self)

        # v2.0.1 — 상태바
        self._init_statusbar()

    def _init_statusbar(self):
        """상태바 초기화 — FPS/해상도/화질/비율 표시"""
        sb = self.statusBar()
        sb.setStyleSheet("""
            QStatusBar {
                background-color: #1e1e1e;
                color: #aaa;
                font-size: 11px;
                border-top: 1px solid #3e3e3e;
            }
            QStatusBar::item { border: none; }
        """)
        sb.setFixedHeight(22)

        label_style = "color: #aaa; padding: 0 6px; font-size: 11px;"

        self._res_label = QLabel("-- x --")
        self._res_label.setStyleSheet(label_style)
        sb.addPermanentWidget(self._res_label)

        self._fps_label = QLabel("0 FPS")
        self._fps_label.setStyleSheet(label_style)
        sb.addPermanentWidget(self._fps_label)

        self._quality_label = QLabel(f"Q:{self._current_quality}")
        self._quality_label.setStyleSheet(label_style)
        sb.addPermanentWidget(self._quality_label)

        self._codec_label = QLabel("--")
        self._codec_label.setStyleSheet(label_style)
        sb.addPermanentWidget(self._codec_label)

        self._ratio_label = QLabel("Fit")
        self._ratio_label.setStyleSheet(label_style)
        sb.addPermanentWidget(self._ratio_label)

    def _connect_signals(self):
        self._server.screen_frame_received.connect(self._on_frame_received)
        self._server.h264_frame_received.connect(self._on_h264_frame)
        self._server.stream_started.connect(self._on_stream_started)

    def _load_geometry(self):
        """저장된 창 위치/크기 복원"""
        x = settings.get('desktop_widget.x', 100)
        y = settings.get('desktop_widget.y', 100)
        w = settings.get('desktop_widget.width', 960)
        h = settings.get('desktop_widget.height', 640)
        self.setGeometry(x, y, w, h)

        if settings.get('desktop_widget.fullscreen', False):
            QTimer.singleShot(100, self.toggle_fullscreen)

    def _save_geometry(self):
        """창 위치/크기 저장"""
        if not self._is_fullscreen:
            geo = self.geometry()
            settings.set('desktop_widget.x', geo.x(), auto_save=False)
            settings.set('desktop_widget.y', geo.y(), auto_save=False)
            settings.set('desktop_widget.width', geo.width(), auto_save=False)
            settings.set('desktop_widget.height', geo.height(), auto_save=False)
            settings.save()

    # ==================== 프레임 수신 ====================

    def _on_frame_received(self, agent_id: str, jpeg_data: bytes):
        if agent_id != self._pc.agent_id:
            return
        self._screen.update_frame(jpeg_data)
        self._frame_count += 1

        # 해상도 표시 갱신
        pix = self._screen.current_pixmap
        if not pix.isNull():
            self._res_label.setText(f"{pix.width()} x {pix.height()}")

    # ==================== 코덱 협상 (v2.0.2) ====================

    def _on_stream_started(self, agent_id: str, info: dict):
        """stream_started 응답 — 실제 코덱 확인 및 디코더 초기화"""
        if agent_id != self._pc.agent_id:
            return

        codec = info.get('codec', 'mjpeg')
        encoder = info.get('encoder', '')
        self._stream_codec = codec

        if codec == 'h264':
            # H.264 디코더 초기화
            self._h264_decoder = H264Decoder()
            if self._h264_decoder.is_available:
                logger.info(
                    f"[{self._pc.name}] H.264 스트리밍 (인코더: {encoder})"
                )
                self._codec_label.setText(f"H.264 ({encoder})")
            else:
                logger.warning(
                    f"[{self._pc.name}] H.264 디코더 불가 — MJPEG으로 재시작"
                )
                self._h264_decoder = None
                self._stream_codec = 'mjpeg'
                self._codec_label.setText("MJPEG")
                # 에이전트에 MJPEG으로 재시작 요청
                self._server.stop_streaming(self._pc.agent_id)
                QTimer.singleShot(200, self._restart_as_mjpeg)
        else:
            self._h264_decoder = None
            logger.info(f"[{self._pc.name}] MJPEG 스트리밍")
            self._codec_label.setText("MJPEG")

    def _restart_as_mjpeg(self):
        """H.264 불가 시 MJPEG으로 스트리밍 재시작"""
        logger.info(f"[{self._pc.name}] MJPEG으로 스트리밍 재시작")
        self._server.start_streaming(
            self._pc.agent_id,
            fps=self._current_target_fps,
            quality=self._current_quality,
            codec='mjpeg',
        )

    # ==================== H.264 프레임 수신 (v2.0.2) ====================

    def _on_h264_frame(self, agent_id: str, header: int, raw_data: bytes):
        """H.264 프레임 수신 → 디코딩 → QImage 렌더링"""
        if agent_id != self._pc.agent_id:
            return

        if not self._h264_decoder:
            return

        qimage = self._h264_decoder.decode_frame(header, raw_data)
        if qimage:
            self._screen.update_frame_qimage(qimage)
            self._frame_count += 1

            # 해상도 표시 갱신
            self._res_label.setText(f"{qimage.width()} x {qimage.height()}")
        elif self._h264_decoder.waiting_for_keyframe:
            # 키프레임 대기 중 — 에이전트에 요청
            self._server.request_keyframe(self._pc.agent_id)

    # ==================== FPS 표시 ====================

    def _update_fps_display(self):
        """1초 타이머 — 실측 FPS 계산 및 상태바 갱신"""
        self._current_fps = self._frame_count
        self._frame_count = 0
        self._fps_label.setText(f"{self._current_fps} FPS")

    # ==================== 화질/FPS 조절 (v2.0.1) ====================

    def _adjust_quality(self, delta: int):
        """화질 조절 (±10)"""
        new_q = max(10, min(100, self._current_quality + delta))
        if new_q == self._current_quality:
            return
        self._current_quality = new_q
        self._quality_label.setText(f"Q:{new_q}")
        self._server.update_streaming(
            self._pc.agent_id,
            fps=self._current_target_fps,
            quality=self._current_quality,
        )
        logger.info(f"[{self._pc.name}] 화질 변경: {new_q}")

    def _adjust_fps(self, delta: int):
        """FPS 조절 (±5)"""
        new_fps = max(1, min(60, self._current_target_fps + delta))
        if new_fps == self._current_target_fps:
            return
        self._current_target_fps = new_fps
        self._server.update_streaming(
            self._pc.agent_id,
            fps=self._current_target_fps,
            quality=self._current_quality,
        )
        logger.info(f"[{self._pc.name}] FPS 변경: {new_fps}")

    # ==================== 특수키 (v2.0.1) ====================

    def _send_ctrl_alt_del(self):
        """Ctrl+Alt+Del 전송"""
        self._server.send_special_key(self._pc.agent_id, 'ctrl_alt_del')
        logger.info(f"[{self._pc.name}] Ctrl+Alt+Del 전송")

    def _send_alt_tab(self):
        """Alt+Tab 전송"""
        self._server.send_special_key(self._pc.agent_id, 'alt_tab')
        logger.info(f"[{self._pc.name}] Alt+Tab 전송")

    def _send_win_key(self):
        """Windows 키 전송"""
        self._server.send_special_key(self._pc.agent_id, 'win')
        logger.info(f"[{self._pc.name}] Win 키 전송")

    # ==================== 화면 비율 토글 (v2.0.1) ====================

    def _toggle_aspect_ratio(self):
        """Fit ↔ Stretch 토글"""
        self._is_stretch = not self._is_stretch
        if self._is_stretch:
            self._screen.set_aspect_mode(RemoteScreenWidget.MODE_STRETCH)
            self._ratio_label.setText("Stretch")
        else:
            self._screen.set_aspect_mode(RemoteScreenWidget.MODE_FIT)
            self._ratio_label.setText("Fit")
        logger.info(f"[{self._pc.name}] 비율: {self._screen.aspect_mode}")

    # ==================== 전체화면 ====================

    def toggle_fullscreen(self):
        if self._is_fullscreen:
            self._is_fullscreen = False
            self.showNormal()
            if self._normal_geometry:
                self.setGeometry(self._normal_geometry)
            self._side_menu.show()
            self.statusBar().show()
        else:
            self._normal_geometry = self.geometry()
            self._is_fullscreen = True
            self._side_menu.hide()
            self.statusBar().hide()
            self.showFullScreen()

    # ==================== 사이드 메뉴 액션 ====================

    def _sync_clipboard(self):
        """클립보드 동기화 요청"""
        self._server._send_to_agent(self._pc.agent_id, {'type': 'get_clipboard'})

    def _send_file(self):
        """파일 전송"""
        path, _ = QFileDialog.getOpenFileName(self, "파일 선택")
        if path:
            self._server.send_file(self._pc.agent_id, path)

    def _save_screenshot(self):
        """현재 화면 스크린샷 저장"""
        pixmap = self._screen.current_pixmap
        if pixmap.isNull():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "스크린샷 저장", f"{self._pc.name}_screenshot.png",
            "PNG (*.png);;JPEG (*.jpg)"
        )
        if path:
            pixmap.save(path)

    def _execute_command(self):
        """원격 명령 실행"""
        cmd, ok = QInputDialog.getText(self, "명령 실행", "명령어:")
        if ok and cmd.strip():
            self._server.execute_command(self._pc.agent_id, cmd.strip())

    # ==================== 키보드/마우스 이벤트 ====================

    def eventFilter(self, obj, event):
        """screen 위젯의 이벤트 필터"""
        if obj != self._screen:
            return False

        from PyQt6.QtCore import QEvent

        if event.type() == QEvent.Type.KeyPress:
            self._on_key_press(event)
            return True
        elif event.type() == QEvent.Type.KeyRelease:
            self._on_key_release(event)
            return True
        elif event.type() == QEvent.Type.MouseButtonPress:
            self._on_mouse_press(event)
            return True
        elif event.type() == QEvent.Type.MouseButtonRelease:
            self._on_mouse_release(event)
            return True
        elif event.type() == QEvent.Type.MouseMove:
            self._on_mouse_move(event)
            return True
        elif event.type() == QEvent.Type.MouseButtonDblClick:
            self._on_mouse_double_click(event)
            return True
        elif event.type() == QEvent.Type.Wheel:
            self._on_wheel(event)
            return True

        return False

    def _on_key_press(self, event: QKeyEvent):
        key = self._resolve_key(event)
        if not key:
            return

        # F11 = 전체화면 토글 (로컬 처리)
        if key == 'f11':
            self.toggle_fullscreen()
            return

        # 전체화면 시 ←→ = PC 전환
        if self._is_fullscreen and key in ('left', 'right') and not self._get_modifiers(event):
            direction = -1 if key == 'left' else 1
            self.navigate_request.emit(self._pc.name, direction)
            return

        mods = self._get_modifiers(event)
        if key in ('ctrl', 'shift', 'alt', 'meta'):
            mods = []

        # 멀컨 모드면 모든 선택 PC에 전달
        if self._multi_control and self._multi_control.is_active:
            self._multi_control.broadcast_key_event(key, 'press', mods)
        else:
            self._server.send_key_event(self._pc.agent_id, key, 'press', mods)

    def _on_key_release(self, event: QKeyEvent):
        key = self._resolve_key(event)
        if not key or key == 'f11':
            return

        mods = self._get_modifiers(event)
        if key in ('ctrl', 'shift', 'alt', 'meta'):
            mods = []

        if self._multi_control and self._multi_control.is_active:
            self._multi_control.broadcast_key_event(key, 'release', mods)
        else:
            self._server.send_key_event(self._pc.agent_id, key, 'release', mods)

    def _on_mouse_press(self, event: QMouseEvent):
        x, y = self._map_mouse(event)
        button = self._button_name(event.button())
        if self._multi_control and self._multi_control.is_active:
            self._multi_control.broadcast_mouse_event(x, y, button, 'press')
        else:
            self._server.send_mouse_event(self._pc.agent_id, x, y, button, 'press')

    def _on_mouse_release(self, event: QMouseEvent):
        x, y = self._map_mouse(event)
        button = self._button_name(event.button())
        if self._multi_control and self._multi_control.is_active:
            self._multi_control.broadcast_mouse_event(x, y, button, 'release')
        else:
            self._server.send_mouse_event(self._pc.agent_id, x, y, button, 'release')

    def _on_mouse_move(self, event: QMouseEvent):
        # 쓰로틀링: 너무 빈번한 마우스 이동은 무시 → 부드러움 개선
        now = time.time()
        if now - self._last_mouse_move_time < self._mouse_move_interval:
            return
        self._last_mouse_move_time = now

        x, y = self._map_mouse(event)
        if self._multi_control and self._multi_control.is_active:
            self._multi_control.broadcast_mouse_event(x, y, 'none', 'move')
        else:
            self._server.send_mouse_event(self._pc.agent_id, x, y, 'none', 'move')

    def _on_mouse_double_click(self, event: QMouseEvent):
        x, y = self._map_mouse(event)
        button = self._button_name(event.button())
        if self._multi_control and self._multi_control.is_active:
            self._multi_control.broadcast_mouse_event(x, y, button, 'double_click')
        else:
            self._server.send_mouse_event(self._pc.agent_id, x, y, button, 'double_click')

    def _on_wheel(self, event: QWheelEvent):
        x, y = self._screen.map_to_remote(
            int(event.position().x()), int(event.position().y()),
            self._pc.info.screen_width, self._pc.info.screen_height,
        )
        delta = event.angleDelta().y() // 120
        if self._multi_control and self._multi_control.is_active:
            self._multi_control.broadcast_mouse_event(x, y, 'none', 'scroll', delta)
        else:
            self._server.send_mouse_event(self._pc.agent_id, x, y, 'none', 'scroll', delta)

    def _map_mouse(self, event: QMouseEvent):
        return self._screen.map_to_remote(
            int(event.position().x()), int(event.position().y()),
            self._pc.info.screen_width, self._pc.info.screen_height,
        )

    # ==================== 유틸리티 ====================

    @staticmethod
    def _resolve_key(event: QKeyEvent) -> Optional[str]:
        qt_key = event.key()
        if qt_key in (Qt.Key.Key_Control, Qt.Key.Key_Meta):
            return 'ctrl'
        if qt_key == Qt.Key.Key_Shift:
            return 'shift'
        if qt_key == Qt.Key.Key_Alt:
            return 'alt'
        if qt_key in _QT_KEY_MAP:
            return _QT_KEY_MAP[qt_key]
        text = event.text()
        if text and len(text) == 1:
            return text
        return None

    @staticmethod
    def _get_modifiers(event) -> list:
        mods = []
        flags = event.modifiers()
        if flags & Qt.KeyboardModifier.ControlModifier:
            mods.append('ctrl')
        if flags & Qt.KeyboardModifier.ShiftModifier:
            mods.append('shift')
        if flags & Qt.KeyboardModifier.AltModifier:
            mods.append('alt')
        if flags & Qt.KeyboardModifier.MetaModifier:
            mods.append('meta')
        return mods

    @staticmethod
    def _button_name(button) -> str:
        if button == Qt.MouseButton.LeftButton:
            return 'left'
        elif button == Qt.MouseButton.RightButton:
            return 'right'
        elif button == Qt.MouseButton.MiddleButton:
            return 'middle'
        return 'none'

    # ==================== 윈도우 이벤트 ====================

    def closeEvent(self, event):
        """닫기 시 스트리밍 중지 + 설정 저장"""
        self._save_geometry()

        # FPS 타이머 중지
        self._fps_timer.stop()

        # 스트리밍 중지
        self._server.stop_streaming(self._pc.agent_id)

        # H.264 디코더 정리 (v2.0.2)
        if self._h264_decoder:
            self._h264_decoder.close()
            self._h264_decoder = None

        # 시그널 해제
        try:
            self._server.screen_frame_received.disconnect(self._on_frame_received)
        except TypeError:
            pass
        try:
            self._server.h264_frame_received.disconnect(self._on_h264_frame)
        except TypeError:
            pass
        try:
            self._server.stream_started.disconnect(self._on_stream_started)
        except TypeError:
            pass

        self.closed.emit(self._pc.name)
        event.accept()
