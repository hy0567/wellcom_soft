"""원격 데스크톱 뷰어 위젯

원격 PC 화면을 표시하고, 키보드/마우스 입력을 캡처하여 에이전트에 전달한다.
"""

import logging
from typing import Optional

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QByteArray, pyqtSignal
from PyQt6.QtGui import QPainter, QPixmap, QKeyEvent, QMouseEvent, QWheelEvent

from core.pc_device import PCDevice
from core.agent_server import AgentServer

logger = logging.getLogger(__name__)

# Qt 키코드 → 키 이름 매핑
_QT_KEY_MAP = {
    Qt.Key.Key_Return: 'enter', Qt.Key.Key_Enter: 'enter',
    Qt.Key.Key_Tab: 'tab',
    Qt.Key.Key_Space: 'space',
    Qt.Key.Key_Backspace: 'backspace',
    Qt.Key.Key_Delete: 'delete',
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
    Qt.Key.Key_CapsLock: 'capslock',
    Qt.Key.Key_NumLock: 'numlock',
    Qt.Key.Key_ScrollLock: 'scrolllock',
    Qt.Key.Key_Print: 'printscreen',
    Qt.Key.Key_Pause: 'pause',
    Qt.Key.Key_Menu: 'menu',
}


class ViewerWidget(QWidget):
    """원격 데스크톱 뷰어"""

    def __init__(self, pc: PCDevice, agent_server: AgentServer):
        super().__init__()
        self._pc = pc
        self._server = agent_server
        self._pixmap = QPixmap()
        self._scale_x = 1.0
        self._scale_y = 1.0
        self._offset_x = 0
        self._offset_y = 0

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(640, 480)

    @property
    def pc_name(self) -> str:
        return self._pc.name

    def on_frame_received(self, agent_id: str, jpeg_data: bytes):
        """스트림 프레임 수신 (시그널 슬롯)"""
        if agent_id != self._pc.agent_id:
            return

        self._pixmap.loadFromData(QByteArray(jpeg_data))
        self._calculate_scale()
        self.update()

    def _calculate_scale(self):
        """화면 스케일 계산"""
        if self._pixmap.isNull():
            return

        pw, ph = self._pixmap.width(), self._pixmap.height()
        ww, wh = self.width(), self.height()

        # 비율 유지하며 맞춤
        scale = min(ww / pw, wh / ph)
        self._scale_x = scale
        self._scale_y = scale

        scaled_w = int(pw * scale)
        scaled_h = int(ph * scale)
        self._offset_x = (ww - scaled_w) // 2
        self._offset_y = (wh - scaled_h) // 2

    def _map_to_remote(self, local_x: int, local_y: int):
        """로컬 좌표 → 원격 화면 좌표"""
        if self._pixmap.isNull() or self._scale_x == 0:
            return 0, 0

        remote_x = int((local_x - self._offset_x) / self._scale_x)
        remote_y = int((local_y - self._offset_y) / self._scale_y)

        # 범위 제한
        remote_x = max(0, min(remote_x, self._pc.info.screen_width - 1))
        remote_y = max(0, min(remote_y, self._pc.info.screen_height - 1))

        return remote_x, remote_y

    def _get_modifiers(self, event) -> list:
        """현재 수정자 키 목록"""
        mods = []
        mod_flags = event.modifiers()
        if mod_flags & Qt.KeyboardModifier.ControlModifier:
            mods.append('ctrl')
        if mod_flags & Qt.KeyboardModifier.ShiftModifier:
            mods.append('shift')
        if mod_flags & Qt.KeyboardModifier.AltModifier:
            mods.append('alt')
        if mod_flags & Qt.KeyboardModifier.MetaModifier:
            mods.append('meta')
        return mods

    # ==================== 렌더링 ====================

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)

        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)

    def resizeEvent(self, event):
        self._calculate_scale()
        super().resizeEvent(event)

    # ==================== 키보드 입력 캡처 ====================

    def keyPressEvent(self, event: QKeyEvent):
        key = self._resolve_qt_key(event)
        if key:
            # 수정자 키 자체는 수정자 목록에서 제외
            mods = self._get_modifiers(event)
            if key in ('ctrl', 'shift', 'alt', 'meta'):
                mods = []
            self._server.send_key_event(self._pc.agent_id, key, 'press', mods)
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent):
        key = self._resolve_qt_key(event)
        if key:
            mods = self._get_modifiers(event)
            if key in ('ctrl', 'shift', 'alt', 'meta'):
                mods = []
            self._server.send_key_event(self._pc.agent_id, key, 'release', mods)
        event.accept()

    @staticmethod
    def _resolve_qt_key(event: QKeyEvent) -> Optional[str]:
        """Qt 키 이벤트 → 키 이름"""
        qt_key = event.key()

        # 수정자 키
        if qt_key in (Qt.Key.Key_Control, Qt.Key.Key_Meta):
            return 'ctrl'
        if qt_key in (Qt.Key.Key_Shift,):
            return 'shift'
        if qt_key in (Qt.Key.Key_Alt,):
            return 'alt'

        # 특수 키
        if qt_key in _QT_KEY_MAP:
            return _QT_KEY_MAP[qt_key]

        # 일반 문자
        text = event.text()
        if text and len(text) == 1:
            return text

        return None

    # ==================== 마우스 입력 캡처 ====================

    def mousePressEvent(self, event: QMouseEvent):
        x, y = self._map_to_remote(int(event.position().x()), int(event.position().y()))
        button = self._qt_button_name(event.button())
        self._server.send_mouse_event(self._pc.agent_id, x, y, button, 'press')
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        x, y = self._map_to_remote(int(event.position().x()), int(event.position().y()))
        button = self._qt_button_name(event.button())
        self._server.send_mouse_event(self._pc.agent_id, x, y, button, 'release')
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        x, y = self._map_to_remote(int(event.position().x()), int(event.position().y()))
        self._server.send_mouse_event(self._pc.agent_id, x, y, 'none', 'move')
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        x, y = self._map_to_remote(int(event.position().x()), int(event.position().y()))
        button = self._qt_button_name(event.button())
        self._server.send_mouse_event(self._pc.agent_id, x, y, button, 'double_click')
        event.accept()

    def wheelEvent(self, event: QWheelEvent):
        x, y = self._map_to_remote(int(event.position().x()), int(event.position().y()))
        delta = event.angleDelta().y() // 120  # 1 or -1
        self._server.send_mouse_event(
            self._pc.agent_id, x, y, 'none', 'scroll', scroll_delta=delta
        )
        event.accept()

    @staticmethod
    def _qt_button_name(button) -> str:
        if button == Qt.MouseButton.LeftButton:
            return 'left'
        elif button == Qt.MouseButton.RightButton:
            return 'right'
        elif button == Qt.MouseButton.MiddleButton:
            return 'middle'
        return 'none'
