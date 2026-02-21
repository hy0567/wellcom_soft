"""다중 에이전트 WebSocket 릴레이 클라이언트

관리PC(매니저)가 서버의 /ws/manager 엔드포인트에 접속하여,
서버가 에이전트↔매니저 간 메시지를 릴레이한다.
포트포워딩 불필요 — 매니저/에이전트 모두 서버에 접속.

메시지 라우팅:
  매니저→서버→에이전트: JSON에 target_agent 필드 추가 / 바이너리에 agent_id(32B) 프리픽스
  에이전트→서버→매니저: JSON에 source_agent 필드 포함 / 바이너리에 agent_id(32B) 프리픽스
"""

import asyncio
import json
import base64
import logging
import threading
import os
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets 미설치 — 에이전트 기능 비활성화")

# agent_id 바이너리 패딩 크기 (서버 릴레이 프로토콜)
AGENT_ID_LEN = 32


def _pad_agent_id(agent_id: str) -> bytes:
    return agent_id.encode('utf-8')[:AGENT_ID_LEN].ljust(AGENT_ID_LEN, b'\x00')


def _unpad_agent_id(data: bytes) -> str:
    return data[:AGENT_ID_LEN].rstrip(b'\x00').decode('utf-8', errors='replace')


class AgentServer(QObject):
    """다중 에이전트 WebSocket 릴레이 클라이언트 (관리PC 측)

    서버의 /ws/manager?token=JWT 엔드포인트에 접속하여
    서버가 중계하는 에이전트 메시지를 수신/전송한다.
    """

    # 시그널 (기존 인터페이스 100% 유지)
    agent_connected = pyqtSignal(str, str)         # agent_id, remote_ip
    agent_disconnected = pyqtSignal(str)            # agent_id
    thumbnail_received = pyqtSignal(str, bytes)     # agent_id, jpeg_data
    screen_frame_received = pyqtSignal(str, bytes)  # agent_id, jpeg_data
    h264_frame_received = pyqtSignal(str, int, bytes)  # agent_id, header(0x03|0x04), raw_data  v2.0.2
    stream_started = pyqtSignal(str, dict)           # agent_id, info_dict  v2.0.2
    clipboard_received = pyqtSignal(str, str, object)  # agent_id, format, data
    file_progress = pyqtSignal(str, int, int)       # agent_id, sent, total
    file_complete = pyqtSignal(str, str)            # agent_id, remote_path
    command_result = pyqtSignal(str, str, str, int)  # agent_id, command, output, returncode

    CHUNK_SIZE = 64 * 1024  # 64KB

    # 바이너리 프레임 헤더
    HEADER_THUMBNAIL = 0x01
    HEADER_STREAM = 0x02
    HEADER_H264_KEYFRAME = 0x03   # v2.0.2
    HEADER_H264_DELTA = 0x04      # v2.0.2

    def __init__(self):
        super().__init__()
        self._ws: Optional[object] = None           # 서버 WebSocket 연결
        self._agents: Dict[str, dict] = {}           # agent_id → info dict (가상)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._server_url: str = ""
        self._token: str = ""

    @property
    def connected_count(self) -> int:
        return len(self._agents)

    def get_connected_agents(self) -> List[str]:
        return list(self._agents.keys())

    def is_agent_connected(self, agent_id: str) -> bool:
        return agent_id in self._agents

    def get_agent_info(self, agent_id: str) -> Optional[dict]:
        return self._agents.get(agent_id)

    # ==================== 연결 수명주기 ====================

    def start_connection(self, server_url: str, token: str):
        """서버 WS 릴레이에 접속 (백그라운드 스레드)

        Args:
            server_url: 서버 WS URL (예: ws://log.wellcomll.org:4797)
            token: JWT 토큰
        """
        if not WEBSOCKETS_AVAILABLE:
            logger.error(
                "[AgentServer] websockets 미설치 — 서버 접속 불가! "
                "pip install websockets 또는 app/_vendor/ 에 번들 필요"
            )
            return

        if self._thread and self._thread.is_alive():
            return

        self._server_url = server_url
        self._token = token
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name='AgentServer'
        )
        self._thread.start()
        logger.info(f"[AgentServer] 서버 릴레이 접속 시작: {server_url}")

    def stop_connection(self):
        """서버 연결 종료"""
        self._stop_event.set()

        if self._ws and self._loop and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
            except Exception:
                pass

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        # 모든 가상 에이전트 해제
        for agent_id in list(self._agents.keys()):
            self.agent_disconnected.emit(agent_id)
        self._agents.clear()

    # 하위 호환 (start_server/stop_server → start_connection/stop_connection)
    def start_server(self):
        """하위 호환 — start_connection 사용 권장"""
        logger.warning("[AgentServer] start_server() 호출 — 서버 URL 없이 시작 불가")

    def stop_server(self):
        """하위 호환"""
        self.stop_connection()

    # ==================== 에이전트에 명령 전송 ====================

    def request_thumbnail(self, agent_id: str):
        self._send_to_agent(agent_id, {'type': 'request_thumbnail'})

    def start_streaming(self, agent_id: str, fps: int = 15, quality: int = 60,
                        codec: str = 'h264', keyframe_interval: int = 60):
        self._send_to_agent(agent_id, {
            'type': 'start_stream', 'fps': fps, 'quality': quality,
            'codec': codec, 'keyframe_interval': keyframe_interval,
        })

    def stop_streaming(self, agent_id: str):
        self._send_to_agent(agent_id, {'type': 'stop_stream'})

    def update_streaming(self, agent_id: str, fps: int = 15, quality: int = 60):
        """스트리밍 중 화질/FPS 실시간 변경 (v2.0.1)"""
        self._send_to_agent(agent_id, {
            'type': 'update_stream', 'fps': fps, 'quality': quality,
        })

    def send_special_key(self, agent_id: str, key_combo: str):
        """특수키 전송: ctrl_alt_del, alt_tab, win (v2.0.1)"""
        self._send_to_agent(agent_id, {
            'type': 'special_key', 'combo': key_combo,
        })

    def request_keyframe(self, agent_id: str):
        """H.264 키프레임 강제 요청 (v2.0.2)"""
        self._send_to_agent(agent_id, {'type': 'request_keyframe'})

    def start_thumbnail_push(self, agent_id: str, interval: float = 1.0):
        self._send_to_agent(agent_id, {
            'type': 'start_thumbnail_push', 'interval': interval,
        })

    def stop_thumbnail_push(self, agent_id: str):
        self._send_to_agent(agent_id, {'type': 'stop_thumbnail_push'})

    def send_key_event(self, agent_id: str, key: str, action: str,
                       modifiers: list = None):
        self._send_to_agent(agent_id, {
            'type': 'key_event', 'key': key,
            'action': action, 'modifiers': modifiers or [],
        })

    def send_mouse_event(self, agent_id: str, x: int, y: int,
                         button: str = 'none', action: str = 'move',
                         scroll_delta: int = 0):
        self._send_to_agent(agent_id, {
            'type': 'mouse_event', 'x': x, 'y': y,
            'button': button, 'action': action,
            'scroll_delta': scroll_delta,
        })

    def send_clipboard_text(self, agent_id: str, text: str):
        self._send_to_agent(agent_id, {
            'type': 'clipboard', 'format': 'text', 'data': text,
        })

    def send_clipboard_image(self, agent_id: str, image_data: bytes):
        self._send_to_agent(agent_id, {
            'type': 'clipboard', 'format': 'image',
            'data': base64.b64encode(image_data).decode('ascii'),
        })

    def send_file(self, agent_id: str, filepath: str):
        if not self.is_agent_connected(agent_id):
            return

        def _do_send():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._send_file_async(agent_id, filepath), self._loop
                ).result(timeout=300)
            except Exception as e:
                logger.error(f"파일 전송 실패 [{agent_id}]: {e}")

        threading.Thread(target=_do_send, daemon=True).start()

    def execute_command(self, agent_id: str, command: str):
        self._send_to_agent(agent_id, {'type': 'execute', 'command': command})

    # ==================== 브로드캐스트 ====================

    def broadcast_key_event(self, agent_ids: List[str], key: str,
                            action: str, modifiers: list = None):
        for agent_id in agent_ids:
            self.send_key_event(agent_id, key, action, modifiers)

    def broadcast_mouse_event(self, agent_ids: List[str], x: int, y: int,
                              button: str = 'none', action: str = 'move',
                              scroll_delta: int = 0):
        for agent_id in agent_ids:
            self.send_mouse_event(agent_id, x, y, button, action, scroll_delta)

    def broadcast_file(self, agent_ids: List[str], filepath: str):
        for agent_id in agent_ids:
            self.send_file(agent_id, filepath)

    def broadcast_command(self, agent_ids: List[str], command: str):
        for agent_id in agent_ids:
            self.execute_command(agent_id, command)

    # ==================== 내부 구현 ====================

    # v2.1.0: 입력 명령 전송 카운터 (디버그)
    _input_send_count: int = 0

    def _send_to_agent(self, agent_id: str, msg_dict: dict):
        """서버 릴레이를 통해 에이전트에 JSON 메시지 전송"""
        if not self._ws or not self._loop or not self._loop.is_running():
            msg_type = msg_dict.get('type', '?')
            if msg_type in ('mouse_event', 'key_event'):
                logger.warning(
                    f"[AgentServer] 입력 전송 실패 — WS 미연결: type={msg_type}, "
                    f"ws={'있음' if self._ws else '없음'}, "
                    f"loop={'실행중' if self._loop and self._loop.is_running() else '없음'}"
                )
            return

        # target_agent 필드 추가 (서버가 라우팅)
        msg_dict['target_agent'] = agent_id
        msg = json.dumps(msg_dict)

        # v2.1.0: 입력 이벤트 전송 로그 (클릭/키만, move 제외)
        msg_type = msg_dict.get('type', '')
        if msg_type == 'key_event':
            self._input_send_count += 1
            logger.info(
                f"[AgentServer] 키 전송 #{self._input_send_count}: "
                f"key={msg_dict.get('key')}, agent={agent_id}"
            )
        elif msg_type == 'mouse_event' and msg_dict.get('action') != 'move':
            self._input_send_count += 1
            logger.info(
                f"[AgentServer] 마우스 전송 #{self._input_send_count}: "
                f"action={msg_dict.get('action')}, btn={msg_dict.get('button')}, "
                f"pos=({msg_dict.get('x')},{msg_dict.get('y')}), agent={agent_id}"
            )

        asyncio.run_coroutine_threadsafe(self._ws.send(msg), self._loop)

    def _send_binary_to_agent(self, agent_id: str, data: bytes):
        """서버 릴레이를 통해 에이전트에 바이너리 전송"""
        if not self._ws or not self._loop or not self._loop.is_running():
            return

        # agent_id(32B) + 원본 데이터
        prefixed = _pad_agent_id(agent_id) + data
        asyncio.run_coroutine_threadsafe(self._ws.send(prefixed), self._loop)

    async def _send_file_async(self, agent_id: str, filepath: str):
        """파일 전송 코루틴 (릴레이 경유)"""
        if not self._ws:
            return

        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        # file_start
        await self._ws.send(json.dumps({
            'type': 'file_start', 'name': filename, 'size': filesize,
            'target_agent': agent_id,
        }))

        # 파일 청크 전송 (바이너리 — agent_id 프리픽스)
        sent = 0
        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(self.CHUNK_SIZE)
                if not chunk:
                    break
                await self._ws.send(_pad_agent_id(agent_id) + chunk)
                sent += len(chunk)
                self.file_progress.emit(agent_id, sent, filesize)

        # file_end
        await self._ws.send(json.dumps({
            'type': 'file_end', 'name': filename,
            'target_agent': agent_id,
        }))

    def _run_loop(self):
        """asyncio 이벤트 루프 실행 (스레드)"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        except Exception as e:
            if not self._stop_event.is_set():
                logger.error(f"AgentServer 오류: {e}")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None

    async def _connect_loop(self):
        """서버 WS 릴레이에 접속 + 자동 재연결"""
        # http(s):// → ws(s):// 변환
        base = self._server_url
        if base.startswith('https://'):
            base = 'wss://' + base[8:]
        elif base.startswith('http://'):
            base = 'ws://' + base[7:]
        elif not base.startswith(('ws://', 'wss://')):
            base = 'ws://' + base
        ws_url = f"{base}/ws/manager?token={self._token}"

        while not self._stop_event.is_set():
            try:
                logger.info(f"[AgentServer] 서버 릴레이 접속 시도...")
                async with websockets.connect(
                    ws_url,
                    max_size=50 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                ) as ws:
                    self._ws = ws
                    self._binary_frame_count = 0
                    logger.info("[AgentServer] 서버 릴레이 접속 성공 — 메시지 수신 대기")

                    # 메시지 수신 루프
                    async for message in ws:
                        if self._stop_event.is_set():
                            break

                        if isinstance(message, str):
                            self._handle_relay_text(message)
                        elif isinstance(message, bytes):
                            self._handle_relay_binary(message)

            except websockets.exceptions.ConnectionClosed as e:
                logger.info(f"[AgentServer] 서버 연결 종료: {e}")
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.warning(f"[AgentServer] 서버 연결 오류: {e}")
            finally:
                self._ws = None
                # 모든 에이전트 연결 해제 알림
                for agent_id in list(self._agents.keys()):
                    self.agent_disconnected.emit(agent_id)
                self._agents.clear()

            # 재연결 대기
            if not self._stop_event.is_set():
                logger.info("[AgentServer] 5초 후 재연결...")
                for _ in range(50):
                    if self._stop_event.is_set():
                        return
                    await asyncio.sleep(0.1)

    def _handle_relay_text(self, raw: str):
        """서버에서 릴레이된 JSON 텍스트 메시지 처리"""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get('type', '')
        source_agent = msg.get('source_agent', '')

        # 에이전트 연결/해제 알림 (서버가 전달)
        if msg_type == 'auth':
            agent_id = msg.get('agent_id', source_agent)
            if agent_id:
                hostname = msg.get('hostname', '')
                os_info = msg.get('os_info', '')
                ip = msg.get('ip', '')
                self._agents[agent_id] = {
                    'hostname': hostname,
                    'os_info': os_info,
                    'ip': ip,
                    'screen_width': msg.get('screen_width', 1920),
                    'screen_height': msg.get('screen_height', 1080),
                }
                self.agent_connected.emit(agent_id, ip)
                logger.info(f"[AgentServer] 에이전트 연결 (릴레이): {agent_id}")
            return

        if msg_type == 'agent_connected':
            agent_id = source_agent
            if agent_id and agent_id not in self._agents:
                self._agents[agent_id] = {'hostname': '', 'os_info': '', 'ip': ''}
                self.agent_connected.emit(agent_id, '')
                logger.info(f"[AgentServer] 에이전트 연결 알림: {agent_id}")
            return

        if msg_type == 'agent_disconnected':
            agent_id = source_agent
            if agent_id:
                self._agents.pop(agent_id, None)
                self.agent_disconnected.emit(agent_id)
                logger.info(f"[AgentServer] 에이전트 해제 (릴레이): {agent_id}")
            return

        # 에이전트→매니저 메시지 (source_agent로 식별)
        agent_id = source_agent
        if not agent_id:
            return

        if msg_type == 'stream_started':
            # v2.0.2 — 코덱 협상 응답
            info = {
                'codec': msg.get('codec', 'mjpeg'),
                'encoder': msg.get('encoder', ''),
                'width': msg.get('width', 0),
                'height': msg.get('height', 0),
                'fps': msg.get('fps', 15),
                'quality': msg.get('quality', 60),
            }
            self.stream_started.emit(agent_id, info)
            logger.info(
                f"[AgentServer] 스트림 시작: {agent_id} "
                f"codec={info['codec']}, encoder={info['encoder']}"
            )
            return

        if msg_type == 'clipboard':
            fmt = msg.get('format', '')
            data = msg.get('data', '')
            if fmt == 'text':
                self.clipboard_received.emit(agent_id, 'text', data)
            elif fmt == 'image':
                png_data = base64.b64decode(data)
                self.clipboard_received.emit(agent_id, 'image', png_data)

        elif msg_type == 'pong':
            pass

        elif msg_type == 'file_progress':
            received = msg.get('received', 0)
            total = msg.get('total', 0)
            self.file_progress.emit(agent_id, received, total)

        elif msg_type == 'file_complete':
            remote_path = msg.get('path', '')
            self.file_complete.emit(agent_id, remote_path)

        elif msg_type == 'execute_result':
            command = msg.get('command', '')
            stdout = msg.get('stdout', '')
            stderr = msg.get('stderr', '')
            returncode = msg.get('returncode', -1)
            output = stdout if stdout else stderr
            self.command_result.emit(agent_id, command, output, returncode)

    _binary_frame_count: int = 0  # 디버그: 바이너리 프레임 수신 카운터

    def _handle_relay_binary(self, data: bytes):
        """서버에서 릴레이된 바이너리 메시지 처리

        포맷: agent_id(32바이트) + 원본 바이너리(헤더 + JPEG)
        """
        if len(data) < AGENT_ID_LEN + 2:
            logger.debug(f"[AgentServer] 바이너리 프레임 너무 짧음: {len(data)}B")
            return

        agent_id = _unpad_agent_id(data[:AGENT_ID_LEN])
        payload = data[AGENT_ID_LEN:]

        header = payload[0]
        frame_data = payload[1:]

        self._binary_frame_count += 1
        # 첫 프레임 + 100프레임마다 로깅
        if self._binary_frame_count == 1:
            logger.info(
                f"[AgentServer] 첫 바이너리 프레임: agent={agent_id}, "
                f"header=0x{header:02x}, size={len(frame_data)}B"
            )
        elif self._binary_frame_count % 100 == 0:
            logger.info(
                f"[AgentServer] 바이너리 #{self._binary_frame_count}: "
                f"agent={agent_id}, header=0x{header:02x}, size={len(frame_data)}B"
            )

        if header == self.HEADER_THUMBNAIL:
            self.thumbnail_received.emit(agent_id, frame_data)
        elif header == self.HEADER_STREAM:
            self.screen_frame_received.emit(agent_id, frame_data)
        elif header in (self.HEADER_H264_KEYFRAME, self.HEADER_H264_DELTA):
            # v2.0.2 — H.264 프레임: header(0x03|0x04) + [4B seq + NAL]
            self.h264_frame_received.emit(agent_id, header, frame_data)
