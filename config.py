"""
WellcomSOFT 설정
"""

import sys
import os
import json
from typing import Any, Optional


def _get_base_dir():
    """실행 환경에 따른 기본 디렉터리 결정"""
    env_base = os.environ.get('WELLCOMSOFT_BASE_DIR')
    if env_base and os.path.isdir(env_base):
        return env_base

    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


# 기본 경로
BASE_DIR = _get_base_dir()
DATA_DIR = os.path.join(BASE_DIR, "data")

# 데이터베이스
DB_PATH = os.path.join(DATA_DIR, "pc_devices.db")
CONFIG_PATH = os.path.join(DATA_DIR, "settings.json")

# 로그 / 백업
LOG_DIR = os.path.join(BASE_DIR, "logs")
BACKUP_DIR = os.path.join(BASE_DIR, "backup")

# 아이콘 경로
def _get_icon_path():
    """아이콘 파일 경로 (EXE/개발 환경 자동 감지)"""
    env_base = os.environ.get('WELLCOMSOFT_BASE_DIR')
    if env_base:
        candidates = [
            os.path.join(env_base, "_internal", "assets", "wellcom.ico"),
            os.path.join(env_base, "assets", "wellcom.ico"),
            os.path.join(env_base, "wellcom.ico"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p

    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, "assets", "wellcom.ico")
    else:
        for p in [
            os.path.join(BASE_DIR, "build", "wellcom.ico"),
            os.path.join(BASE_DIR, "wellcom.ico"),
        ]:
            if os.path.exists(p):
                return p
    return ""

ICON_PATH = _get_icon_path()

# 모니터링
MONITOR_INTERVAL = 5000  # ms
STATUS_CHECK_INTERVAL = 3000  # ms

# UI 설정
WINDOW_TITLE = "WellcomSOFT"
WINDOW_MIN_WIDTH = 1400
WINDOW_MIN_HEIGHT = 900


class Settings:
    """애플리케이션 설정 관리"""

    _instance: Optional['Settings'] = None
    _defaults = {
        'window': {
            'width': WINDOW_MIN_WIDTH,
            'height': WINDOW_MIN_HEIGHT,
            'x': 100,
            'y': 100,
            'maximized': False
        },
        'agent_server': {
            'port': 9877,
            'max_connections': 200,
        },
        'screen': {
            'thumbnail_interval': 1000,   # ms - 그리드 썸네일 갱신 간격
            'stream_fps': 15,             # 전체 화면 스트리밍 FPS
            'stream_quality': 60,         # 스트리밍 JPEG 품질 (1-100)
            'thumbnail_quality': 40,      # 썸네일 JPEG 품질
            'thumbnail_width': 320,       # 썸네일 최대 너비
        },
        'grid_view': {
            'columns': 5,                 # 기본 5컬럼 (LinkIO 기준)
            'scale_factor': 100,          # 축척 (%)
            'show_title': True,           # 타이틀 표시
            'frame_speed': 5,             # 그리드 FPS
        },
        'multi_control': {
            'random_pos_x': 3,            # 랜덤 좌표 오프셋 X (px)
            'random_pos_y': 3,            # 랜덤 좌표 오프셋 Y (px)
            'random_delay_min': 300,      # 랜덤 딜레이 최소 (ms)
            'random_delay_max': 2000,     # 랜덤 딜레이 최대 (ms)
            'input_delay': 0.01,          # 기본 입력 간격 (초)
        },
        'desktop_widget': {
            'fullscreen': False,
            'sound_mute': True,
            'side_menu': True,
            'title_bar': True,
        },
        'shortcuts': {
            # 단축키 슬롯 Key_1 ~ Key_20
            'key_1': '', 'key_2': '', 'key_3': '', 'key_4': '', 'key_5': '',
            'key_6': '', 'key_7': '', 'key_8': '', 'key_9': '', 'key_10': '',
            'key_11': '', 'key_12': '', 'key_13': '', 'key_14': '', 'key_15': '',
            'key_16': '', 'key_17': '', 'key_18': '', 'key_19': '', 'key_20': '',
        },
        'general': {
            'theme': 'dark',              # dark / light
            'language': 'ko',
            'start_minimized': False,
            'confirm_delete': True
        },
        'update': {
            'github_token': '',
            'auto_check': True,
            'skip_version': ''
        },
        'server': {
            'api_url': 'http://log.wellcomll.org:4797',
            'token': '',
            'username': '',
            'auto_login': False
        },
        'p2p': {
            'agent_ws_port': 21350,         # 에이전트 WS 서버 기본 포트
            'connect_timeout_lan': 3,       # LAN 연결 타임아웃 (초)
            'connect_timeout_wan': 5,       # WAN 연결 타임아웃 (초)
            'reconnect_interval': 10,       # 재연결 간격 (초)
        },
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = {}
            cls._instance._load()
        return cls._instance

    def _load(self):
        """설정 파일 로드"""
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
        except Exception as e:
            print(f"설정 로드 실패: {e}")
            self._data = {}

    def save(self):
        """설정 파일 저장"""
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"설정 저장 실패: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """설정 값 가져오기 (점 표기법 지원)"""
        keys = key.split('.')
        value = self._data

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                default_val = self._defaults
                for dk in keys:
                    if isinstance(default_val, dict) and dk in default_val:
                        default_val = default_val[dk]
                    else:
                        return default
                return default_val

        return value

    def set(self, key: str, value: Any, auto_save: bool = True):
        """설정 값 저장 (점 표기법 지원)"""
        keys = key.split('.')
        data = self._data

        for k in keys[:-1]:
            if k not in data:
                data[k] = {}
            data = data[k]

        data[keys[-1]] = value

        if auto_save:
            self.save()

    def reset(self, key: Optional[str] = None):
        """설정 초기화"""
        if key:
            keys = key.split('.')
            default_val = self._defaults
            for k in keys:
                if isinstance(default_val, dict) and k in default_val:
                    default_val = default_val[k]
                else:
                    return
            self.set(key, default_val)
        else:
            self._data = {}
            self.save()


# 싱글톤 인스턴스
settings = Settings()
