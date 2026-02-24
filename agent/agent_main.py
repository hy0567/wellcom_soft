"""WellcomSOFT Agent — 대상PC에서 실행되는 경량 에이전트 (P2P WS 서버)

v3.0.0: P2P 직접 연결 아키텍처 (LinkIO 방식)
- 에이전트가 WS 서버(포트 21350)로 동작, 매니저가 직접 접속
- 서버는 REST API만 사용 (로그인/등록/조회)
- 공인IP(ip_public) + 사설IP(ip) 이중 등록

기능:
- 서버 로그인 + 자기 등록 + 하트비트
- WebSocket 서버 (매니저가 직접 연결)
- 화면 캡처 및 스트리밍 (mss + MJPEG)
- 키보드/마우스 입력 주입 (pynput)
- 양방향 클립보드 동기화
- 파일 수신

사용법:
  python agent_main.py --api-url http://log.wellcomll.org:8000
  python agent_main.py --install --api-url http://log.wellcomll.org:8000
  python agent_main.py --uninstall
"""

import asyncio
import json
import base64
import logging
import sys
import os
import platform
import socket
import subprocess
import threading
import time
import winreg
from typing import Optional, Dict

try:
    import requests
except ImportError:
    print("requests 패키지가 필요합니다: pip install requests")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("websockets 패키지가 필요합니다: pip install websockets")
    sys.exit(1)

from agent_config import AgentConfig
from screen_capture import ScreenCapture
from input_handler import InputHandler
from clipboard_monitor import ClipboardMonitor
from file_receiver import FileReceiver

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('WellcomAgent')

STARTUP_REG_KEY = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
STARTUP_REG_NAME = 'WellcomAgent'


def _get_agent_base_dir() -> str:
    """에이전트 베이스 디렉터리 (업데이터 기준 경로)"""
    env_base = os.environ.get('WELLCOMAGENT_BASE_DIR')
    if env_base and os.path.isdir(env_base):
        return env_base
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    # 개발 환경: agent/agent_main.py → 상위 프로젝트 루트
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


AGENT_BASE_DIR = _get_agent_base_dir()


def _check_and_apply_update() -> bool:
    """시작 시 무음 자동 업데이트. True=업데이트 후 재시작, False=계속 실행"""
    try:
        # 개발 환경에서 updater/ 모듈 경로 추가
        _project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _project_dir not in sys.path:
            sys.path.insert(0, _project_dir)

        from pathlib import Path
        from updater import UpdateChecker

        try:
            from version import __version__, __github_repo__, __asset_name__
        except ImportError:
            __version__ = "0.0.0"
            __github_repo__ = "hy0567/wellcom_soft"
            __asset_name__ = "agent.zip"

        checker = UpdateChecker(
            Path(AGENT_BASE_DIR), __github_repo__,
            asset_name=__asset_name__,
            running_version=__version__,
        )

        has_update, release = checker.check_update()
        if not has_update:
            logger.info(f"최신 버전 사용 중: v{__version__}")
            return False

        logger.info(f"★ 업데이트 발견: v{__version__} → v{release.version} — 자동 적용 중...")
        success = checker.apply_update(release)
        if success:
            logger.info("업데이트 성공 — 재시작")
            _restart_agent()
            return True
        logger.warning("업데이트 적용 실패 — 현재 버전으로 계속 실행")
        return False
    except Exception as e:
        logger.debug(f"업데이트 확인 건너뜀: {e}")
        return False


def _restart_agent():
    """에이전트 재시작"""
    exe_path = os.environ.get('WELLCOMAGENT_EXE_PATH')
    if exe_path and os.path.exists(exe_path):
        subprocess.Popen([exe_path] + sys.argv[1:])
        sys.exit(0)
    if getattr(sys, 'frozen', False):
        subprocess.Popen([sys.executable] + sys.argv[1:])
    else:
        subprocess.Popen([sys.executable] + sys.argv)
    sys.exit(0)


