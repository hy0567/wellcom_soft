"""WellcomSOFT Agent — 대상PC에서 실행되는 경량 에이전트

기능:
- 서버 로그인 + 자기 등록 + 하트비트
- WebSocket 클라이언트 (관리PC에 역방향 연결)
- 화면 캡처 및 스트리밍 (mss + MJPEG)
- 키보드/마우스 입력 주입 (pynput)
- 양방향 클립보드 동기화
- 파일 수신

사용법:
  python agent_main.py --server 192.168.1.100
  python agent_main.py --api-url http://log.wellcomll.org:8000
  python agent_main.py --install --server 192.168.1.100
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
HEADER_H264_KEYFRAME = 0x03
HEADER_H264_DELTA = 0x04

# H.264 인코더 (PyAV)
try:
    from h264_encoder import H264Encoder, AV_AVAILABLE as H264_AVAILABLE
except ImportError:
    H264_AVAILABLE = False
    H264Encoder = None


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
    """트레이 아이콘 + 서버 등록 + WebSocket + 화면 캡처 + 입력 주입"""

    def __init__(self):
        self.config = AgentConfig()
        self.screen_capture = ScreenCapture()
        self.input_handler = InputHandler()
        self.clipboard = ClipboardMonitor()
        self.file_receiver = FileReceiver(self.config.save_dir)
        self.api_client: Optional[AgentAPIClient] = None
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tray_thread = None
        self._heartbeat_thread = None
        self._running = True
        self._streaming = False
        self._stream_task = None
        self._stream_fps = 15       # v2.0.1: 실시간 조절용
        self._stream_quality = 60   # v2.0.1: 실시간 조절용
        self._stream_codec = 'mjpeg'  # v2.0.2: 'mjpeg' 또는 'h264'
        self._h264_encoder: Optional[object] = None  # v2.0.2: H264Encoder 인스턴스
        self._thumbnail_push = False
        self._thumbnail_push_task = None
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

    @staticmethod
    def _ask_server_ip() -> str:
        """GUI 입력창으로 관리PC IP 입력받기"""
        try:
            import tkinter as tk
            from tkinter import simpledialog

            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)

            ip = simpledialog.askstring(
                "WellcomAgent 초기 설정",
                "관리PC IP 주소를 입력하세요:\n"
                "(예: 192.168.1.100)",
                parent=root,
            )
            root.destroy()
            if ip:
                ip = ip.strip()
            return ip or ''
        except Exception as e:
            logger.error(f"IP 입력창 오류: {e}")
            return ''

    @staticmethod
    def _ask_login_info() -> tuple:
        """GUI 입력창으로 서버 로그인 정보 입력받기"""
        try:
            import tkinter as tk
            from tkinter import simpledialog

            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)

            api_url = simpledialog.askstring(
                "WellcomAgent 서버 설정",
                "서버 API 주소를 입력하세요:\n"
                "(예: http://log.wellcomll.org:8000)",
                parent=root,
            )
            if not api_url:
                root.destroy()
                return '', '', ''

            username = simpledialog.askstring(
                "WellcomAgent 로그인",
                "사용자 이름:",
                parent=root,
            )
            if not username:
                root.destroy()
                return api_url.strip(), '', ''

            password = simpledialog.askstring(
                "WellcomAgent 로그인",
                "비밀번호:",
                parent=root,
                show='*',
            )
            root.destroy()
            return api_url.strip(), username.strip(), password or ''
        except Exception as e:
            logger.error(f"로그인 입력창 오류: {e}")
            return '', '', ''

    def _server_login(self) -> bool:
        """서버에 로그인하고 에이전트 등록"""
        if not self.config.api_url:
            return False

        self.api_client = AgentAPIClient(self.config)

        # 저장된 토큰으로 먼저 시도
        if self.config.api_token and self.api_client.verify_token():
            logger.info("저장된 토큰으로 인증 성공")
            return True

        # 토큰 없거나 만료 → 로그인 필요
        _, username, password = self._ask_login_info()
        if not username or not password:
            return False

        return self.api_client.login(username, password)

    def _register_self(self):
        """서버에 에이전트 자신을 등록"""
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
                    screen_w, screen_h,
                )

    def start(self):
        """에이전트 시작"""
        # 서버 로그인 (API URL이 설정된 경우)
        if self.config.api_url:
            if not self._server_login():
                logger.warning("서버 로그인 실패 — 설정 UI로 전환")
                # 서버 로그인 실패 시 설정 UI 열기
                result = self._ask_server_ip()
                if not result:
                    return
            else:
                self._register_self()

                # 하트비트 시작
                self._heartbeat_thread = threading.Thread(
                    target=self._heartbeat_loop, daemon=True, name='Heartbeat'
                )
                self._heartbeat_thread.start()
        else:
            # API URL이 없으면 설정 UI 열기
            result = self._ask_server_ip()
            if not result:
                return

        logger.info("WellcomSOFT Agent 시작")
        logger.info(f"서버 API: {self.config.api_url}")

        # 클립보드 감시
        if self.config.clipboard_sync:
            self.clipboard.start_monitoring(self._on_clipboard_changed)

        # 트레이 아이콘
        self._tray_thread = threading.Thread(
            target=self._run_tray, daemon=True, name='TrayIcon'
        )
        self._tray_thread.start()

        # WebSocket 클라이언트 (서버 릴레이 접속)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_client())
        except KeyboardInterrupt:
            logger.info("Ctrl+C — 종료")
        finally:
            # 오프라인 보고
            if self.api_client:
                self.api_client.report_offline(self._agent_id)
            self.clipboard.stop_monitoring()
            self.screen_capture.close()

    async def _run_client(self):
        """서버 WS 릴레이에 접속 (자동 재연결)

        서버의 /ws/agent?token=JWT 엔드포인트에 접속하여
        서버가 매니저와 메시지를 중계한다.
        """
        # WS URL 구성: http → ws 변환
        api_url = self.config.api_url or ''
        ws_base = api_url.replace('https://', 'wss://').replace('http://', 'ws://')
        token = self.api_client._token if self.api_client else ''
        uri = f"{ws_base}/ws/agent?token={token}"

        while self._running:
            try:
                logger.info(f"서버 WS 릴레이 접속 시도: {ws_base}/ws/agent")
                async with websockets.connect(
                    uri,
                    max_size=50 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                ) as ws:
                    self._ws = ws

                    # 인증 (서버가 매니저에 전달)
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
                        logger.error(f"인증 실패: {msg}")
                        await asyncio.sleep(5)
                        continue

                    logger.info("서버 WS 릴레이 접속 성공! (매니저와 중계)")

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
            except Exception as e:
                err_msg = str(e) or type(e).__name__
                logger.warning(f"연결 오류: {err_msg}")
            finally:
                self._ws = None
                self._streaming = False
                self._thumbnail_push = False
                if self._h264_encoder:
                    self._h264_encoder.close()
                    self._h264_encoder = None

            if self._running:
                logger.info("5초 후 재연결...")
                await asyncio.sleep(5)

    # v2.1.0: 메시지 수신 카운터 (디버그)
    _msg_recv_count: int = 0

    async def _handle_text(self, websocket, raw: str):
        """JSON 텍스트 메시지 처리"""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get('type', '')
        self._msg_recv_count += 1
        # 스트리밍 중 메시지 수신 로그 (디버그)
        if self._streaming and msg_type not in ('ping', 'pong'):
            logger.info(f"📩 메시지 #{self._msg_recv_count} (스트리밍 중): type={msg_type}")

        if msg_type == 'ping':
            await websocket.send(json.dumps({'type': 'pong'}))

        elif msg_type == 'request_thumbnail':
            await self._send_thumbnail(websocket)

        elif msg_type == 'start_stream':
            fps = msg.get('fps', self.config.screen_fps)
            quality = msg.get('quality', self.config.screen_quality)
            codec = msg.get('codec', 'h264')  # v2.0.2: 기본 h264, 불가 시 mjpeg 폴백
            keyframe_interval = msg.get('keyframe_interval', 60)
            # v2.1.0: 백그라운드 태스크로 실행 (메시지 수신 루프 블로킹 방지)
            if self._stream_task and not self._stream_task.done():
                self._streaming = False
                await asyncio.sleep(0.1)  # 이전 스트리밍 종료 대기
            self._stream_task = asyncio.create_task(
                self._start_streaming(websocket, fps, quality, codec, keyframe_interval)
            )

        elif msg_type == 'stop_stream':
            self._streaming = False

        elif msg_type == 'update_stream':
            # v2.0.1 — 스트리밍 중 화질/FPS 실시간 변경
            new_fps = msg.get('fps', self._stream_fps)
            new_quality = msg.get('quality', self._stream_quality)
            old_quality = self._stream_quality
            self._stream_fps = max(1, min(60, new_fps))
            self._stream_quality = max(10, min(100, new_quality))
            # v2.0.2 — H.264 인코더 화질 업데이트
            if self._h264_encoder and old_quality != self._stream_quality:
                self._h264_encoder.update_quality(self._stream_quality)
            logger.info(f"스트리밍 설정 변경: {self._stream_fps}fps, Q={self._stream_quality}")

        elif msg_type == 'request_keyframe':
            # v2.0.2 — H.264 키프레임 강제 요청
            if self._h264_encoder:
                self._h264_encoder.force_keyframe()
                logger.info("키프레임 강제 요청 수신")

        elif msg_type == 'special_key':
            # v2.0.1 — 특수키 (Ctrl+Alt+Del, Alt+Tab, Win)
            combo = msg.get('combo', '')
            await self._handle_special_key(combo)

        elif msg_type == 'start_thumbnail_push':
            interval = msg.get('interval', 1.0)
            # v2.1.0: 백그라운드 태스크로 실행
            if self._thumbnail_push_task and not self._thumbnail_push_task.done():
                self._thumbnail_push = False
                await asyncio.sleep(0.1)
            self._thumbnail_push_task = asyncio.create_task(
                self._start_thumbnail_push(websocket, interval)
            )

        elif msg_type == 'stop_thumbnail_push':
            self._thumbnail_push = False

        elif msg_type == 'key_event':
            key = msg.get('key', '')
            action = msg.get('action', 'press')
            modifiers = msg.get('modifiers', [])
            logger.info(f"⌨ 키 입력: key={key}, action={action}, mods={modifiers}")
            self.input_handler.handle_key_event(
                key=key, action=action, modifiers=modifiers,
            )

        elif msg_type == 'mouse_event':
            x = msg.get('x', 0)
            y = msg.get('y', 0)
            button = msg.get('button', 'none')
            action = msg.get('action', 'move')
            scroll_delta = msg.get('scroll_delta', 0)
            # move는 너무 빈번하므로 클릭/스크롤만 로그
            if action != 'move':
                logger.info(f"🖱 마우스: action={action}, btn={button}, pos=({x},{y}), scroll={scroll_delta}")
            self.input_handler.handle_mouse_event(
                x=x, y=y, button=button, action=action, scroll_delta=scroll_delta,
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
        except Exception as e:
            logger.debug(f"썸네일 전송 실패: {e}")

    async def _start_thumbnail_push(self, websocket, interval: float = 1.0):
        """썸네일 push 모드 — 주기적으로 썸네일을 자동 전송"""
        self._thumbnail_push = True
        interval = max(0.2, min(interval, 5.0))
        logger.info(f"썸네일 push 시작: {interval}초 간격")

        try:
            while self._thumbnail_push and self._running:
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
        finally:
            self._thumbnail_push = False
            logger.info("썸네일 push 중지")

    async def _start_streaming(self, websocket, fps: int, quality: int,
                               codec: str = 'h264', keyframe_interval: int = 60):
        """화면 스트리밍 시작 (MJPEG 또는 H.264)

        v2.0.2: H.264 코덱 지원 + 코덱 협상
        """
        self._streaming = True
        self._stream_fps = max(1, min(60, fps))
        self._stream_quality = max(10, min(100, quality))

        # 코덱 결정: H.264 요청 시 인코더 초기화 시도
        actual_codec = 'mjpeg'
        encoder_name = ''

        if codec == 'h264' and H264_AVAILABLE and H264Encoder:
            try:
                screen_w, screen_h = self.screen_capture.screen_size
                self._h264_encoder = H264Encoder(
                    width=screen_w, height=screen_h,
                    fps=self._stream_fps,
                    quality=self._stream_quality,
                    gop_size=keyframe_interval,
                )
                actual_codec = 'h264'
                encoder_name = self._h264_encoder.encoder_name
                self._stream_codec = 'h264'
                logger.info(f"H.264 인코더 활성화: {encoder_name}")
            except Exception as e:
                logger.warning(f"H.264 인코더 초기화 실패, MJPEG 폴백: {e}")
                self._h264_encoder = None
                self._stream_codec = 'mjpeg'
        else:
            self._stream_codec = 'mjpeg'
            if codec == 'h264':
                logger.info("H.264 미지원 환경 — MJPEG 폴백")

        # stream_started 응답 (코덱 협상)
        screen_w, screen_h = self.screen_capture.screen_size
        await websocket.send(json.dumps({
            'type': 'stream_started',
            'codec': actual_codec,
            'encoder': encoder_name,
            'width': screen_w,
            'height': screen_h,
            'fps': self._stream_fps,
            'quality': self._stream_quality,
        }))

        logger.info(
            f"★ 스트리밍 시작: codec={actual_codec} ({encoder_name or 'jpeg'}), "
            f"{self._stream_fps}fps, Q={self._stream_quality}, "
            f"해상도={screen_w}x{screen_h}"
        )

        try:
            if actual_codec == 'h264':
                await self._stream_h264(websocket)
            else:
                await self._stream_mjpeg(websocket)
        except websockets.exceptions.ConnectionClosed:
            logger.info("스트리밍 중단 — WS 연결 종료")
        except Exception as e:
            logger.warning(f"스트리밍 오류: {e}")
        finally:
            self._streaming = False
            if self._h264_encoder:
                self._h264_encoder.close()
                self._h264_encoder = None
            logger.info("★ 스트리밍 종료")

    async def _stream_mjpeg(self, websocket):
        """MJPEG 스트리밍 루프

        v2.1.1: 대폭 최적화 — 캡처/전송 파이프라인 분리, 적응형 FPS
        서버 릴레이 RTT로 인한 FPS 저하 문제 해결:
        - websocket.send()가 RTT만큼 블로킹 → 캡처와 독립적으로 전송
        - 프레임 큐로 최신 프레임만 전송 (낡은 프레임 자동 드롭)
        """
        screen_w, screen_h = self.screen_capture.screen_size
        # 해상도 스케일링
        if screen_w > 1920:
            scale = 1280 / screen_w
        elif screen_w > 1280:
            scale = 1280 / screen_w  # 1080p→720p
        else:
            scale = 1.0

        logger.info(f"MJPEG 스트리밍 시작: {screen_w}x{screen_h} "
                     f"→ scale={scale:.2f}, Q={self._stream_quality}, "
                     f"target={self._stream_fps}fps")

        # 프레임 큐: 캡처 루프가 넣고, 전송 루프가 꺼냄
        # maxsize=2: 최신 2프레임만 유지 (낡은 프레임 자동 드롭)
        frame_queue = asyncio.Queue(maxsize=2)
        send_done = asyncio.Event()

        async def _capture_loop():
            """캡처 루프 — 타이머 기반으로 프레임 생성"""
            consecutive_errors = 0
            target_interval = 1.0 / max(1, self._stream_fps)
            next_frame_time = time.time()

            while self._streaming and self._running:
                try:
                    now = time.time()
                    wait_time = next_frame_time - now
                    if wait_time > 0.001:
                        await asyncio.sleep(wait_time)

                    jpeg_data = self.screen_capture.capture_jpeg(
                        quality=self._stream_quality,
                        scale=scale,
                    )
                    if jpeg_data:
                        # 큐가 가득 차면 오래된 프레임 버리고 새 프레임 넣기
                        if frame_queue.full():
                            try:
                                frame_queue.get_nowait()  # 오래된 프레임 드롭
                            except asyncio.QueueEmpty:
                                pass
                        await frame_queue.put(jpeg_data)
                        consecutive_errors = 0
                    else:
                        consecutive_errors += 1

                    target_interval = 1.0 / max(1, self._stream_fps)
                    next_frame_time = time.time() + target_interval

                except asyncio.CancelledError:
                    return
                except Exception as e:
                    consecutive_errors += 1
                    if consecutive_errors >= 10:
                        logger.warning("MJPEG 캡처 연속 에러 10회 — 중단")
                        return
                    await asyncio.sleep(0.05)

            send_done.set()

        async def _send_loop():
            """전송 루프 — 큐에서 프레임 꺼내서 WS 전송"""
            frame_count = 0
            total_bytes = 0
            fps_start = time.time()

            while self._streaming and self._running:
                try:
                    # 최대 0.2초 대기 (캡처 루프가 느려도 반응)
                    try:
                        jpeg_data = await asyncio.wait_for(
                            frame_queue.get(), timeout=0.2
                        )
                    except asyncio.TimeoutError:
                        if send_done.is_set():
                            return
                        continue

                    await websocket.send(bytes([HEADER_STREAM]) + jpeg_data)
                    frame_count += 1
                    total_bytes += len(jpeg_data)

                    # 5초마다 FPS 로그
                    elapsed = time.time() - fps_start
                    if elapsed >= 5.0:
                        actual_fps = frame_count / elapsed
                        avg_size = total_bytes / frame_count if frame_count else 0
                        bandwidth_kbps = (total_bytes * 8) / (elapsed * 1000)
                        q_size = frame_queue.qsize()
                        logger.info(
                            f"MJPEG: {actual_fps:.1f}fps (목표 {self._stream_fps}), "
                            f"평균={avg_size/1024:.1f}KB, "
                            f"BW={bandwidth_kbps:.0f}kbps, "
                            f"큐={q_size}"
                        )
                        frame_count = 0
                        total_bytes = 0
                        fps_start = time.time()

                except websockets.exceptions.ConnectionClosed:
                    logger.info(f"MJPEG 전송 중단 — WS 연결 종료 (프레임 #{frame_count})")
                    raise
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.debug(f"MJPEG 전송 오류: {e}")
                    await asyncio.sleep(0.05)

        # 캡처+전송을 동시 실행
        capture_task = asyncio.create_task(_capture_loop())
        send_task = asyncio.create_task(_send_loop())
        try:
            # 둘 중 하나라도 끝나면 다른 것도 취소
            done, pending = await asyncio.wait(
                [capture_task, send_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            # 완료된 태스크에서 예외 전파
            for task in done:
                if task.exception():
                    raise task.exception()
        except websockets.exceptions.ConnectionClosed:
            raise
        except asyncio.CancelledError:
            pass

    async def _stream_h264(self, websocket):
        """H.264 스트리밍 루프 (v2.0.2)"""
        consecutive_errors = 0
        while self._streaming and self._running:
            try:
                # PIL Image 캡처 (JPEG 안 거침)
                pil_image = self.screen_capture.capture_raw()
                if pil_image is None:
                    await asyncio.sleep(0.1)
                    continue

                # H.264 인코딩
                packets = self._h264_encoder.encode_frame(pil_image)

                for is_keyframe, nal_data in packets:
                    header = HEADER_H264_KEYFRAME if is_keyframe else HEADER_H264_DELTA
                    await websocket.send(bytes([header]) + nal_data)

                consecutive_errors = 0
                interval = 1.0 / max(1, self._stream_fps)
                await asyncio.sleep(interval)
            except websockets.exceptions.ConnectionClosed:
                raise
            except Exception as e:
                consecutive_errors += 1
                logger.debug(f"H.264 프레임 전송 오류: {e}")
                if consecutive_errors >= 10:
                    logger.warning("H.264 연속 에러 10회 — 스트리밍 중단")
                    break
                await asyncio.sleep(0.1)

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

    async def _handle_special_key(self, combo: str):
        """v2.0.1 — 특수키 조합 전송 (pynput)"""
        try:
            if combo == 'ctrl_alt_del':
                # Ctrl+Alt+Del (SAS) — 일반 프로세스에서 직접 불가
                # 대안: SASTrigger 레지스트리 또는 subprocess 사용
                try:
                    import ctypes
                    ctypes.windll.user32.LockWorkStation()
                    logger.info("특수키: Ctrl+Alt+Del → LockWorkStation 실행")
                except Exception as e:
                    logger.warning(f"Ctrl+Alt+Del 실패 (LockWorkStation): {e}")

            elif combo == 'alt_tab':
                self.input_handler.handle_key_event('alt', 'press', [])
                self.input_handler.handle_key_event('tab', 'press', ['alt'])
                await asyncio.sleep(0.05)
                self.input_handler.handle_key_event('tab', 'release', ['alt'])
                self.input_handler.handle_key_event('alt', 'release', [])
                logger.info("특수키: Alt+Tab 전송")

            elif combo == 'win':
                self.input_handler.handle_key_event('meta', 'press', [])
                await asyncio.sleep(0.05)
                self.input_handler.handle_key_event('meta', 'release', [])
                logger.info("특수키: Win 키 전송")

            else:
                logger.warning(f"알 수 없는 특수키 조합: {combo}")
        except Exception as e:
            logger.error(f"특수키 전송 오류 [{combo}]: {e}")

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
        """로컬 클립보드 변경 → 관리PC에 전송"""
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

        if self._loop and self._loop.is_running() and self._ws:
            asyncio.run_coroutine_threadsafe(self._ws.send(msg), self._loop)

    def _run_tray(self):
        """시스템 트레이 아이콘"""
        try:
            import pystray
            from PIL import Image, ImageDraw

            img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([8, 8, 56, 56], fill=(33, 150, 243, 255))
            draw.text((20, 18), 'S', fill=(255, 255, 255, 255))

            def on_quit(icon, item):
                self._running = False
                if self.api_client:
                    self.api_client.report_offline(self._agent_id)
                icon.stop()
                os._exit(0)

            def on_show_info(icon, item):
                status = "연결됨" if self._ws else "연결 대기"
                streaming = " [스트리밍]" if self._streaming else ""
                server_info = f"서버: {self.config.api_url}" if self.config.api_url else "서버: 미설정"
                logger.info(f"{server_info}, 상태: {status}{streaming}")

            menu = pystray.Menu(
                pystray.MenuItem(
                    f'WellcomSOFT → {self.config.server_ip}',
                    on_show_info,
                    default=True,
                ),
                pystray.MenuItem('종료', on_quit),
            )

            icon = pystray.Icon('WellcomAgent', img, 'WellcomSOFT Agent', menu)
            icon.run()
        except ImportError:
            logger.warning("pystray 미설치 — 트레이 아이콘 없이 실행")
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

    if '--server' in sys.argv:
        idx = sys.argv.index('--server')
        if idx + 1 < len(sys.argv):
            server_ip = sys.argv[idx + 1]
            config.set('server_ip', server_ip)
            print(f"관리PC IP 설정: {server_ip}")

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

    agent = WellcomAgent()
    agent.start()


if __name__ == '__main__':
    main()
