"""키매핑 시스템 — 키보드 키를 사용자 정의 동작에 매핑

LinkIO 키매핑 패턴 참고:
  Key_1 ~ Key_20 슬롯에 매핑 저장.
  매핑 프로파일 단위로 PC별 할당 가능.
"""

import json
import os
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


class KeyActionType(Enum):
    """키매핑 동작 타입"""
    KEY = "key"             # 단일 키 / 조합키
    CLICK = "click"         # 좌표 클릭
    TEXT = "text"           # 텍스트 입력
    SCRIPT = "script"       # 스크립트 실행
    COMMAND = "command"     # 원격 명령 실행


@dataclass
class KeyMapping:
    """단일 키 매핑"""
    trigger: str            # 트리거 키 (예: "F1", "Ctrl+Shift+A")
    action_type: KeyActionType
    action_data: dict = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            'trigger': self.trigger,
            'action_type': self.action_type.value,
            'action_data': self.action_data,
            'description': self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'KeyMapping':
        return cls(
            trigger=data.get('trigger', ''),
            action_type=KeyActionType(data.get('action_type', 'key')),
            action_data=data.get('action_data', {}),
            description=data.get('description', ''),
        )


@dataclass
class KeymapProfile:
    """키매핑 프로파일 (이름 단위)"""
    name: str
    mappings: List[KeyMapping] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'description': self.description,
            'mappings': [m.to_dict() for m in self.mappings],
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'KeymapProfile':
        profile = cls(
            name=data.get('name', ''),
            description=data.get('description', ''),
        )
        for m_data in data.get('mappings', []):
            try:
                profile.mappings.append(KeyMapping.from_dict(m_data))
            except (ValueError, KeyError):
                pass
        return profile


class KeyMapper(QObject):
    """키매핑 매니저"""

    mapping_triggered = pyqtSignal(str, dict)  # agent_id, action_data

    def __init__(self, agent_server, data_dir: str = ""):
        super().__init__()
        self.agent_server = agent_server
        self.data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'keymaps'
        )
        os.makedirs(self.data_dir, exist_ok=True)

        self._profiles: Dict[str, KeymapProfile] = {}
        self._active_profile: Optional[str] = None
        self._trigger_map: Dict[str, KeyMapping] = {}  # trigger → mapping
        self._load_profiles()

    def _load_profiles(self):
        """키맵 프로파일 목록 로드"""
        index_path = os.path.join(self.data_dir, 'keymaps.json')
        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for item in data.get('profiles', []):
                    profile = KeymapProfile.from_dict(item)
                    if profile.name:
                        self._profiles[profile.name] = profile
                self._active_profile = data.get('active_profile')
                self._build_trigger_map()
            except Exception as e:
                logger.error(f"키맵 로드 실패: {e}")

    def _save_profiles(self):
        """키맵 프로파일 저장"""
        index_path = os.path.join(self.data_dir, 'keymaps.json')
        try:
            data = {
                'active_profile': self._active_profile,
                'profiles': [p.to_dict() for p in self._profiles.values()],
            }
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"키맵 저장 실패: {e}")

    def _build_trigger_map(self):
        """현재 활성 프로파일의 트리거 맵 구축"""
        self._trigger_map.clear()
        if self._active_profile:
            profile = self._profiles.get(self._active_profile)
            if profile:
                for m in profile.mappings:
                    self._trigger_map[m.trigger.lower()] = m

    # ==================== 프로파일 관리 ====================

    def get_profiles(self) -> List[KeymapProfile]:
        return list(self._profiles.values())

    def get_profile(self, name: str) -> Optional[KeymapProfile]:
        return self._profiles.get(name)

    def get_active_profile(self) -> Optional[KeymapProfile]:
        if self._active_profile:
            return self._profiles.get(self._active_profile)
        return None

    def create_profile(self, name: str, description: str = "") -> KeymapProfile:
        profile = KeymapProfile(name=name, description=description)
        self._profiles[name] = profile
        self._save_profiles()
        return profile

    def delete_profile(self, name: str):
        self._profiles.pop(name, None)
        if self._active_profile == name:
            self._active_profile = None
            self._trigger_map.clear()
        self._save_profiles()

    def rename_profile(self, old_name: str, new_name: str) -> bool:
        profile = self._profiles.pop(old_name, None)
        if not profile:
            return False
        profile.name = new_name
        self._profiles[new_name] = profile
        if self._active_profile == old_name:
            self._active_profile = new_name
        self._save_profiles()
        return True

    def set_active_profile(self, name: Optional[str]):
        self._active_profile = name
        self._build_trigger_map()
        self._save_profiles()

    # ==================== 매핑 관리 ====================

    def add_mapping(self, profile_name: str, mapping: KeyMapping):
        profile = self._profiles.get(profile_name)
        if not profile:
            return
        # 중복 트리거 교체
        profile.mappings = [m for m in profile.mappings if m.trigger != mapping.trigger]
        profile.mappings.append(mapping)
        self._build_trigger_map()
        self._save_profiles()

    def remove_mapping(self, profile_name: str, trigger: str):
        profile = self._profiles.get(profile_name)
        if not profile:
            return
        profile.mappings = [m for m in profile.mappings if m.trigger != trigger]
        self._build_trigger_map()
        self._save_profiles()

    def update_mapping(self, profile_name: str, trigger: str, **kwargs):
        profile = self._profiles.get(profile_name)
        if not profile:
            return
        for m in profile.mappings:
            if m.trigger == trigger:
                for k, v in kwargs.items():
                    if hasattr(m, k):
                        setattr(m, k, v)
                break
        self._build_trigger_map()
        self._save_profiles()

    # ==================== 키 이벤트 처리 ====================

    def handle_key(self, key_str: str, agent_id: str) -> bool:
        """키 입력 처리 — 매핑이 있으면 실행하고 True 반환"""
        mapping = self._trigger_map.get(key_str.lower())
        if not mapping:
            return False

        self._execute_mapping(mapping, agent_id)
        return True

    def _execute_mapping(self, mapping: KeyMapping, agent_id: str):
        """매핑된 동작 실행"""
        data = mapping.action_data

        if mapping.action_type == KeyActionType.KEY:
            key = data.get('key', '')
            modifiers = data.get('modifiers', [])
            self.agent_server.send_key_event(agent_id, key, 'press', modifiers)

        elif mapping.action_type == KeyActionType.CLICK:
            x = data.get('x', 0)
            y = data.get('y', 0)
            self.agent_server.send_mouse_event(
                agent_id, x, y, button='left', action='click',
            )

        elif mapping.action_type == KeyActionType.TEXT:
            text = data.get('text', '')
            self.agent_server.send_clipboard_text(agent_id, text)
            import time
            time.sleep(0.1)
            self.agent_server.send_key_event(agent_id, 'v', 'press', ['ctrl'])

        elif mapping.action_type == KeyActionType.COMMAND:
            command = data.get('command', '')
            self.agent_server.execute_command(agent_id, command)

        elif mapping.action_type == KeyActionType.SCRIPT:
            # 스크립트 실행은 외부에서 처리 (시그널로 전달)
            pass

        self.mapping_triggered.emit(agent_id, mapping.to_dict())