def _show_update_ui() -> bool:
    """업데이트 확인 + 진행 팝업창 (tkinter) 표시.

    현재 버전 표시 → GitHub 릴리스 조회 → 업데이트 있으면 프로그레스바로 진행.
    Returns True if updated (agent will restart), False to continue running.
    """
    try:
        import tkinter as tk
        from tkinter import ttk

        try:
            from version import __version__, __github_repo__, __asset_name__
        except ImportError:
            __version__ = "0.0.0"
            __github_repo__ = "hy0567/wellcom_soft"
            __asset_name__ = "agent.zip"

        result = {'updated': False}

        root = tk.Tk()
        root.title("WellcomAgent 업데이터")
        W, H = 420, 200
        root.geometry(f"{W}x{H}")
        root.resizable(False, False)
        root.configure(bg='#1e1e1e')
        root.attributes('-topmost', True)
        root.update_idletasks()
        sx = (root.winfo_screenwidth() - W) // 2
        sy = (root.winfo_screenheight() - H) // 2
        root.geometry(f"{W}x{H}+{sx}+{sy}")

        # 타이틀
        tk.Label(root, text=f"WellcomAgent v{__version__}",
                 bg='#1e1e1e', fg='#ffffff',
                 font=('Segoe UI', 14, 'bold')).pack(pady=(22, 4))

        # 상태 메시지
        status_var = tk.StringVar(value="업데이트 확인 중...")
        tk.Label(root, textvariable=status_var,
                 bg='#1e1e1e', fg='#aaaaaa',
                 font=('Segoe UI', 10)).pack()

        # 프로그레스바
        sty = ttk.Style()
        sty.theme_use('default')
        sty.configure("W.Horizontal.TProgressbar",
                      background='#4CAF50', troughcolor='#333333', thickness=10)
        pb = ttk.Progressbar(root, style="W.Horizontal.TProgressbar",
                             orient='horizontal', length=380, mode='indeterminate')
        pb.pack(pady=12)
        pb.start(12)

        # 퍼센트/크기 표시
        pct_var = tk.StringVar(value="")
        tk.Label(root, textvariable=pct_var,
                 bg='#1e1e1e', fg='#888888',
                 font=('Segoe UI', 9)).pack()

        # ── UI 업데이트 헬퍼 ──────────────────────────────
        def _set_status(msg):
            root.after(0, lambda: status_var.set(msg))

        def _set_pct(msg):
            root.after(0, lambda: pct_var.set(msg))

        def _to_determinate(val=0):
            def _do():
                pb.stop()
                pb.configure(mode='determinate', value=val)
            root.after(0, _do)

        def _set_pb(val):
            root.after(0, lambda: pb.configure(value=val))

        def _close_after(ms):
            root.after(ms, root.destroy)

        # ── 백그라운드 업데이트 로직 ─────────────────────
        def _run():
            try:
                from pathlib import Path
                _proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if _proj not in sys.path:
                    sys.path.insert(0, _proj)
                from updater import UpdateChecker

                checker = UpdateChecker(
                    Path(AGENT_BASE_DIR), __github_repo__,
                    asset_name=__asset_name__,
                    running_version=__version__,
                )

                _set_status("GitHub에서 릴리스 정보 조회 중...")
                has_update, release = checker.check_update()

                if not has_update:
                    _to_determinate(100)
                    _set_status(f"✓ 최신 버전입니다 (v{__version__})")
                    _close_after(1500)
                    return

                # 업데이트 발견
                new_ver = release.version
                _to_determinate(0)
                _set_status(f"업데이트 발견: v{__version__} → v{new_ver}")
                time.sleep(0.8)

                _set_status(f"v{new_ver} 다운로드 중...")

                def _on_progress(downloaded, total):
                    if total > 0:
                        pct = int(downloaded * 100 / total)
                        mb_d = downloaded / (1024 * 1024)
                        mb_t = total / (1024 * 1024)
                        def _upd(p=pct, d=mb_d, t=mb_t):
                            pb.configure(value=p)
                            pct_var.set(f"{p}%  ({d:.1f} / {t:.1f} MB)")
                        root.after(0, _upd)

                success = checker.apply_update(release, progress_callback=_on_progress)

                if success:
                    result['updated'] = True

                    def _done():
                        pb.configure(value=100)
                        status_var.set("✓ 업데이트 완료 — 재시작 중...")
                        pct_var.set("")
                    root.after(0, _done)
                    _close_after(1200)
                else:
                    def _fail():
                        status_var.set("업데이트 실패 — 현재 버전으로 계속 실행")
                        pct_var.set("")
                    root.after(0, _fail)
                    _close_after(2500)

            except Exception as e:
                def _err():
                    status_var.set("업데이트 확인 건너뜀")
                    pct_var.set("")
                root.after(0, _err)
                logger.debug(f"업데이트 UI 오류: {e}")
                _close_after(2000)

        threading.Thread(target=_run, daemon=True).start()
        root.mainloop()
        return result['updated']

    except Exception as e:
        logger.debug(f"업데이트 UI 불가 ({e}) — 무음 모드")
        return _check_and_apply_update()

# 바이너리 프레임 헤더
HEADER_THUMBNAIL = 0x01
HEADER_STREAM = 0x02


def _get_public_ip() -> str:
    """공인IP 조회 (LinkIO의 Ip1)"""
    for url in ['https://api.ipify.org', 'https://ifconfig.me/ip',
                'https://icanhazip.com', 'https://checkip.amazonaws.com']:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                ip = r.text.strip()
                if ip and '.' in ip:
                    return ip
        except Exception:
            continue
    return ''


