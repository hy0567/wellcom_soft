"""키보드/마우스 입력 주입 (pynput)"""

import logging
from typing import List

from pynput.keyboard import Controller as KeyboardController, Key
from pynput.mouse import Controller as MouseController, Button

logger = logging.getLogger(__name__)

# Qt 키 이름 → pynput Key 매핑
_SPECIAL_KEYS = {
    'enter': Key.enter, 'return': Key.enter,
    'tab': Key.tab,
    'space': Key.space,
    'backspace': Key.backspace,
    'delete': Key.delete,
    'escape': Key.esc, 'esc': Key.esc,
    'up': Key.up, 'down': Key.down,
    'left': Key.left, 'right': Key.right,
    'home': Key.home, 'end': Key.end,
    'pageup': Key.page_up, 'page_up': Key.page_up,
    'pagedown': Key.page_down, 'page_down': Key.page_down,
    'insert': Key.insert,
    'f1': Key.f1, 'f2': Key.f2, 'f3': Key.f3, 'f4': Key.f4,
    'f5': Key.f5, 'f6': Key.f6, 'f7': Key.f7, 'f8': Key.f8,
    'f9': Key.f9, 'f10': Key.f10, 'f11': Key.f11, 'f12': Key.f12,
    'capslock': Key.caps_lock, 'caps_lock': Key.caps_lock,
    'numlock': Key.num_lock, 'num_lock': Key.num_lock,
    'scrolllock': Key.scroll_lock, 'scroll_lock': Key.scroll_lock,
    'printscreen': Key.print_screen, 'print_screen': Key.print_screen,
    'pause': Key.pause,
    'ctrl': Key.ctrl, 'ctrl_l': Key.ctrl_l, 'ctrl_r': Key.ctrl_r,
    'shift': Key.shift, 'shift_l': Key.shift_l, 'shift_r': Key.shift_r,
    'alt': Key.alt, 'alt_l': Key.alt_l, 'alt_r': Key.alt_r,
    'meta': Key.cmd, 'win': Key.cmd, 'cmd': Key.cmd,
    'menu': Key.menu,
}

_MODIFIER_MAP = {
    'ctrl': Key.ctrl_l,
    'shift': Key.shift_l,
    'alt': Key.alt_l,
    'meta': Key.cmd,
    'win': Key.cmd,
}

_MOUSE_BUTTONS = {
    'left': Button.left,
    'right': Button.right,
    'middle': Button.middle,
}


class InputHandler:
    """키보드/마우스 입력 주입"""

    def __init__(self):
        self.keyboard = KeyboardController()
        self.mouse = MouseController()

    def handle_key_event(self, key: str, action: str, modifiers: List[str] = None):
        """키보드 이벤트 처리

        Args:
            key: 키 이름 ('a', 'enter', 'f1' 등)
            action: 'press' | 'release'
            modifiers: ['ctrl', 'shift', 'alt'] 등
        """
        try:
            pynput_key = self._resolve_key(key)

            if action == 'press':
                # 수정자 키 먼저 누르기
                if modifiers:
                    for mod in modifiers:
                        mod_key = _MODIFIER_MAP.get(mod.lower())
                        if mod_key:
                            self.keyboard.press(mod_key)

                self.keyboard.press(pynput_key)

            elif action == 'release':
                self.keyboard.release(pynput_key)

                # 수정자 키 해제
                if modifiers:
                    for mod in reversed(modifiers):
                        mod_key = _MODIFIER_MAP.get(mod.lower())
                        if mod_key:
                            self.keyboard.release(mod_key)

        except Exception as e:
            logger.debug(f"키 이벤트 처리 실패: key={key}, action={action}, err={e}")

    def handle_mouse_event(self, x: int, y: int, button: str = 'none',
                           action: str = 'move', scroll_delta: int = 0):
        """마우스 이벤트 처리

        Args:
            x, y: 절대 화면 좌표
            button: 'left' | 'right' | 'middle' | 'none'
            action: 'click' | 'press' | 'release' | 'move' | 'scroll' | 'double_click'
            scroll_delta: 스크롤 양 (양수=위, 음수=아래)
        """
        try:
            if action == 'move':
                self.mouse.position = (x, y)

            elif action == 'click':
                self.mouse.position = (x, y)
                btn = _MOUSE_BUTTONS.get(button, Button.left)
                self.mouse.click(btn)

            elif action == 'double_click':
                self.mouse.position = (x, y)
                btn = _MOUSE_BUTTONS.get(button, Button.left)
                self.mouse.click(btn, 2)

            elif action == 'press':
                self.mouse.position = (x, y)
                btn = _MOUSE_BUTTONS.get(button, Button.left)
                self.mouse.press(btn)

            elif action == 'release':
                self.mouse.position = (x, y)
                btn = _MOUSE_BUTTONS.get(button, Button.left)
                self.mouse.release(btn)

            elif action == 'scroll':
                self.mouse.position = (x, y)
                self.mouse.scroll(0, scroll_delta)

        except Exception as e:
            logger.debug(f"마우스 이벤트 처리 실패: action={action}, err={e}")

    def type_text(self, text: str):
        """텍스트 일괄 입력"""
        try:
            self.keyboard.type(text)
        except Exception as e:
            logger.debug(f"텍스트 입력 실패: {e}")

    @staticmethod
    def _resolve_key(key: str):
        """키 이름 → pynput Key 객체 변환"""
        key_lower = key.lower()
        if key_lower in _SPECIAL_KEYS:
            return _SPECIAL_KEYS[key_lower]
        if len(key) == 1:
            return key
        return key
