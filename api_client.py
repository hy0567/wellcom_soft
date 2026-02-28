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
        self._base_url = settings.get('server.api_url', 'http://log.wellcomll.org:8000')
        self._token: str = settings.load_token()
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
        settings.save_token(self._token)
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
        except requests.HTTPError as e:
            logger.info(f"토큰 검증 실패 (HTTP {e.response.status_code})")
            self._token = ''
            self._user = None
            return False
        except requests.ConnectionError:
            logger.warning("토큰 검증 실패: 서버 연결 불가")
            return False
        except Exception as e:
            logger.warning(f"토큰 검증 실패: {type(e).__name__}: {e}")
            self._token = ''
            self._user = None
            return False

    def logout(self):
        """로그아웃"""
        self._token = ''
        self._user = None
        settings.clear_token()

    # ==================== Agent 등록 (에이전트 측) ====================

    @property
    def token(self) -> str:
        """JWT 토큰 (P2P 연결 시 에이전트 인증용)"""
        return self._token

    def register_agent(self, agent_id: str, hostname: str,
                       os_info: str = '', ip: str = '',
                       ip_public: str = '', ws_port: int = 21350,
                       mac_address: str = '',
                       screen_width: int = 1920, screen_height: int = 1080,
                       agent_version: str = '',
                       cpu_model: str = '', cpu_cores: int = 0,
                       ram_gb: float = 0.0, motherboard: str = '',
                       gpu_model: str = '') -> dict:
        """에이전트 등록 (로그인한 사용자 소유로)"""
        return self._post('/api/agents/register', {
            'agent_id': agent_id,
            'hostname': hostname,
            'os_info': os_info,
            'ip': ip,
            'ip_public': ip_public,
            'ws_port': ws_port,
            'mac_address': mac_address,
            'screen_width': screen_width,
            'screen_height': screen_height,
            'agent_version': agent_version,
            'cpu_model': cpu_model,
            'cpu_cores': cpu_cores,
            'ram_gb': ram_gb,
            'motherboard': motherboard,
            'gpu_model': gpu_model,
        })

    def send_heartbeat(self, agent_id: str, ip: str = '',
                       ip_public: str = '', ws_port: int = 21350,
                       screen_width: int = 1920, screen_height: int = 1080,
                       agent_version: str = ''):
        """에이전트 하트비트"""
        try:
            self._post('/api/agents/heartbeat', {
                'agent_id': agent_id,
                'ip': ip,
                'ip_public': ip_public,
                'ws_port': ws_port,
                'screen_width': screen_width,
                'screen_height': screen_height,
                'agent_version': agent_version,
            })
        except requests.ConnectionError:
            logger.debug(f"하트비트 실패: 서버 연결 불가")
        except requests.Timeout:
            logger.debug(f"하트비트 실패: 타임아웃")
        except Exception as e:
            logger.warning(f"하트비트 실패: {type(e).__name__}: {e}")

    def report_offline(self, agent_id: str):
        """에이전트 오프라인 보고"""
        try:
            self._post('/api/agents/offline', {
                'agent_id': agent_id,
            })
        except requests.ConnectionError:
            logger.debug("오프라인 보고 실패: 서버 연결 불가")
        except Exception as e:
            logger.debug(f"오프라인 보고 실패: {type(e).__name__}")

    # ==================== Agent 조회 (매니저 측) ====================

    def get_agents(self) -> List[dict]:
        """내 에이전트 목록 조회"""
        try:
            return self._get('/api/agents')
        except requests.ConnectionError:
            logger.debug("에이전트 목록 조회 실패: 서버 연결 불가")
            return []
        except requests.HTTPError as e:
            logger.warning(f"에이전트 목록 조회 실패 (HTTP {e.response.status_code})")
            return []
        except Exception as e:
            logger.warning(f"에이전트 목록 조회 실패: {type(e).__name__}: {e}")
            return []

    def get_agent(self, agent_db_id: int) -> Optional[dict]:
        """특정 에이전트 조회"""
        try:
            return self._get(f'/api/agents/{agent_db_id}')
        except requests.ConnectionError:
            logger.debug(f"에이전트 조회 실패 ({agent_db_id}): 서버 연결 불가")
            return None
        except requests.HTTPError as e:
            logger.warning(f"에이전트 조회 실패 ({agent_db_id}): HTTP {e.response.status_code}")
            return None
        except Exception as e:
            logger.warning(f"에이전트 조회 실패 ({agent_db_id}): {type(e).__name__}: {e}")
            return None

    def delete_agent(self, agent_db_id: int) -> bool:
        """에이전트 삭제"""
        try:
            self._delete(f'/api/agents/{agent_db_id}')
            return True
        except requests.ConnectionError:
            logger.warning(f"에이전트 삭제 실패 ({agent_db_id}): 서버 연결 불가")
            return False
        except requests.HTTPError as e:
            logger.warning(f"에이전트 삭제 실패 ({agent_db_id}): HTTP {e.response.status_code}")
            return False
        except Exception as e:
            logger.warning(f"에이전트 삭제 실패 ({agent_db_id}): {type(e).__name__}: {e}")
            return False

    def move_agent_group(self, agent_db_id: int, group_name: str) -> bool:
        """에이전트 그룹 이동"""
        try:
            self._put(f'/api/agents/{agent_db_id}/group', params={'group_name': group_name})
            return True
        except requests.ConnectionError:
            logger.warning(f"그룹 이동 실패 ({agent_db_id}): 서버 연결 불가")
            return False
        except requests.HTTPError as e:
            logger.warning(f"그룹 이동 실패 ({agent_db_id}): HTTP {e.response.status_code}")
            return False
        except Exception as e:
            logger.warning(f"그룹 이동 실패 ({agent_db_id}): {type(e).__name__}: {e}")
            return False

    def rename_agent(self, agent_db_id: int, display_name: str) -> bool:
        """에이전트 이름 변경"""
        try:
            self._put(f'/api/agents/{agent_db_id}/name', params={'display_name': display_name})
            return True
        except requests.ConnectionError:
            logger.warning(f"이름 변경 실패 ({agent_db_id}): 서버 연결 불가")
            return False
        except requests.HTTPError as e:
            logger.warning(f"이름 변경 실패 ({agent_db_id}): HTTP {e.response.status_code}")
            return False
        except Exception as e:
            logger.warning(f"이름 변경 실패 ({agent_db_id}): {type(e).__name__}: {e}")
            return False

    def rename_agent_by_agent_id(self, agent_id: str, display_name: str) -> bool:
        """agent_id(hostname)로 에이전트 표시 이름 변경"""
        try:
            self._put(f'/api/agents/by-agent-id/{agent_id}/name',
                      params={'display_name': display_name})
            return True
        except requests.ConnectionError:
            logger.warning(f"서버 이름 변경 실패 ({agent_id}): 서버 연결 불가")
            return False
        except requests.HTTPError as e:
            logger.warning(f"서버 이름 변경 실패 ({agent_id}): HTTP {e.response.status_code}")
            return False
        except Exception as e:
            logger.warning(f"서버 이름 변경 실패 ({agent_id}): {type(e).__name__}: {e}")
            return False


# 싱글톤 인스턴스
api_client = APIClient()