class AgentAPIClient:
    """에이전트 전용 경량 REST API 클라이언트"""

    def __init__(self, config: AgentConfig):
        self.config = config
        self._token = config.api_token

    @property
    def token(self) -> str:
        return self._token

    def _headers(self) -> dict:
        h = {'Content-Type': 'application/json'}
        if self._token:
            h['Authorization'] = f'Bearer {self._token}'
        return h

    def login(self, username: str, password: str) -> bool:
        """서버 로그인 → JWT 토큰 획득"""
        try:
            r = requests.post(
                f'{self.config.api_url}/api/auth/login',
                json={'username': username, 'password': password},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            self._token = data['token']
            self.config.set('api_token', self._token)
            self.config.set('api_username', username)
            logger.info(f"서버 로그인 성공: {username}")
            return True
        except Exception as e:
            logger.error(f"서버 로그인 실패: {e}")
            return False

    def verify_token(self) -> bool:
        """저장된 토큰 유효성 확인"""
        if not self._token:
            return False
        try:
            r = requests.get(
                f'{self.config.api_url}/api/auth/me',
                headers=self._headers(),
                timeout=10,
            )
            return r.status_code == 200
        except Exception:
            return False

    def register_agent(self, agent_id: str, hostname: str,
                       os_info: str, ip: str,
                       ip_public: str = '', ws_port: int = 21350,
                       mac_address: str = '',
                       screen_width: int = 1920,
                       screen_height: int = 1080,
                       agent_version: str = '',
                       cpu_model: str = '',
                       cpu_cores: int = 0,
                       ram_gb: float = 0.0,
                       motherboard: str = '',
                       gpu_model: str = '') -> bool:
        """에이전트 자신을 서버에 등록 (ip_public, ws_port, agent_version, 하드웨어 정보 포함)"""
        try:
            r = requests.post(
                f'{self.config.api_url}/api/agents/register',
                json={
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
                },
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            logger.info(f"에이전트 등록 성공: {agent_id} (ip={ip}, ip_public={ip_public}, ws_port={ws_port}, v{agent_version}, cpu={cpu_model})")
            return True
        except Exception as e:
            logger.error(f"에이전트 등록 실패: {e}")
            return False

    def send_heartbeat(self, agent_id: str, ip: str,
                       ip_public: str = '', ws_port: int = 21350,
                       screen_width: int = 1920,
                       screen_height: int = 1080,
                       agent_version: str = ''):
        """하트비트 전송"""
        try:
            requests.post(
                f'{self.config.api_url}/api/agents/heartbeat',
                json={
                    'agent_id': agent_id,
                    'ip': ip,
                    'ip_public': ip_public,
                    'ws_port': ws_port,
                    'screen_width': screen_width,
                    'screen_height': screen_height,
                    'agent_version': agent_version,
                },
                headers=self._headers(),
                timeout=10,
            )
        except Exception as e:
            logger.debug(f"하트비트 전송 실패: {e}")

    def report_offline(self, agent_id: str):
        """오프라인 보고"""
        try:
            requests.post(
                f'{self.config.api_url}/api/agents/offline',
                json={'agent_id': agent_id},
                headers=self._headers(),
                timeout=5,
            )
        except Exception:
            pass


def _get_local_ip() -> str:
    """로컬 IP 주소 조회"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def _get_mac_address() -> str:
    """MAC 주소 조회"""
    try:
        import uuid
        mac = uuid.getnode()
        return ':'.join(f'{(mac >> (8 * i)) & 0xFF:02x}' for i in reversed(range(6)))
    except Exception:
        return ''


class WellcomAgent:
    """트레이 아이콘 + 서버 등록 + WS 서버(P2P) + 화면 캡처 + 입력 주입

    v3.0.0: 에이전트가 WS 서버로 동작 (LinkIO 방식)
    - 매니저가 에이전트에 직접 WS 접속
    - 다중 매니저 동시 접속 지원
    - 서버는 REST API만 (등록/조회)
    """

    def __init__(self):
        self.config = AgentConfig()
        self.screen_capture = ScreenCapture()
        self.input_handler = InputHandler()
        self.clipboard = ClipboardMonitor()
        self.file_receiver = FileReceiver(self.config.save_dir)
        self.api_client: Optional[AgentAPIClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tray_thread = None
        self._heartbeat_thread = None
        self._running = True
        self._agent_id = socket.gethostname()
        self._local_ip = _get_local_ip()
        self._public_ip = ''
        self._mac_address = _get_mac_address()
        self._ws_port = self.config.ws_port or 21350
        self._agent_version = ""  # start()에서 version.py 로드

        # UPnP
        self._upnp = None

        # 연결 상태 플래그 (트레이 아이콘 동적 업데이트용)
        self._relay_connected = False       # 서버 릴레이 연결 여부
        self._server_logged_in = False      # 서버 로그인 성공 여부
        self._tray_icon_obj = None          # pystray Icon 객체 참조

        # 다중 매니저 관리
        self._managers: Dict[str, object] = {}       # manager_id → websocket
        self._stream_tasks: Dict[str, asyncio.Task] = {}
        self._thumbnail_tasks: Dict[str, asyncio.Task] = {}
        self._stream_settings: Dict[str, dict] = {}  # manager_id → {fps, quality}

    def _get_system_info(self) -> dict:
        """시스템 정보 수집"""
        return {
            'hostname': socket.gethostname(),
            'os_info': f"{platform.system()} {platform.release()} {platform.version()}",
            'agent_id': self._agent_id,
            'ip': self._local_ip,
            'ip_public': self._public_ip,
            'mac_address': self._mac_address,
        }

    def _get_hardware_info(self) -> dict:
        """하드웨어 정보 수집 (CPU/RAM/MB/GPU)"""
        info = {'cpu_model': '', 'cpu_cores': 0, 'ram_gb': 0.0,
                'motherboard': '', 'gpu_model': ''}
        try:
            import psutil
            info['cpu_cores'] = psutil.cpu_count(logical=False) or 0
            info['ram_gb'] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        except Exception:
            pass
        try:
            info['cpu_model'] = platform.processor()
        except Exception:
            pass
        if platform.system() == 'Windows':
            try:
                r = subprocess.run(
                    ['wmic', 'baseboard', 'get', 'Manufacturer,Product'],
                    capture_output=True, text=True, timeout=5,
                    encoding='utf-8', errors='replace'
                )
                lines = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
                if len(lines) >= 2:
                    info['motherboard'] = lines[1]
            except Exception:
                pass
            try:
                r = subprocess.run(
                    ['wmic', 'path', 'win32_VideoController', 'get', 'Name'],
                    capture_output=True, text=True, timeout=5,
                    encoding='utf-8', errors='replace'
                )
                lines = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
                if len(lines) >= 2:
                    info['gpu_model'] = lines[1]
            except Exception:
                pass
        return info

    def _setup_upnp(self) -> bool:
        """UPnP로 WS 포트 자동 개방"""
        try:
            import miniupnpc
            upnp = miniupnpc.UPnP()
            upnp.discoverdelay = 200
            discovered = upnp.discover()
            if not discovered:
                logger.info("[UPnP] 라우터 발견 실패 — 릴레이로 폴백")
                return False
            upnp.selectigd()
            local_ip = self._local_ip
            upnp.addportmapping(
                self._ws_port, 'TCP', local_ip, self._ws_port,
                'WellcomAgent', ''
            )
            external_ip = upnp.externalipaddress()
            if external_ip:
                self._public_ip = external_ip
            self._upnp = upnp
            logger.info(f"[UPnP] 포트 {self._ws_port} 개방 성공, 공인IP: {external_ip}")
            return True
        except ImportError:
            logger.debug("[UPnP] miniupnpc 미설치 — 릴레이로 폴백")
            return False
        except Exception as e:
            logger.info(f"[UPnP] 포트 개방 실패 ({e}) — 릴레이로 폴백")
            return False

    def _cleanup_upnp(self):
        """UPnP 포트 매핑 제거"""
        if self._upnp:
            try:
                self._upnp.deleteportmapping(self._ws_port, 'TCP')
                logger.info(f"[UPnP] 포트 {self._ws_port} 매핑 제거")
            except Exception:
                pass
            self._upnp = None

    @staticmethod
    def _add_firewall_rule(ws_port: int):
        """Windows 방화벽 TCP 인바운드 규칙 추가 (포트 21350 직접 P2P용)"""
        if platform.system() != 'Windows':
            return
        rule_name = 'WellcomAgent'
        try:
            chk = subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'show', 'rule', f'name={rule_name}'],
                capture_output=True, text=True, timeout=5,
                encoding='utf-8', errors='replace',
            )
            if rule_name in chk.stdout:
                logger.debug(f"[Firewall] 규칙 이미 존재: {rule_name}")
                return
            res = subprocess.run(
                ['netsh', 'advfirewall', 'firewall', 'add', 'rule',
                 f'name={rule_name}', 'protocol=TCP', 'dir=in',
                 f'localport={ws_port}', 'action=allow',
                 'description=WellcomSOFT Agent P2P Port'],
                capture_output=True, text=True, timeout=5,
                encoding='utf-8', errors='replace',
            )
            if res.returncode == 0:
                logger.info(f"[Firewall] 포트 {ws_port} TCP 인바운드 규칙 추가")
            else:
                logger.info("[Firewall] 규칙 추가 실패 (관리자 권한 필요) — 릴레이 폴백 사용")
        except Exception as e:
            logger.debug(f"[Firewall] 오류: {e}")

    async def _relay_outbound_loop(self):
        """서버 릴레이 아웃바운드 연결 유지 (포트 개방 불필요 폴백)

        에이전트가 서버에 먼저 연결 → 매니저가 서버 릴레이를 통해 에이전트 제어.
        TeamViewer/AnyDesk 방식 — 어떤 NAT/방화벽에서도 동작.
        """
        if not self.api_client or not self.api_client.token:
            return

        base = self.config.api_url
        if base.startswith('https://'):
            base = 'wss://' + base[8:]
        elif base.startswith('http://'):
            base = 'ws://' + base[7:]
        elif not base.startswith(('ws://', 'wss://')):
            base = 'ws://' + base
        ws_url = f"{base}/ws/agent?token={self.api_client.token}"

        while self._running:
            try:
                logger.info(f"[Relay] 서버 릴레이 아웃바운드 연결: {base}/ws/agent")
                async with websockets.connect(
                    ws_url,
                    max_size=50 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    # 핸드셰이크
                    await ws.send(json.dumps({
                        'type': 'agent_hello',
                        'agent_id': self._agent_id,
                    }))
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    resp = json.loads(raw)
                    if resp.get('type') != 'relay_ok':
                        logger.warning(f"[Relay] 핸드셰이크 실패: {resp}")
                        break

                    logger.info("[Relay] 서버 릴레이 연결 성공 — 매니저 폴백 대기 중")
                    self._relay_connected = True
                    self._update_tray_icon()

                    # 매니저 메시지 처리 루프 (서버가 매니저의 메시지를 에이전트에게 전달)
                    async for message in ws:
                        if not self._running:
                            break
                        if isinstance(message, str):
                            await self._handle_text(ws, message, 'relay')
                        elif isinstance(message, bytes):
                            await self._handle_binary(ws, message, 'relay')

            except asyncio.CancelledError:
                self._relay_connected = False
                self._update_tray_icon()
                return
            except Exception as e:
                if self._running:
                    logger.warning(f"[Relay] 연결 끊김: {type(e).__name__} — 30초 후 재연결")
            self._relay_connected = False
            self._update_tray_icon()
            if self._running:
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    return

    @staticmethod
    def _ask_server_config(current_url: str = '') -> tuple:
        """서버 설정 통합 다이얼로그 (API URL + 로그인 — 단일 창)

        Returns: (api_url, username, password)
        """
        try:
            import tkinter as tk

            result = {'api_url': '', 'username': '', 'password': ''}

            root = tk.Tk()
            root.title("WellcomAgent 서버 설정")
            W, H = 380, 290
            root.geometry(f"{W}x{H}")
            root.resizable(False, False)
            root.configure(bg='#1e1e1e')
            root.attributes('-topmost', True)
            root.update_idletasks()
            sx = (root.winfo_screenwidth() - W) // 2
            sy = (root.winfo_screenheight() - H) // 2
            root.geometry(f"{W}x{H}+{sx}+{sy}")

            tk.Label(root, text="WellcomAgent 서버 설정",
                     bg='#1e1e1e', fg='#ffffff',
                     font=('Segoe UI', 12, 'bold')).pack(pady=(18, 14))

            entry_style = {
                'bg': '#2d2d2d', 'fg': '#ffffff',
                'insertbackground': 'white', 'relief': 'flat',
                'font': ('Segoe UI', 10), 'bd': 0,
                'highlightthickness': 1,
                'highlightbackground': '#444', 'highlightcolor': '#4CAF50',
            }

            def _add_field(label_text, default='', show=''):
                frm = tk.Frame(root, bg='#1e1e1e')
                frm.pack(fill='x', padx=28, pady=(0, 10))
                tk.Label(frm, text=label_text, bg='#1e1e1e', fg='#aaaaaa',
                         font=('Segoe UI', 9), anchor='w').pack(fill='x')
                e = tk.Entry(frm, show=show, **entry_style)
                e.pack(fill='x', ipady=6)
                if default:
                    e.insert(0, default)
                return e

            url_e = _add_field("서버 API 주소",
                               current_url or 'http://log.wellcomll.org:8000')
            usr_e = _add_field("사용자 이름")
            pwd_e = _add_field("비밀번호", show='*')

            # 버튼
            btn_frm = tk.Frame(root, bg='#1e1e1e')
            btn_frm.pack(fill='x', padx=28, pady=(6, 0))

            def _ok(event=None):
                result['api_url'] = url_e.get().strip()
                result['username'] = usr_e.get().strip()
                result['password'] = pwd_e.get()
                root.destroy()

            def _cancel():
                root.destroy()

            root.bind('<Return>', _ok)
            root.bind('<Escape>', lambda e: _cancel())

            btn_opts = {'font': ('Segoe UI', 10), 'bd': 0, 'relief': 'flat',
                        'padx': 16, 'pady': 6, 'cursor': 'hand2'}
            tk.Button(btn_frm, text="취소", bg='#3e3e3e', fg='#aaaaaa',
                      command=_cancel, **btn_opts).pack(side='left')
            tk.Button(btn_frm, text="연결", bg='#4CAF50', fg='white',
                      command=_ok, **btn_opts).pack(side='right')

            usr_e.focus_set()
            root.mainloop()
            return result['api_url'], result['username'], result['password']

        except Exception as e:
            logger.error(f"서버 설정 창 오류: {e}")
            return '', '', ''

    def _server_login(self) -> bool:
        """서버에 로그인"""
        self.api_client = AgentAPIClient(self.config)

        # 저장된 토큰이 유효하면 바로 통과
        if self.config.api_url and self.config.api_token:
            if self.api_client.verify_token():
                logger.info("저장된 토큰으로 인증 성공")
                return True

        # 설정 다이얼로그 (API URL + 로그인 정보 통합)
        api_url, username, password = self._ask_server_config(
            self.config.api_url or ''
        )
        if not api_url:
            return False

        # API URL 변경 시 설정 저장 + 클라이언트 재생성
        if api_url != self.config.api_url:
            self.config.set('api_url', api_url)
            self.api_client = AgentAPIClient(self.config)

        if not username or not password:
            return False

        return self.api_client.login(username, password)

    def _register_self(self):
        """서버에 에이전트 자신을 등록 (ip_public, ws_port, 하드웨어 정보 포함)"""
        if not self.api_client:
            return

        sys_info = self._get_system_info()
        hw_info = self._get_hardware_info()
        screen_w, screen_h = self.screen_capture.screen_size

        self.api_client.register_agent(
            agent_id=sys_info['agent_id'],
            hostname=sys_info['hostname'],
            os_info=sys_info['os_info'],
            ip=sys_info['ip'],
            ip_public=self._public_ip,
            ws_port=self._ws_port,
            mac_address=sys_info['mac_address'],
            screen_width=screen_w,
            screen_height=screen_h,
            agent_version=self._agent_version,
            cpu_model=hw_info['cpu_model'],
            cpu_cores=hw_info['cpu_cores'],
            ram_gb=hw_info['ram_gb'],
            motherboard=hw_info['motherboard'],
            gpu_model=hw_info['gpu_model'],
        )

    def _heartbeat_loop(self):
        """하트비트 스레드"""
        interval = self.config.heartbeat_interval
        screen_w, screen_h = self.screen_capture.screen_size

        while self._running:
            time.sleep(interval)
            if not self._running:
                break
            if self.api_client:
                self.api_client.send_heartbeat(
                    self._agent_id, self._local_ip,
                    ip_public=self._public_ip,
                    ws_port=self._ws_port,
                    screen_width=screen_w,
                    screen_height=screen_h,
                    agent_version=self._agent_version,
                )

    def _verify_token(self, token: str) -> bool:
        """매니저의 JWT 토큰 검증 (서버 API 호출)"""
        if not self.config.api_url:
            return True  # 서버 미설정 시 인증 스킵
        try:
            r = requests.get(
                f'{self.config.api_url}/api/auth/me',
                headers={'Authorization': f'Bearer {token}'},
                timeout=5,
            )
            return r.status_code == 200
        except Exception:
            return False

    def start(self):
        """에이전트 시작"""
        # 버전 정보 로드
        try:
            from version import __version__ as _ver
        except ImportError:
            _ver = "0.0.0"
        self._agent_version = _ver
        logger.info(f"★ WellcomSOFT Agent v{_ver} (P2P WS 서버 모드)")

        # 0. 업데이트 확인 팝업 (버전 표시 + 프로그레스바)
        if _show_update_ui():
            _restart_agent()
            return  # 업데이트 후 재시작됨

        # 1. 공인IP 조회
        self._public_ip = _get_public_ip()
        logger.info(f"사설IP: {self._local_ip}, 공인IP: {self._public_ip or '조회 실패'}")

        # 2. UPnP 포트 자동 개방 (성공 시 공인IP 갱신)
        self._setup_upnp()

        # 2-b. Windows 방화벽 인바운드 규칙 추가 (P2P LAN/WAN용)
        self._add_firewall_rule(self._ws_port)

        # 3. 서버 로그인 + 등록
        if self.config.api_url:
            if not self._server_login():
                logger.warning("서버 로그인 실패 — WS 서버만 시작")
                self._server_logged_in = False
            else:
                self._server_logged_in = True
                self._register_self()

                # 하트비트 시작
                self._heartbeat_thread = threading.Thread(
                    target=self._heartbeat_loop, daemon=True, name='Heartbeat'
                )
                self._heartbeat_thread.start()

        # 4. 클립보드 감시
        if self.config.clipboard_sync:
            self.clipboard.start_monitoring(self._on_clipboard_changed)

        # 5. 트레이 아이콘
        self._tray_thread = threading.Thread(
            target=self._run_tray, daemon=True, name='TrayIcon'
        )
        self._tray_thread.start()

        # 6. WS 서버 시작 (P2P — 매니저 접속 대기)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_server())
        except KeyboardInterrupt:
            logger.info("Ctrl+C — 종료")
        finally:
            if self.api_client:
                self.api_client.report_offline(self._agent_id)
            self._cleanup_upnp()
            self.clipboard.stop_monitoring()
            self.screen_capture.close()

    async def _run_server(self):
        """WS 서버 시작 (직접 P2P) + 서버 릴레이 아웃바운드 (폴백) — 병렬 실행"""
        relay_task = None
        try:
            async with websockets.serve(
                self._handle_manager,
                '0.0.0.0', self._ws_port,
                max_size=50 * 1024 * 1024,
                ping_interval=20,
                ping_timeout=20,
            ):
                logger.info(f"★ WS 서버 시작: 0.0.0.0:{self._ws_port} (P2P 직접 + 릴레이 폴백)")
                # 서버 릴레이 아웃바운드 연결 병렬 시작
                if self.api_client and self.api_client.token and self.config.api_url:
                    relay_task = asyncio.create_task(self._relay_outbound_loop())
                while self._running:
                    await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"WS 서버 오류: {e}")
        finally:
            if relay_task and not relay_task.done():
                relay_task.cancel()
                try:
                    await relay_task
                except asyncio.CancelledError:
                    pass

    async def _handle_manager(self, websocket):
        """매니저 연결 핸들러"""
        remote = websocket.remote_address
        remote_ip = remote[0] if remote else 'unknown'
        manager_id = None

        try:
            # 1. 인증 핸드셰이크
            raw = await asyncio.wait_for(websocket.recv(), timeout=10)
            msg = json.loads(raw)

            if msg.get('type') != 'auth':
                await websocket.close(4003, 'Expected auth')
                return

            token = msg.get('token', '')
            manager_id = msg.get('manager_id', remote_ip)

            # 2. JWT 토큰 검증
            if not self._verify_token(token):
                await websocket.send(json.dumps({'type': 'auth_fail'}))
                await websocket.close(4001, 'Invalid token')
                logger.warning(f"매니저 인증 실패: {manager_id} ({remote_ip})")
                return

            # 3. 기존 같은 manager_id 연결 교체
            old_ws = self._managers.get(manager_id)
            if old_ws:
                try:
                    await old_ws.close()
                except Exception:
                    pass

            # 4. 연결 수락
            self._managers[manager_id] = websocket
            self._update_tray_icon()
            screen_w, screen_h = self.screen_capture.screen_size
            await websocket.send(json.dumps({
                'type': 'auth_ok',
                'agent_id': self._agent_id,
                'hostname': socket.gethostname(),
                'os_info': f"{platform.system()} {platform.release()} {platform.version()}",
                'screen_width': screen_w,
                'screen_height': screen_h,
            }))
            logger.info(f"매니저 연결: {manager_id} ({remote_ip})")

            # 5. 메시지 수신 루프
            async for message in websocket:
                if not self._running:
                    break
                if isinstance(message, str):
                    await self._handle_text(websocket, message, manager_id)
                elif isinstance(message, bytes):
                    await self._handle_binary(websocket, message, manager_id)

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"매니저 연결 종료: {manager_id or remote_ip}")
        except asyncio.TimeoutError:
            logger.warning(f"매니저 인증 타임아웃: {remote_ip}")
        except Exception as e:
            logger.warning(f"매니저 핸들러 오류 [{manager_id or remote_ip}]: {e}")
        finally:
            if manager_id:
                self._managers.pop(manager_id, None)
                self._update_tray_icon()
                # 해당 매니저의 스트림/썸네일 태스크 정리
                task = self._stream_tasks.pop(manager_id, None)
                if task:
                    task.cancel()
                task = self._thumbnail_tasks.pop(manager_id, None)
                if task:
                    task.cancel()
                self._stream_settings.pop(manager_id, None)
                logger.info(f"매니저 해제: {manager_id}")

    async def _handle_text(self, websocket, raw: str, manager_id: str):
        """JSON 텍스트 메시지 처리"""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get('type', '')

        if msg_type == 'ping':
            await websocket.send(json.dumps({'type': 'pong'}))

        elif msg_type == 'request_thumbnail':
            await self._send_thumbnail(websocket)

        elif msg_type == 'start_stream':
            fps = msg.get('fps', self.config.screen_fps)
            quality = msg.get('quality', self.config.screen_quality)
            # 기존 스트림 태스크 취소
            old_task = self._stream_tasks.get(manager_id)
            if old_task:
                old_task.cancel()
            self._stream_settings[manager_id] = {'fps': fps, 'quality': quality}
            task = asyncio.create_task(self._start_streaming(websocket, fps, quality, manager_id))
            self._stream_tasks[manager_id] = task

        elif msg_type == 'update_stream':
            fps = msg.get('fps')
            quality = msg.get('quality')
            settings = self._stream_settings.get(manager_id, {})
            if fps is not None:
                settings['fps'] = fps
            if quality is not None:
                settings['quality'] = quality
            self._stream_settings[manager_id] = settings

        elif msg_type == 'stop_stream':
            task = self._stream_tasks.pop(manager_id, None)
            if task:
                task.cancel()
            self._stream_settings.pop(manager_id, None)

        elif msg_type == 'start_thumbnail_push':
            interval = msg.get('interval', 1.0)
            old_task = self._thumbnail_tasks.get(manager_id)
            if old_task:
                old_task.cancel()
            task = asyncio.create_task(self._start_thumbnail_push(websocket, interval, manager_id))
            self._thumbnail_tasks[manager_id] = task

        elif msg_type == 'stop_thumbnail_push':
            task = self._thumbnail_tasks.pop(manager_id, None)
            if task:
                task.cancel()

        elif msg_type == 'key_event':
            self.input_handler.handle_key_event(
                key=msg.get('key', ''),
                action=msg.get('action', 'press'),
                modifiers=msg.get('modifiers', []),
            )

        elif msg_type == 'mouse_event':
            self.input_handler.handle_mouse_event(
                x=msg.get('x', 0),
                y=msg.get('y', 0),
                button=msg.get('button', 'none'),
                action=msg.get('action', 'move'),
                scroll_delta=msg.get('scroll_delta', 0),
            )

        elif msg_type == 'special_key':
            combo = msg.get('combo', '')
            self.input_handler.handle_special_key(combo)

        elif msg_type == 'clipboard':
            await self._handle_clipboard_msg(msg)

        elif msg_type == 'file_start':
            name = msg.get('name', 'unknown')
            size = msg.get('size', 0)
            ok = self.file_receiver.begin_file(name, size)
            await websocket.send(json.dumps({
                'type': 'file_ack',
                'status': 'ready' if ok else 'error'
            }))

        elif msg_type == 'file_end':
            path = self.file_receiver.finish_file()
            await websocket.send(json.dumps({
                'type': 'file_complete',
                'path': path or '',
                'status': 'ok' if path else 'error'
            }))

        elif msg_type == 'get_clipboard':
            fmt, data = self.clipboard.get_clipboard()
            if fmt == 'text':
                await websocket.send(json.dumps({
                    'type': 'clipboard',
                    'format': 'text',
                    'data': data,
                }))
            elif fmt == 'image':
                await websocket.send(json.dumps({
                    'type': 'clipboard',
                    'format': 'image',
                    'data': base64.b64encode(data).decode('ascii'),
                }))

        elif msg_type == 'execute':
            command = msg.get('command', '')
            await self._execute_command(websocket, command)

        elif msg_type == 'update_request':
            # 매니저가 업데이트 명령 전송 → 백그라운드 스레드에서 헤드리스 업데이트
            await websocket.send(json.dumps({'type': 'update_started'}))
            threading.Thread(
                target=_check_and_apply_update,
                daemon=True, name='UpdateWorker'
            ).start()

    async def _handle_binary(self, websocket, data: bytes, manager_id: str):
        """바이너리 프레임 처리 (파일 청크)"""
        if self.file_receiver.is_receiving:
            received = self.file_receiver.write_chunk(data)
            await websocket.send(json.dumps({
                'type': 'file_progress',
                'received': received,
                'total': self.file_receiver._current_size,
            }))

    async def _send_thumbnail(self, websocket):
        """썸네일 캡처 및 전송"""
        try:
            jpeg_data = self.screen_capture.capture_thumbnail(
                max_width=self.config.thumbnail_width,
                quality=self.config.thumbnail_quality,
            )
            await websocket.send(bytes([HEADER_THUMBNAIL]) + jpeg_data)
        except Exception as e:
            logger.debug(f"썸네일 전송 실패: {e}")

    async def _start_thumbnail_push(self, websocket, interval: float, manager_id: str):
        """썸네일 push 모드 — 주기적으로 자동 전송"""
        interval = max(0.2, min(interval, 5.0))
        logger.info(f"[{manager_id}] 썸네일 push 시작: {interval}초")

        try:
            while self._running:
                try:
                    jpeg_data = self.screen_capture.capture_thumbnail(
                        max_width=self.config.thumbnail_width,
                        quality=self.config.thumbnail_quality,
                    )
                    await websocket.send(bytes([HEADER_THUMBNAIL]) + jpeg_data)
                except websockets.exceptions.ConnectionClosed:
                    break
                except Exception as e:
                    logger.debug(f"push 썸네일 전송 실패: {e}")
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info(f"[{manager_id}] 썸네일 push 중지")

    async def _start_streaming(self, websocket, fps: int, quality: int, manager_id: str):
        """화면 스트리밍 시작 (매니저별 독립)"""
        interval = 1.0 / max(1, fps)
        logger.info(f"[{manager_id}] 스트리밍 시작: {fps}fps, quality={quality}")

        try:
            while self._running:
                settings = self._stream_settings.get(manager_id, {})
                cur_quality = settings.get('quality', quality)
                cur_fps = settings.get('fps', fps)
                cur_interval = 1.0 / max(1, cur_fps)

                jpeg_data = self.screen_capture.capture_jpeg(quality=cur_quality)
                await websocket.send(bytes([HEADER_STREAM]) + jpeg_data)
                await asyncio.sleep(cur_interval)
        except asyncio.CancelledError:
            pass
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.debug(f"[{manager_id}] 스트리밍 오류: {e}")
        finally:
            self._stream_tasks.pop(manager_id, None)
            self._stream_settings.pop(manager_id, None)
            logger.info(f"[{manager_id}] 스트리밍 중지")

    async def _execute_command(self, websocket, command: str):
        """원격 명령 실행"""
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True,
                text=True, timeout=30, encoding='utf-8', errors='replace'
            )
            await websocket.send(json.dumps({
                'type': 'execute_result',
                'command': command,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'returncode': result.returncode,
            }))
        except subprocess.TimeoutExpired:
            await websocket.send(json.dumps({
                'type': 'execute_result',
                'command': command,
                'stdout': '',
                'stderr': '명령 실행 타임아웃 (30초)',
                'returncode': -1,
            }))
        except Exception as e:
            await websocket.send(json.dumps({
                'type': 'execute_result',
                'command': command,
                'stdout': '',
                'stderr': str(e),
                'returncode': -1,
            }))

    async def _handle_clipboard_msg(self, msg: dict):
        """클립보드 메시지 수신"""
        fmt = msg.get('format', '')
        data = msg.get('data', '')

        if fmt == 'text' and data:
            self.clipboard.set_clipboard_text(data)
        elif fmt == 'image' and data:
            png_data = base64.b64decode(data)
            self.clipboard.set_clipboard_image(png_data)

    def _on_clipboard_changed(self, fmt: str, data):
        """로컬 클립보드 변경 → 모든 연결된 매니저에 브로드캐스트"""
        if not self._managers:
            return

        if fmt == 'text':
            msg = json.dumps({
                'type': 'clipboard',
                'format': 'text',
                'data': data,
            })
        elif fmt == 'image':
            msg = json.dumps({
                'type': 'clipboard',
                'format': 'image',
                'data': base64.b64encode(data).decode('ascii'),
            })
        else:
            return

        if self._loop and self._loop.is_running():
            for ws in list(self._managers.values()):
                try:
                    asyncio.run_coroutine_threadsafe(ws.send(msg), self._loop)
                except Exception:
                    pass

    def _make_tray_icon_image(self, color_rgb: tuple) -> 'Image':
        """상태 색상에 맞는 트레이 아이콘 이미지 생성"""
        from PIL import Image, ImageDraw
        img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        r, g, b = color_rgb
        draw.ellipse([4, 4, 60, 60], fill=(r, g, b, 255))
        # 'W' 문자
        draw.line([(14, 16), (22, 48), (32, 28), (42, 48), (50, 16)],
                  fill=(255, 255, 255, 255), width=5)
        return img

    def _get_tray_status(self) -> tuple:
        """(색상RGB, 툴팁문자열) — 현재 연결 상태에 맞는 값 반환"""
        n = len(self._managers)
        if n > 0:
            # 매니저가 직접 연결 중 → 초록
            return (34, 197, 94), f"WellcomAgent — 연결 중 (매니저 {n}개)"
        elif self._relay_connected:
            # 릴레이 연결됨, 매니저 대기 중 → 파랑
            return (33, 150, 243), f"WellcomAgent — 릴레이 대기 중 (포트 {self._ws_port})"
        elif self._server_logged_in:
            # 서버 로그인됨, 릴레이 미연결 → 노랑
            return (234, 179, 8), f"WellcomAgent — 서버 연결 중 (포트 {self._ws_port})"
        else:
            # 서버 미연결 → 회색
            return (120, 120, 120), f"WellcomAgent — 오프라인 (포트 {self._ws_port})"

    def _update_tray_icon(self):
        """트레이 아이콘 색상 및 툴팁 동적 업데이트"""
        try:
            if self._tray_icon_obj is None:
                return
            color, tooltip = self._get_tray_status()
            self._tray_icon_obj.icon = self._make_tray_icon_image(color)
            self._tray_icon_obj.title = tooltip
        except Exception:
            pass

    def _run_tray(self):
        """시스템 트레이 아이콘 (LinkIO 스타일 — 동적 상태 표시)"""
        try:
            import pystray
            from PIL import Image, ImageDraw

            def on_quit(icon, item):
                self._running = False
                if self.api_client:
                    self.api_client.report_offline(self._agent_id)
                icon.stop()
                os._exit(0)

            def on_show_status(icon, item):
                """연결 상태 팝업 (tkinter)"""
                try:
                    import tkinter as tk
                    from tkinter import ttk

                    root = tk.Tk()
                    root.title("WellcomAgent 상태")
                    root.configure(bg='#1e1e1e')
                    root.resizable(False, False)
                    root.attributes('-topmost', True)
                    W, H = 380, 300
                    sw = root.winfo_screenwidth()
                    sh = root.winfo_screenheight()
                    root.geometry(f"{W}x{H}+{sw - W - 20}+{sh - H - 60}")

                    tk.Label(root, text="WellcomAgent 연결 상태",
                             font=('맑은 고딕', 12, 'bold'),
                             bg='#1e1e1e', fg='#ffffff').pack(pady=(15, 5))

                    ttk.Separator(root).pack(fill='x', padx=15)

                    frame = tk.Frame(root, bg='#1e1e1e')
                    frame.pack(fill='both', expand=True, padx=20, pady=10)

                    n = len(self._managers)
                    color, _ = self._get_tray_status()
                    hex_color = f'#{color[0]:02x}{color[1]:02x}{color[2]:02x}'

                    rows = [
                        ("버전",       self._agent_version or "알 수 없음"),
                        ("호스트명",   socket.gethostname()),
                        ("사설 IP",    self._local_ip),
                        ("공인 IP",    self._public_ip or "조회 실패"),
                        ("WS 포트",    str(self._ws_port)),
                        ("서버",       self.config.api_url or "미설정"),
                        ("서버 연결",  "✓ 로그인됨" if self._server_logged_in else "✗ 미연결"),
                        ("릴레이",     "✓ 연결됨" if self._relay_connected else "✗ 대기"),
                        ("매니저 수",  f"{n}개 연결 중" if n > 0 else "없음"),
                    ]
                    for label, value in rows:
                        row = tk.Frame(frame, bg='#1e1e1e')
                        row.pack(fill='x', pady=1)
                        tk.Label(row, text=f"{label}:", width=10, anchor='e',
                                 bg='#1e1e1e', fg='#888888',
                                 font=('맑은 고딕', 9)).pack(side='left')
                        fg = hex_color if label in ("서버 연결", "릴레이", "매니저 수") else '#ffffff'
                        tk.Label(row, text=value, anchor='w',
                                 bg='#1e1e1e', fg=fg,
                                 font=('맑은 고딕', 9)).pack(side='left', padx=5)

                    ttk.Separator(root).pack(fill='x', padx=15)
                    tk.Button(root, text="닫기", command=root.destroy,
                              bg='#333333', fg='#ffffff',
                              relief='flat', padx=20, pady=5).pack(pady=10)
                    root.mainloop()
                except Exception as e:
                    logger.warning(f"상태 팝업 오류: {e}")

            def on_reconnect(icon, item):
                """서버 재연결"""
                try:
                    if self.api_client:
                        self._server_logged_in = self.api_client.verify_token()
                        if not self._server_logged_in:
                            self._server_logged_in = self._server_login()
                        if self._server_logged_in:
                            self._register_self()
                        self._update_tray_icon()
                except Exception as e:
                    logger.warning(f"재연결 실패: {e}")

            def on_restart(icon, item):
                """에이전트 재시작"""
                icon.stop()
                _restart_agent()

            # 초기 아이콘 (회색)
            init_img = self._make_tray_icon_image((120, 120, 120))

            menu = pystray.Menu(
                pystray.MenuItem(
                    lambda item: (
                        f'WellcomAgent v{self._agent_version}'
                        if self._agent_version else 'WellcomAgent'
                    ),
                    on_show_status,
                    default=True,
                    enabled=True,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem('연결 상태 보기', on_show_status),
                pystray.MenuItem('서버 재연결', on_reconnect),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem('재시작', on_restart),
                pystray.MenuItem('종료', on_quit),
            )

            _, init_tooltip = self._get_tray_status()
            icon = pystray.Icon('WellcomAgent', init_img, init_tooltip, menu)
            self._tray_icon_obj = icon

            # 1초 뒤 초기 상태 반영
            def _delayed_update():
                time.sleep(1)
                self._update_tray_icon()
            threading.Thread(target=_delayed_update, daemon=True).start()

            icon.run()
        except ImportError:
            logger.warning("pystray/Pillow 미설치 — 트레이 아이콘 없이 실행")
        except Exception as e:
            logger.warning(f"트레이 아이콘 실패: {e}")


def install_startup():
    try:
        exe_path = sys.executable
        if not getattr(sys, 'frozen', False):
            exe_path = f'"{sys.executable}" "{os.path.abspath(__file__)}"'

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY,
            0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ, exe_path)
        winreg.CloseKey(key)
        print(f"시작프로그램 등록 완료: {exe_path}")
    except Exception as e:
        print(f"시작프로그램 등록 실패: {e}")


def uninstall_startup():
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY,
            0, winreg.KEY_SET_VALUE
        )
        winreg.DeleteValue(key, STARTUP_REG_NAME)
        winreg.CloseKey(key)
        print("시작프로그램 해제 완료")
    except FileNotFoundError:
        print("시작프로그램에 등록되어 있지 않습니다.")
    except Exception as e:
        print(f"시작프로그램 해제 실패: {e}")


def main():
    config = AgentConfig()

    if '--api-url' in sys.argv:
        idx = sys.argv.index('--api-url')
        if idx + 1 < len(sys.argv):
            api_url = sys.argv[idx + 1]
            config.set('api_url', api_url)
            print(f"서버 API URL 설정: {api_url}")

    if '--ws-port' in sys.argv:
        idx = sys.argv.index('--ws-port')
        if idx + 1 < len(sys.argv):
            ws_port = int(sys.argv[idx + 1])
            config.set('ws_port', ws_port)
            print(f"WS 서버 포트 설정: {ws_port}")

    if '--install' in sys.argv:
        install_startup()
    elif '--uninstall' in sys.argv:
        uninstall_startup()
        return

    agent = WellcomAgent()
    agent.start()


if __name__ == '__main__':
    main()
