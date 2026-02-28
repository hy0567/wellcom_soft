"""WellcomSOFT Agent 설정 관리"""

import json
import os
from pathlib import Path


class AgentConfig:
    """에이전트 설정 (JSON 파일 기반)"""

    DEFAULT = {
        'server_ip': '',
        'server_port': 9877,
        'api_url': '',              # 서버 API URL (예: http://log.wellcomll.org:4797)
        'api_username': '',         # 서버 로그인 사용자명
        'api_token': '',            # 서버 JWT 토큰 (자동 저장)
        'save_dir': '',
        'auto_start': True,
        'clipboard_sync': True,
        'screen_quality': 80,       # JPEG/H.264 품질 (1-100)
        'screen_fps': 30,           # 스트리밍 FPS (LinkIO 수준)
        'thumbnail_quality': 50,    # 썸네일 품질
        'thumbnail_width': 480,     # 썸네일 최대 너비
        'heartbeat_interval': 30,   # 하트비트 간격 (초)
        'ws_port': 21350,            # WS 서버 리스닝 포트 (P2P)
        'ws_max_connections': 5,     # 최대 동시 매니저 연결 수
    }

    def __init__(self, config_path: str = None):
        if config_path:
            self.path = Path(config_path)
        else:
            appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
            self.path = Path(appdata) / 'WellcomAgent' / 'config.json'

        self._data = dict(self.DEFAULT)
        self._load_portable()
        self._load()
        self._migrate()

        if not self._data['save_dir']:
            self._data['save_dir'] = str(
                Path.home() / 'Desktop' / 'WellcomAgent'
            )
            self._save()

    def _load_portable(self):
        """exe 옆 config.json 로드"""
        try:
            import sys
            if getattr(sys, 'frozen', False):
                exe_dir = Path(sys.executable).parent
            else:
                exe_dir = Path(__file__).parent
            portable = exe_dir / 'config.json'
            if portable.exists():
                raw = portable.read_text(encoding='utf-8')
                loaded = json.loads(raw)
                self._data.update(loaded)
        except Exception:
            pass

    def _load(self):
        try:
            if self.path.exists():
                raw = self.path.read_text(encoding='utf-8')
                loaded = json.loads(raw)
                self._data.update(loaded)
        except Exception:
            pass

    def _migrate(self):
        """구 버전 설정 마이그레이션 — 낮은 기본값을 신규 기본값으로 업그레이드"""
        changed = False
        # v3.2.4 이전: screen_quality=60, screen_fps=15 → 80, 30
        if self._data.get('screen_quality', 0) <= 60:
            self._data['screen_quality'] = 80
            changed = True
        if self._data.get('screen_fps', 0) <= 15:
            self._data['screen_fps'] = 30
            changed = True
        # v3.2.6 이전: thumbnail_quality=30, thumbnail_width=320 → 50, 480
        if self._data.get('thumbnail_quality', 0) <= 30:
            self._data['thumbnail_quality'] = 50
            changed = True
        if self._data.get('thumbnail_width', 0) <= 320:
            self._data['thumbnail_width'] = 480
            changed = True
        if changed:
            self._save()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )
        except Exception:
            pass

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value
        self._save()

    @property
    def server_ip(self) -> str:
        return self._data['server_ip']

    @property
    def server_port(self) -> int:
        return self._data['server_port']

    @property
    def save_dir(self) -> str:
        return self._data['save_dir']

    @property
    def auto_start(self) -> bool:
        return self._data['auto_start']

    @property
    def clipboard_sync(self) -> bool:
        return self._data['clipboard_sync']

    @property
    def screen_quality(self) -> int:
        return self._data.get('screen_quality', 80)

    @property
    def screen_fps(self) -> int:
        return self._data.get('screen_fps', 30)

    @property
    def thumbnail_quality(self) -> int:
        return self._data.get('thumbnail_quality', 50)

    @property
    def thumbnail_width(self) -> int:
        return self._data.get('thumbnail_width', 480)

    @property
    def api_url(self) -> str:
        return self._data.get('api_url', '')

    @property
    def api_username(self) -> str:
        return self._data.get('api_username', '')

    @property
    def api_token(self) -> str:
        return self._data.get('api_token', '')

    @property
    def heartbeat_interval(self) -> int:
        return self._data.get('heartbeat_interval', 30)

    @property
    def ws_port(self) -> int:
        return self._data.get('ws_port', 21350)

    @property
    def ws_max_connections(self) -> int:
        return self._data.get('ws_max_connections', 5)
