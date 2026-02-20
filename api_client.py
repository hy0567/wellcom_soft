"""WellcomSOFT API 클라이언트

매니저/에이전트 공통으로 사용하는 서버 통신 클라이언트.
로그인 → 에이전트 등록/조회 → 하트비트
"""
import logging
import requests
from typing import Optional, List

from config import settings

logger = logging.getLogger(__name__)


class APIClient:
    """서버 API 클라이언트"""

    def __init__(self):
        self._base_url = settings.get('server.api_url', 'http://log.wellcomll.org:4797')
        self._token: str = settings.get('server.token', '')
        self._user: Optional[dict] = None

    @property
    def is_logged_in(self) -> bool:
        return bool(self._token and self._user)

    @property
    def user(self) -> Optional[dict]:
        return self._user

    @property
    def username(self) -> str:
        return self._user.get('username', '') if self._user else ''

    @property
    def user_id(self) -> int:
        return self._user.get('id', 0) if self._user else 0

    @property
    def is_admin(self) -> bool:
        return self._user.get('role') == 'admin' if self._user else False

    def _headers(self) -> dict:
        h = {'Content-Type': 'application/json'}
        if self._token:
            h['Authorization'] = f'Bearer {self._token}'
        return h

    def _get(self, path: str) -> dict:
        r = requests.get(f'{self._base_url}{path}', headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, data: dict) -> dict:
        r = requests.post(f'{self._base_url}{path}', json=data, headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, data: dict = None, params: dict = None) -> dict:
        r = requests.put(
            f'{self._base_url}{path}', json=data, params=params,
            headers=self._headers(), timeout=10
        )
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> dict:
        r = requests.delete(f'{self._base_url}{path}', headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    # ==================== Auth ====================

    def login(self, username: str, password: str) -> dict:
        """로그인 → JWT 토큰 + 사용자 정보"""
        data = self._post('/api/auth/login', {
            'username': username,
            'password': password,
        })
        self._token = data['token']
        self._user = data['user']
        settings.set('server.token', self._token)
        settings.set('server.username', username)
        return data

    def verify_token(self) -> bool:
        """저장된 토큰 유효성 확인"""
        if not self._token:
            return False
        try:
            data = self._get('/api/auth/me')
            self._user = data
            return True
        except Exception:
            self._token = ''
            self._user = None
            return False

    def logout(self):
        """로그아웃"""
        self._token = ''
        self._user = None
        settings.set('server.token', '')

    # ==================== Agent 등록 (에이전트 측) ====================

    def register_agent(self, agent_id: str, hostname: str,
                       os_info: str = '', ip: str = '',
                       mac_address: str = '',
                       screen_width: int = 1920, screen_height: int = 1080) -> dict:
        """에이전트 등록 (로그인한 사용자 소유로)"""
        return self._post('/api/agents/register', {
            'agent_id': agent_id,
            'hostname': hostname,
            'os_info': os_info,
            'ip': ip,
            'mac_address': mac_address,
            'screen_width': screen_width,
            'screen_height': screen_height,
        })

    def send_heartbeat(self, agent_id: str, ip: str = '',
                       screen_width: int = 1920, screen_height: int = 1080):
        """에이전트 하트비트"""
        try:
            self._post('/api/agents/heartbeat', {
                'agent_id': agent_id,
                'ip': ip,
                'screen_width': screen_width,
                'screen_height': screen_height,
            })
        except Exception as e:
            logger.debug(f"하트비트 실패: {e}")

    def report_offline(self, agent_id: str):
        """에이전트 오프라인 보고"""
        try:
            self._post('/api/agents/offline', {
                'agent_id': agent_id,
            })
        except Exception:
            pass

    # ==================== Agent 조회 (매니저 측) ====================

    def get_agents(self) -> List[dict]:
        """내 에이전트 목록 조회"""
        try:
            return self._get('/api/agents')
        except Exception as e:
            logger.warning(f"에이전트 목록 조회 실패: {e}")
            return []

    def get_agent(self, agent_db_id: int) -> Optional[dict]:
        """특정 에이전트 조회"""
        try:
            return self._get(f'/api/agents/{agent_db_id}')
        except Exception:
            return None

    def delete_agent(self, agent_db_id: int) -> bool:
        try:
            self._delete(f'/api/agents/{agent_db_id}')
            return True
        except Exception:
            return False

    def move_agent_group(self, agent_db_id: int, group_name: str) -> bool:
        try:
            self._put(f'/api/agents/{agent_db_id}/group', params={'group_name': group_name})
            return True
        except Exception:
            return False

    def rename_agent(self, agent_db_id: int, display_name: str) -> bool:
        try:
            self._put(f'/api/agents/{agent_db_id}/name', params={'display_name': display_name})
            return True
        except Exception:
            return False


# 싱글톤 인스턴스
api_client = APIClient()
