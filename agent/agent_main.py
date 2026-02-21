"""WellcomSOFT Agent — 대상PC에서 실행되는 경량 에이전트

기능:
- 서버 로그인 + 자기 등록 + 하트비트
- 서버(log.wellcomll.org:4797/ws/agent)에 WebSocket 클라이언트로 접속
- 서버가 같은 계정의 매니저와 메시지를 릴레이
- 화면 캡처 및 스트리밍 (mss + MJPEG)
- 키보드/마우스 입력 주입 (pynput)
- 양방향 클립보드 동기화
- 파일 수신

아키텍처:
  매니저 = WS 클라이언트 (서버/ws/manager?token=JWT 접속)
  에이전트(이 코드) = WS 클라이언트 (서버/ws/agent?token=JWT 접속)
  서버 = REST API + WS 릴레이 (포트포워딩 불필요)

사용법:
  python agent_main.py
  python agent_main.py --api-url http://log.wellcomll.org:4797
  python agent_main.py --install
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
from typing import Optional

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

# 바이너리 프레임 헤더
HEADER_THUMBNAIL = 0x01
HEADER_STREAM = 0x02


class AgentAPIClient:
    """에이전트 전용 경량 REST API 클라이언트"""

    def __init__(self, config: AgentConfig):
        self.config = config
        self._token = config.api_token

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
                       os_info: str, ip: str, mac_address: str,
                       screen_width: int, screen_height: int) -> bool:
        """에이전트 자신을 서버에 등록"""
        try:
            r = requests.post(
                f'{self.config.api_url}/api/agents/register',
                json={
                    'agent_id': agent_id,
                    'hostname': hostname,
                    'os_info': os_info,
                    'ip': ip,
                    'mac_address': mac_address,
                    'screen_width': screen_width,
                    'screen_height': screen_height,
                },
                headers=self._headers(),
                timeout=10,
            )
            r.raise_for_status()
            logger.info(f"에이전트 등록 성공: {agent_id}")
            return True
        except Exception as e:
            logger.error(f"에이전트 등록 실패: {e}")
            return False

    def send_heartbeat(self, agent_id: str, ip: str,
                       screen_width: int, screen_height: int):
        """하트비트 전송"""
        try:
            requests.post(
                f'{self.config.api_url}/api/agents/heartbeat',
                json={
                    'agent_id': agent_id,
                    'ip': ip,
                    'screen_width': screen_width,
                    'screen_height': screen_height,
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

    @property
    def token(self) -> str:
        """JWT 토큰 반환 (WS 접속 시 사용)"""
        return self._token or ''


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
    """서버 등록 + 서버 WS 릴레이 접속 + 화면 캡처 + 입력 주입

    에이전트는 서버(REST API)에 로그인 후,
    서버(WS 릴레이)에 직접 접속하여 매니저와 통신한다.
    서버가 같은 계정의 매니저에 메시지를 중계. 포트포워딩 불필요.
    """

    def __init__(self):
        self.config = AgentConfig()
        self.screen_capture = ScreenCapture()
        self.input_handler = InputHandler()
        self.clipboard = ClipboardMonitor()
        self.file_receiver = FileReceiver(self.config.save_dir)
        self.api_client: Optional[AgentAPIClient] = None
        self._ws: Optional[object] = None           # 서버 WS 릴레이 연결
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tray_thread = None
        self._heartbeat_thread = None
        self._running = True
        self._streaming = False
        self._stream_task = None
        self._agent_id = socket.gethostname()
        self._local_ip = _get_local_ip()
        self._mac_address = _get_mac_address()

    def _get_system_info(self) -> dict:
        """시스템 정보 수집"""
        return {
            'hostname': socket.gethostname(),
            'os_info': f"{platform.system()} {platform.release()} {platform.version()}",
            'agent_id': self._agent_id,
            'ip': self._local_ip,
            'mac_address': self._mac_address,
        }

    # 서버 API URL (고정)
    DEFAULT_API_URL = "http://log.wellcomll.org:4797"

    @staticmethod
    def _ask_credentials() -> tuple:
        """GUI 입력창으로 사용자 이름/비밀번호만 입력받기"""
        try:
            import tkinter as tk
            from tkinter import simpledialog

            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)

            username = simpledialog.askstring(
                "WellcomAgent 로그인",
                "사용자 이름:",
                parent=root,
            )
            if not username:
                root.destroy()
                return '', ''

            password = simpledialog.askstring(
                "WellcomAgent 로그인",
                "비밀번호:",
                parent=root,
                show='*',
            )
            root.destroy()
            return username.strip(), password or ''
        except Exception as e:
            logger.error(f"로그인 입력창 오류: {e}")
            return '', ''

    def _server_login(self) -> bool:
        """서버에 로그인 (토큰 저장 → 재시작 시 자동 로그인)"""
        # 항상 기본 서버 URL 강제 적용 (IP 직접 입력 등 이전 설정 무시)
        self.config.set('api_url', self.DEFAULT_API_URL)

        self.api_client = AgentAPIClient(self.config)

        # 저장된 토큰으로 먼저 시도
        if self.config.api_token and self.api_client.verify_token():
            logger.info("저장된 토큰으로 인증 성공")
            return True

        # 토큰 없거나 만료 → 사용자 이름/비밀번호 입력
        username, password = self._ask_credentials()
        if not username or not password:
            return False

        return self.api_client.login(username, password)

    def _register_self(self):
        """서버에 에이전트 자신을 등록 (IP, hostname, OS 정보)"""
        if not self.api_client:
            return

        sys_info = self._get_system_info()
        screen_w, screen_h = self.screen_capture.screen_size

        self.api_client.register_agent(
            agent_id=sys_info['agent_id'],
            hostname=sys_info['hostname'],
            os_info=sys_info['os_info'],
            ip=sys_info['ip'],
            mac_address=sys_info['mac_address'],
            screen_width=screen_w,
            screen_height=screen_h,
        )

    def _get_ws_url(self) -> str:
        """서버 WS URL 생성 (ws://서버/ws/agent?token=JWT)"""
        api_url = self.config.api_url
        ws_url = api_url.replace('https://', 'wss://').replace('http://', 'ws://')
        return f"{ws_url}/ws/agent?token={self.api_client.token}"

    def _heartbeat_loop(self):
        """하트비트 스레드 — 서버에 주기적으로 IP/상태 전송"""
        interval = self.config.heartbeat_interval
        screen_w, screen_h = self.screen_capture.screen_size

        while self._running:
            time.sleep(interval)
            if not self._running:
                break
            if self.api_client:
                # IP가 바뀔 수 있으므로 갱신
                self._local_ip = _get_local_ip()
                self.api_client.send_heartbeat(
                    self._agent_id, self._local_ip,
                    screen_w, screen_h,
                )

    @staticmethod
    def _show_toast(title: str, message: str):
        """간단한 토스트 알림 (tkinter messagebox)"""
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            messagebox.showinfo(title, message, parent=root)
            root.destroy()
        except Exception:
            pass

    def start(self):
        """에이전트 시작: 서버 로그인 → 등록 → 서버 WS 릴레이 접속"""
        # 1) 서버 로그인
        if not self._server_login():
            logger.error("서버 로그인 실패 — 종료합니다. 재실행 후 다시 시도하세요.")
            self._show_toast("WellcomAgent", "서버 로그인 실패.\n재실행 후 다시 시도하세요.")
            return

        # 2) 서버에 자기 등록
        self._register_self()

        # 3) 하트비트 시작
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name='Heartbeat'
        )
        self._heartbeat_thread.start()

        logger.info("WellcomSOFT Agent 시작")
        logger.info(f"에이전트 ID: {self._agent_id}")
        logger.info(f"로컬 IP: {self._local_ip}")
        logger.info(f"서버: {self.config.api_url}")

        # 4) 클립보드 감시
        if self.config.clipboard_sync:
            self.clipboard.start_monitoring(self._on_clipboard_changed)

        # 5) 트레이 아이콘 (메인 스레드에서 실행, WS는 백그라운드)
        #    pystray.Icon.run()은 메인 스레드에서 실행해야 안정적
        self._ws_thread = threading.Thread(
            target=self._run_ws_loop, daemon=True, name='WSRelay'
        )
        self._ws_thread.start()

        # 트레이 아이콘을 메인 스레드에서 실행
        self._run_tray()

    def _run_ws_loop(self):
        """WS 릴레이 접속 루프 (백그라운드 스레드)"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_client())
        except Exception as e:
            logger.warning(f"WS 루프 종료: {e}")
        finally:
            if self.api_client:
                self.api_client.report_offline(self._agent_id)
            self.clipboard.stop_monitoring()
            self.screen_capture.close()

    async def _run_client(self):
        """서버 WS 릴레이에 접속 + 자동 재연결 루프

        서버(/ws/agent?token=JWT)에 접속하여 auth 핸드셰이크 후
        메시지 수신 루프를 실행. 서버가 매니저와의 메시지를 중계.
        """
        reconnect_interval = 5  # 초

        while self._running:
            uri = self._get_ws_url()

            try:
                logger.info(f"서버 WS 릴레이 접속 시도...")
                async with websockets.connect(
                    uri,
                    max_size=50 * 1024 * 1024,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws

                    # 인증 핸드셰이크 (에이전트 → 서버 → 매니저)
                    sys_info = self._get_system_info()
                    screen_w, screen_h = self.screen_capture.screen_size
                    await ws.send(json.dumps({
                        'type': 'auth',
                        'agent_id': sys_info['agent_id'],
                        'hostname': sys_info['hostname'],
                        'os_info': sys_info['os_info'],
                        'screen_width': screen_w,
                        'screen_height': screen_h,
                    }))

                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    msg = json.loads(raw)
                    if msg.get('type') != 'auth_ok':
                        logger.error(f"서버 인증 실패: {msg}")
                        self._ws = None
                        await asyncio.sleep(reconnect_interval)
                        continue

                    logger.info(f"서버 WS 릴레이 접속 성공")

                    # 메시지 수신 루프
                    async for message in ws:
                        if not self._running:
                            break
                        if isinstance(message, str):
                            await self._handle_text(ws, message)
                        elif isinstance(message, bytes):
                            await self._handle_binary(ws, message)

            except websockets.exceptions.ConnectionClosed:
                logger.info("서버 WS 연결 종료")
            except asyncio.TimeoutError:
                logger.warning("서버 인증 타임아웃")
            except (ConnectionRefusedError, OSError) as e:
                logger.warning(f"서버 접속 실패: {e}")
            except Exception as e:
                logger.warning(f"서버 접속 오류: {e}")
            finally:
                self._ws = None
                # 스트리밍 정리
                if self._stream_task:
                    self._stream_task.cancel()
                    self._stream_task = None

            if self._running:
                logger.info(f"서버 재접속 대기... ({reconnect_interval}초)")
                await asyncio.sleep(reconnect_interval)

    async def _handle_text(self, websocket, raw: str):
        """JSON 텍스트 메시지 처리"""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get('type', '')
        logger.info(f"명령 수신: type={msg_type}")

        if msg_type == 'ping':
            await websocket.send(json.dumps({'type': 'pong'}))

        elif msg_type == 'request_thumbnail':
            await self._send_thumbnail(websocket)

        elif msg_type == 'start_stream':
            fps = msg.get('fps', self.config.screen_fps)
            quality = msg.get('quality', self.config.screen_quality)
            # 기존 스트리밍 정리
            if self._stream_task:
                self._stream_task.cancel()
            self._stream_task = asyncio.ensure_future(
                self._stream_loop(websocket, fps, quality)
            )

        elif msg_type == 'stop_stream':
            if self._stream_task:
                self._stream_task.cancel()
                self._stream_task = None

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

    async def _handle_binary(self, websocket, data: bytes):
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
            logger.debug(f"썸네일 전송 완료: {len(jpeg_data)}B")
        except Exception as e:
            logger.error(f"썸네일 전송 실패: {type(e).__name__}: {e}")

    async def _stream_loop(self, websocket, fps: int, quality: int):
        """화면 스트리밍 루프"""
        interval = 1.0 / max(1, fps)
        logger.info(f"스트리밍 시작: {fps}fps, quality={quality}")

        try:
            while self._running:
                jpeg_data = self.screen_capture.capture_jpeg(quality=quality)
                await websocket.send(bytes([HEADER_STREAM]) + jpeg_data)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.debug(f"스트리밍 오류: {e}")
        finally:
            logger.info("스트리밍 중지")

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
        """로컬 클립보드 변경 → 매니저에 전송"""
        if not self._ws:
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
            asyncio.run_coroutine_threadsafe(self._ws.send(msg), self._loop)

    def _run_tray(self):
        """시스템 트레이 아이콘 (메인 스레드에서 실행)"""
        try:
            import pystray
            from PIL import Image, ImageDraw

            img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([8, 8, 56, 56], fill=(33, 150, 243, 255))
            draw.text((20, 18), 'A', fill=(255, 255, 255, 255))

            def on_quit(icon, item):
                self._running = False
                if self.api_client:
                    self.api_client.report_offline(self._agent_id)
                icon.stop()
                os._exit(0)

            def on_show_info(icon, item):
                connected = "연결됨" if self._ws else "대기 중"
                info_msg = (
                    f"에이전트 ID: {self._agent_id}\n"
                    f"서버: {self.config.api_url}\n"
                    f"WS 릴레이: {connected}\n"
                    f"IP: {self._local_ip}"
                )
                self._show_toast("WellcomAgent 정보", info_msg)

            menu = pystray.Menu(
                pystray.MenuItem(
                    f'WellcomAgent ({self._agent_id})',
                    on_show_info,
                    default=True,
                ),
                pystray.MenuItem('종료', on_quit),
            )

            self._tray_icon = pystray.Icon('WellcomAgent', img, 'WellcomSOFT Agent', menu)
            logger.info("트레이 아이콘 시작")
            self._tray_icon.run()
        except ImportError:
            logger.warning("pystray 미설치 — 트레이 아이콘 없이 실행")
            self._wait_forever()
        except Exception as e:
            logger.warning(f"트레이 아이콘 실패: {e}")
            self._wait_forever()

    def _wait_forever(self):
        """트레이 아이콘 없이 대기 (WS 스레드가 돌고 있으므로 메인 스레드 유지)"""
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self._running = False


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


def _get_agent_base_dir() -> str:
    """에이전트 설치 기본 경로 감지"""
    env_base = os.environ.get('WELLCOMAGENT_BASE_DIR')
    if env_base and os.path.isdir(env_base):
        return env_base
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _restart_agent():
    """에이전트 프로세스 재시작"""
    # 1) 런처가 설정한 EXE 경로
    exe_path = os.environ.get('WELLCOMAGENT_EXE_PATH')
    if exe_path and os.path.exists(exe_path):
        logger.info(f"[Restart] EXE 경로: {exe_path}")
        subprocess.Popen([exe_path])
        os._exit(0)

    # 2) 설치 디렉터리 기준 EXE
    base_dir = os.environ.get('WELLCOMAGENT_BASE_DIR')
    if base_dir:
        candidate = os.path.join(base_dir, 'WellcomAgent.exe')
        if os.path.exists(candidate):
            logger.info(f"[Restart] BASE_DIR 기준: {candidate}")
            subprocess.Popen([candidate])
            os._exit(0)

    # 3) Fallback
    if getattr(sys, 'frozen', False):
        subprocess.Popen([sys.executable])
    else:
        subprocess.Popen([sys.executable] + sys.argv)
    os._exit(0)


def check_agent_update() -> bool:
    """에이전트 업데이트 확인 (tkinter UI).

    Returns:
        True = 정상 진행, False = 재시작 필요
    """
    try:
        from pathlib import Path

        # 버전 정보 로드
        try:
            from version import __version__, __github_repo__, __asset_name__
        except ImportError:
            logger.debug("agent/version.py 없음 — 업데이트 확인 스킵")
            return True

        base_dir = Path(_get_agent_base_dir())
        # app/ 디렉터리가 없으면 런처 없이 직접 실행 중 → 스킵
        if not (base_dir / "app").exists() and not os.environ.get('WELLCOMAGENT_BASE_DIR'):
            logger.debug("런처 없이 실행 중 — 업데이트 확인 스킵")
            return True

        # updater 모듈 로드
        try:
            # app/ 경로에서 실행 중이면 updater가 같은 레벨에 없을 수 있음
            # sys.path에 부모 디렉터리 추가
            parent_dir = str(base_dir / "app")
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)

            from updater.update_checker import UpdateChecker
        except ImportError:
            logger.debug("updater 모듈 없음 — 업데이트 확인 스킵")
            return True

        checker = UpdateChecker(
            base_dir=base_dir,
            repo=__github_repo__,
            token=None,
            running_version=__version__,
            asset_name=__asset_name__,
        )

        has_update, release_info = checker.check_update()
        if not has_update or not release_info:
            return True

        # tkinter 알림 (에이전트는 PyQt6 없음)
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)

            answer = messagebox.askyesno(
                "WellcomAgent 업데이트",
                f"새 버전이 있습니다.\n\n"
                f"현재: v{__version__}\n"
                f"최신: v{release_info.version}\n\n"
                f"업데이트하시겠습니까?",
                parent=root,
            )
            root.destroy()

            if not answer:
                return True

        except Exception:
            # tkinter 없으면 자동 업데이트
            logger.info("tkinter 없음 — 자동 업데이트 진행")

        # 업데이트 적용
        logger.info(f"에이전트 업데이트 시작: v{__version__} → v{release_info.version}")
        success = checker.apply_update(release_info)

        if success:
            logger.info("업데이트 완료 — 재시작합니다")
            _restart_agent()
            return False

        logger.error("업데이트 적용 실패")
        return True

    except Exception as e:
        logger.debug(f"업데이트 확인 실패: {e}")
        return True


def main():
    config = AgentConfig()

    if '--api-url' in sys.argv:
        idx = sys.argv.index('--api-url')
        if idx + 1 < len(sys.argv):
            api_url = sys.argv[idx + 1]
            config.set('api_url', api_url)
            print(f"서버 API URL 설정: {api_url}")

    if '--install' in sys.argv:
        install_startup()
    elif '--uninstall' in sys.argv:
        uninstall_startup()
        return

    # 자동 업데이트는 런처(agent_launcher.py)에서 app/ 로드 전에 실행됨.
    # 개발 환경(런처 없이 직접 실행)에서만 여기서 체크.
    if not os.environ.get('WELLCOMAGENT_BASE_DIR'):
        if not check_agent_update():
            return

    agent = WellcomAgent()
    agent.start()


if __name__ == '__main__':
    main()
