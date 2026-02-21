"""멀티컨트롤 (멀컨) 시스템

LinkIO Desktop 기반 멀티/그룹 컨트롤.
선택된 PC들에 입력을 동시 전달하며, 랜덤 좌표/딜레이를 적용하여
자동화 감지를 방지한다.
"""

import asyncio
import logging
import random
import time
import threading
from typing import List, Set, Optional, Dict

from PyQt6.QtCore import QObject, pyqtSignal

from config import settings

logger = logging.getLogger(__name__)


class MultiControlManager(QObject):
    """멀티컨트롤 매니저"""

    # 시그널
    mode_changed = pyqtSignal(str)          # 'off', 'multi', 'group'
    selection_changed = pyqtSignal(list)     # 선택된 agent_id 목록

    def __init__(self, agent_server):
        super().__init__()
        self._agent_server = agent_server
        self._mode = 'off'                  # off / multi / group
        self._selected_agents: Set[str] = set()
        self._group_filter: str = ''        # 그룹컨트롤 시 그룹명

    # ==================== 모드 관리 ====================

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_active(self) -> bool:
        return self._mode != 'off'

    @property
    def selected_agents(self) -> List[str]:
        return list(self._selected_agents)

    def toggle_multi_control(self):
        """멀티컨트롤 토글 (Ctrl+1)"""
        if self._mode == 'multi':
            self.deactivate()
        else:
            self._mode = 'multi'
            self._group_filter = ''
            self.mode_changed.emit('multi')
            logger.info(f"멀티컨트롤 활성화: {len(self._selected_agents)}대 선택")

    def toggle_group_control(self, group_name: str = ''):
        """그룹컨트롤 토글 (Ctrl+2)"""
        if self._mode == 'group':
            self.deactivate()
        else:
            self._mode = 'group'
            self._group_filter = group_name
            self.mode_changed.emit('group')
            logger.info(f"그룹컨트롤 활성화: 그룹={group_name or '전체'}")

    def deactivate(self):
        """비활성화"""
        self._mode = 'off'
        self._group_filter = ''
        self.mode_changed.emit('off')

    def set_selected_agents(self, agent_ids: List[str]):
        """선택된 에이전트 설정"""
        self._selected_agents = set(agent_ids)
        self.selection_changed.emit(list(self._selected_agents))

    def add_agent(self, agent_id: str):
        """에이전트 추가 선택"""
        self._selected_agents.add(agent_id)
        self.selection_changed.emit(list(self._selected_agents))

    def remove_agent(self, agent_id: str):
        """에이전트 선택 해제"""
        self._selected_agents.discard(agent_id)
        self.selection_changed.emit(list(self._selected_agents))

    def clear_selection(self):
        """선택 전체 해제"""
        self._selected_agents.clear()
        self.selection_changed.emit([])

    # ==================== 랜덤 오프셋 적용 ====================

    def _apply_random_offset(self, x: int, y: int) -> tuple:
        """랜덤 좌표 오프셋 적용"""
        rand_x = settings.get('multi_control.random_pos_x', 3)
        rand_y = settings.get('multi_control.random_pos_y', 3)

        if rand_x > 0:
            x += random.randint(-rand_x, rand_x)
        if rand_y > 0:
            y += random.randint(-rand_y, rand_y)

        return max(0, x), max(0, y)

    def _get_random_delay(self) -> float:
        """랜덤 딜레이 (초) 반환"""
        delay_min = settings.get('multi_control.random_delay_min', 300)
        delay_max = settings.get('multi_control.random_delay_max', 2000)
        return random.randint(delay_min, delay_max) / 1000.0

    def _get_target_agents(self) -> List[str]:
        """현재 모드에 따른 대상 에이전트 목록"""
        if not self._selected_agents:
            return []

        connected = set(self._agent_server.get_connected_agents())
        return [a for a in self._selected_agents if a in connected]

    # ==================== 입력 브로드캐스트 ====================

    def broadcast_key_event(self, key: str, action: str, modifiers: list = None):
        """키 이벤트를 선택된 PC들에 전달 (랜덤 딜레이 적용)"""
        if not self.is_active:
            return

        targets = self._get_target_agents()
        if not targets:
            return

        use_delay = len(targets) > 1

        def _send():
            for i, agent_id in enumerate(targets):
                if use_delay and i > 0:
                    delay = self._get_random_delay()
                    time.sleep(delay)
                self._agent_server.send_key_event(
                    agent_id, key, action, modifiers or []
                )

        threading.Thread(target=_send, daemon=True).start()

    def broadcast_mouse_event(self, x: int, y: int, button: str = 'none',
                              action: str = 'move', scroll_delta: int = 0):
        """마우스 이벤트를 선택된 PC들에 전달 (랜덤 좌표/딜레이 적용)"""
        if not self.is_active:
            return

        targets = self._get_target_agents()
        if not targets:
            return

        use_delay = len(targets) > 1

        def _send():
            for i, agent_id in enumerate(targets):
                if use_delay and i > 0:
                    delay = self._get_random_delay()
                    time.sleep(delay)

                # 랜덤 좌표 오프셋 (각 PC마다 다른 오프셋)
                rx, ry = self._apply_random_offset(x, y)
                self._agent_server.send_mouse_event(
                    agent_id, rx, ry, button, action, scroll_delta
                )

        threading.Thread(target=_send, daemon=True).start()

    def broadcast_clipboard_text(self, text: str):
        """클립보드 텍스트를 선택된 PC들에 전달"""
        if not self.is_active:
            return

        for agent_id in self._get_target_agents():
            self._agent_server.send_clipboard_text(agent_id, text)

    def broadcast_command(self, command: str):
        """명령을 선택된 PC들에 전달"""
        targets = self._get_target_agents()
        if targets:
            self._agent_server.broadcast_command(targets, command)

    def broadcast_file(self, filepath: str):
        """파일을 선택된 PC들에 전달"""
        targets = self._get_target_agents()
        if targets:
            self._agent_server.broadcast_file(targets, filepath)
