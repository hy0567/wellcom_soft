"""스크립트 엔진 — 자동화 스크립트 파싱/실행

LinkIO 스크립트 + 확장 명령 지원.
명령 목록: click, drag, swipe, key, text, delay, loop, if_image,
           goto, label, log, screenshot, scroll, pinch, command.
"""

import os
import re
import time
import json
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Any, Callable

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)


# ==================== 명령 타입 ====================

class CommandType(Enum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    LONG_PRESS = "long_press"
    DRAG = "drag"
    SWIPE = "swipe"
    SCROLL = "scroll"
    KEY = "key"
    TEXT = "text"
    DELAY = "delay"
    LOOP_START = "loop_start"
    LOOP_END = "loop_end"
    IF_IMAGE = "if_image"
    ELSE = "else"
    ENDIF = "endif"
    LABEL = "label"
    GOTO = "goto"
    LOG = "log"
    SCREENSHOT = "screenshot"
    COMMAND = "command"
    STOP = "stop"


@dataclass
class ScriptCommand:
    """스크립트 명령 한 줄"""
    type: CommandType
    args: Dict[str, Any] = field(default_factory=dict)
    line_number: int = 0
    raw_text: str = ""


@dataclass
class ScriptInfo:
    """스크립트 메타데이터"""
    name: str
    description: str = ""
    commands: List[ScriptCommand] = field(default_factory=list)
    created_at: str = ""
    modified_at: str = ""

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'description': self.description,
            'commands': [
                {
                    'type': cmd.type.value,
                    'args': cmd.args,
                    'raw_text': cmd.raw_text,
                }
                for cmd in self.commands
            ],
            'created_at': self.created_at,
            'modified_at': self.modified_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ScriptInfo':
        info = cls(
            name=data.get('name', ''),
            description=data.get('description', ''),
            created_at=data.get('created_at', ''),
            modified_at=data.get('modified_at', ''),
        )
        for i, cmd_data in enumerate(data.get('commands', [])):
            try:
                cmd = ScriptCommand(
                    type=CommandType(cmd_data['type']),
                    args=cmd_data.get('args', {}),
                    line_number=i + 1,
                    raw_text=cmd_data.get('raw_text', ''),
                )
                info.commands.append(cmd)
            except (ValueError, KeyError):
                pass
        return info


# ==================== 파서 ====================

class ScriptParser:
    """스크립트 텍스트 → ScriptCommand 리스트"""

    @staticmethod
    def parse(text: str) -> List[ScriptCommand]:
        commands = []
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('//'):
                continue
            cmd = ScriptParser._parse_line(line, i)
            if cmd:
                commands.append(cmd)
        return commands

    @staticmethod
    def _parse_line(line: str, line_num: int) -> Optional[ScriptCommand]:
        parts = line.split(None, 1)
        cmd_name = parts[0].lower()
        args_str = parts[1] if len(parts) > 1 else ""

        try:
            cmd_type = CommandType(cmd_name)
        except ValueError:
            # 약어/별칭 처리
            aliases = {
                'tap': CommandType.CLICK, 'dclick': CommandType.DOUBLE_CLICK,
                'lpress': CommandType.LONG_PRESS, 'wait': CommandType.DELAY,
                'sleep': CommandType.DELAY, 'type': CommandType.TEXT,
                'press': CommandType.KEY, 'exec': CommandType.COMMAND,
                'run': CommandType.COMMAND, 'img': CommandType.IF_IMAGE,
            }
            cmd_type = aliases.get(cmd_name)
            if not cmd_type:
                logger.warning(f"알 수 없는 명령 (줄 {line_num}): {cmd_name}")
                return None

        args = ScriptParser._parse_args(cmd_type, args_str)
        return ScriptCommand(
            type=cmd_type, args=args, line_number=line_num, raw_text=line,
        )

    @staticmethod
    def _parse_args(cmd_type: CommandType, args_str: str) -> Dict[str, Any]:
        """명령 타입에 따른 인자 파싱"""
        args_str = args_str.strip()

        if cmd_type in (CommandType.CLICK, CommandType.DOUBLE_CLICK, CommandType.LONG_PRESS):
            # click 100 200 또는 click 100,200
            coords = re.findall(r'[\d.]+', args_str)
            return {
                'x': int(float(coords[0])) if len(coords) > 0 else 0,
                'y': int(float(coords[1])) if len(coords) > 1 else 0,
                'duration': int(float(coords[2]) * 1000) if len(coords) > 2 else 0,
            }

        elif cmd_type == CommandType.DRAG:
            # drag 100 200 300 400
            coords = re.findall(r'[\d.]+', args_str)
            return {
                'x1': int(float(coords[0])) if len(coords) > 0 else 0,
                'y1': int(float(coords[1])) if len(coords) > 1 else 0,
                'x2': int(float(coords[2])) if len(coords) > 2 else 0,
                'y2': int(float(coords[3])) if len(coords) > 3 else 0,
                'duration': int(float(coords[4]) * 1000) if len(coords) > 4 else 500,
            }

        elif cmd_type == CommandType.SWIPE:
            # swipe 100 200 300 400 500
            coords = re.findall(r'[\d.]+', args_str)
            return {
                'x1': int(float(coords[0])) if len(coords) > 0 else 0,
                'y1': int(float(coords[1])) if len(coords) > 1 else 0,
                'x2': int(float(coords[2])) if len(coords) > 2 else 0,
                'y2': int(float(coords[3])) if len(coords) > 3 else 0,
                'duration': int(float(coords[4])) if len(coords) > 4 else 300,
            }

        elif cmd_type == CommandType.SCROLL:
            # scroll up 3 또는 scroll down 5
            parts = args_str.split()
            return {
                'direction': parts[0] if parts else 'down',
                'amount': int(parts[1]) if len(parts) > 1 else 3,
            }

        elif cmd_type == CommandType.KEY:
            # key enter 또는 key ctrl+c
            return {'key': args_str.strip()}

        elif cmd_type == CommandType.TEXT:
            # text "Hello World"
            text = args_str.strip().strip('"').strip("'")
            return {'text': text}

        elif cmd_type == CommandType.DELAY:
            # delay 1000 (ms) 또는 delay 1.5 (초)
            nums = re.findall(r'[\d.]+', args_str)
            if nums:
                val = float(nums[0])
                ms = int(val) if val > 10 else int(val * 1000)
            else:
                ms = 1000
            return {'ms': ms}

        elif cmd_type == CommandType.LOOP_START:
            # loop 5 또는 loop infinite
            if 'inf' in args_str.lower():
                return {'count': -1}
            nums = re.findall(r'\d+', args_str)
            return {'count': int(nums[0]) if nums else 1}

        elif cmd_type == CommandType.IF_IMAGE:
            # if_image "image.png" 0.8
            parts = args_str.split()
            image_path = parts[0].strip('"').strip("'") if parts else ''
            threshold = float(parts[1]) if len(parts) > 1 else 0.8
            return {'image': image_path, 'threshold': threshold}

        elif cmd_type == CommandType.LABEL:
            return {'name': args_str.strip()}

        elif cmd_type == CommandType.GOTO:
            return {'label': args_str.strip()}

        elif cmd_type == CommandType.LOG:
            return {'message': args_str.strip('"').strip("'")}

        elif cmd_type == CommandType.COMMAND:
            return {'command': args_str.strip()}

        return {}

    @staticmethod
    def to_text(commands: List[ScriptCommand]) -> str:
        """명령 리스트 → 텍스트"""
        lines = []
        for cmd in commands:
            lines.append(cmd.raw_text or ScriptParser._command_to_text(cmd))
        return '\n'.join(lines)

    @staticmethod
    def _command_to_text(cmd: ScriptCommand) -> str:
        a = cmd.args
        if cmd.type in (CommandType.CLICK, CommandType.DOUBLE_CLICK, CommandType.LONG_PRESS):
            return f"{cmd.type.value} {a.get('x', 0)} {a.get('y', 0)}"
        elif cmd.type == CommandType.DRAG:
            return f"drag {a.get('x1', 0)} {a.get('y1', 0)} {a.get('x2', 0)} {a.get('y2', 0)}"
        elif cmd.type == CommandType.SWIPE:
            return f"swipe {a.get('x1', 0)} {a.get('y1', 0)} {a.get('x2', 0)} {a.get('y2', 0)} {a.get('duration', 300)}"
        elif cmd.type == CommandType.SCROLL:
            return f"scroll {a.get('direction', 'down')} {a.get('amount', 3)}"
        elif cmd.type == CommandType.KEY:
            return f"key {a.get('key', '')}"
        elif cmd.type == CommandType.TEXT:
            return f'text "{a.get("text", "")}"'
        elif cmd.type == CommandType.DELAY:
            return f"delay {a.get('ms', 1000)}"
        elif cmd.type == CommandType.LOOP_START:
            count = a.get('count', 1)
            return f"loop {'infinite' if count < 0 else count}"
        elif cmd.type == CommandType.LOOP_END:
            return "loop_end"
        elif cmd.type == CommandType.IF_IMAGE:
            return f'if_image "{a.get("image", "")}" {a.get("threshold", 0.8)}'
        elif cmd.type == CommandType.ELSE:
            return "else"
        elif cmd.type == CommandType.ENDIF:
            return "endif"
        elif cmd.type == CommandType.LABEL:
            return f"label {a.get('name', '')}"
        elif cmd.type == CommandType.GOTO:
            return f"goto {a.get('label', '')}"
        elif cmd.type == CommandType.LOG:
            return f'log "{a.get("message", "")}"'
        elif cmd.type == CommandType.COMMAND:
            return f"command {a.get('command', '')}"
        elif cmd.type == CommandType.STOP:
            return "stop"
        return cmd.type.value


# ==================== 이미지 매칭 ====================

class ImageMatcher:
    """OpenCV 기반 이미지 매칭"""

    @staticmethod
    def match(screenshot: bytes, template_path: str, threshold: float = 0.8) -> Optional[tuple]:
        """스크린샷에서 템플릿 이미지 찾기 → (x, y, confidence) 또는 None"""
        try:
            import cv2
            import numpy as np

            if not os.path.exists(template_path):
                logger.warning(f"템플릿 이미지 없음: {template_path}")
                return None

            # 스크린샷 디코딩
            nparr = np.frombuffer(screenshot, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return None

            # 템플릿 로드
            template = cv2.imread(template_path, cv2.IMREAD_COLOR)
            if template is None:
                return None

            # 매칭
            result = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

            if max_val >= threshold:
                th, tw = template.shape[:2]
                cx = max_loc[0] + tw // 2
                cy = max_loc[1] + th // 2
                return (cx, cy, max_val)

            return None

        except ImportError:
            logger.warning("opencv-python 미설치 — 이미지 매칭 비활성화")
            return None
        except Exception as e:
            logger.error(f"이미지 매칭 오류: {e}")
            return None


# ==================== 스크립트 실행 엔진 ====================

class ScriptEngine(QObject):
    """스크립트 실행 엔진"""

    # 시그널
    started = pyqtSignal(str)              # script_name
    stopped = pyqtSignal(str)              # script_name
    progress = pyqtSignal(str, int, int)   # script_name, current_line, total_lines
    log_message = pyqtSignal(str, str)     # script_name, message
    error = pyqtSignal(str, str)           # script_name, error_message

    def __init__(self, agent_server, scripts_dir: str = ""):
        super().__init__()
        self.agent_server = agent_server
        self.scripts_dir = scripts_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'scripts'
        )
        os.makedirs(self.scripts_dir, exist_ok=True)

        self._running: Dict[str, threading.Event] = {}  # agent_id → stop event
        self._threads: Dict[str, threading.Thread] = {}
        self._scripts: Dict[str, ScriptInfo] = {}
        self._load_scripts()

    def _load_scripts(self):
        """스크립트 목록 로드"""
        index_path = os.path.join(self.scripts_dir, 'scripts.json')
        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for item in data.get('scripts', []):
                    info = ScriptInfo.from_dict(item)
                    if info.name:
                        self._scripts[info.name] = info
            except Exception as e:
                logger.error(f"스크립트 목록 로드 실패: {e}")

    def _save_scripts(self):
        """스크립트 목록 저장"""
        index_path = os.path.join(self.scripts_dir, 'scripts.json')
        try:
            data = {
                'scripts': [s.to_dict() for s in self._scripts.values()]
            }
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"스크립트 목록 저장 실패: {e}")

    # ==================== 스크립트 관리 ====================

    def get_scripts(self) -> List[ScriptInfo]:
        return list(self._scripts.values())

    def get_script(self, name: str) -> Optional[ScriptInfo]:
        return self._scripts.get(name)

    def add_script(self, name: str, text: str = "", description: str = "") -> ScriptInfo:
        """새 스크립트 추가"""
        from datetime import datetime
        now = datetime.now().isoformat()
        info = ScriptInfo(
            name=name,
            description=description,
            commands=ScriptParser.parse(text) if text else [],
            created_at=now,
            modified_at=now,
        )
        self._scripts[name] = info
        self._save_scripts()
        return info

    def update_script(self, name: str, text: str):
        """스크립트 내용 업데이트"""
        from datetime import datetime
        info = self._scripts.get(name)
        if not info:
            return
        info.commands = ScriptParser.parse(text)
        info.modified_at = datetime.now().isoformat()
        self._save_scripts()

    def delete_script(self, name: str):
        """스크립트 삭제"""
        self._scripts.pop(name, None)
        self._save_scripts()

    def rename_script(self, old_name: str, new_name: str) -> bool:
        info = self._scripts.pop(old_name, None)
        if not info:
            return False
        info.name = new_name
        self._scripts[new_name] = info
        self._save_scripts()
        return True

    # ==================== 실행 ====================

    def run_script(self, script_name: str, agent_id: str,
                   get_screenshot: Callable = None):
        """스크립트 실행 (백그라운드 스레드)"""
        key = f"{agent_id}:{script_name}"
        if key in self._running:
            logger.warning(f"이미 실행 중: {key}")
            return

        info = self._scripts.get(script_name)
        if not info or not info.commands:
            self.error.emit(script_name, "스크립트가 비어있거나 존재하지 않습니다.")
            return

        stop_event = threading.Event()
        self._running[key] = stop_event

        thread = threading.Thread(
            target=self._execute,
            args=(info, agent_id, stop_event, get_screenshot),
            daemon=True,
            name=f"Script-{key}",
        )
        self._threads[key] = thread
        thread.start()
        self.started.emit(script_name)
        self.log_message.emit(script_name, f"스크립트 시작: {script_name} → {agent_id}")

    def stop_script(self, script_name: str, agent_id: str):
        """스크립트 중지"""
        key = f"{agent_id}:{script_name}"
        stop_event = self._running.pop(key, None)
        if stop_event:
            stop_event.set()
        self._threads.pop(key, None)
        self.stopped.emit(script_name)
        self.log_message.emit(script_name, f"스크립트 중지: {script_name}")

    def stop_all(self, agent_id: str = None):
        """모든 스크립트 중지"""
        keys = list(self._running.keys())
        for key in keys:
            if agent_id and not key.startswith(f"{agent_id}:"):
                continue
            stop_event = self._running.pop(key, None)
            if stop_event:
                stop_event.set()
            self._threads.pop(key, None)
            script_name = key.split(':', 1)[1] if ':' in key else key
            self.stopped.emit(script_name)

    def is_running(self, script_name: str = None, agent_id: str = None) -> bool:
        if script_name and agent_id:
            return f"{agent_id}:{script_name}" in self._running
        if agent_id:
            return any(k.startswith(f"{agent_id}:") for k in self._running)
        return len(self._running) > 0

    def _execute(self, script: ScriptInfo, agent_id: str,
                 stop_event: threading.Event,
                 get_screenshot: Callable = None):
        """스크립트 실행 루프"""
        commands = script.commands
        total = len(commands)
        pc = 0  # program counter
        loop_stack = []  # (loop_start_pc, remaining_count)
        labels = {}  # label_name → pc

        # 라벨 인덱스 구축
        for i, cmd in enumerate(commands):
            if cmd.type == CommandType.LABEL:
                labels[cmd.args.get('name', '')] = i

        key = f"{agent_id}:{script.name}"

        try:
            while pc < total and not stop_event.is_set():
                cmd = commands[pc]
                self.progress.emit(script.name, pc + 1, total)

                if cmd.type == CommandType.CLICK:
                    self.agent_server.send_mouse_event(
                        agent_id, cmd.args['x'], cmd.args['y'],
                        button='left', action='click',
                    )

                elif cmd.type == CommandType.DOUBLE_CLICK:
                    self.agent_server.send_mouse_event(
                        agent_id, cmd.args['x'], cmd.args['y'],
                        button='left', action='click',
                    )
                    time.sleep(0.05)
                    self.agent_server.send_mouse_event(
                        agent_id, cmd.args['x'], cmd.args['y'],
                        button='left', action='click',
                    )

                elif cmd.type == CommandType.LONG_PRESS:
                    self.agent_server.send_mouse_event(
                        agent_id, cmd.args['x'], cmd.args['y'],
                        button='left', action='press',
                    )
                    duration = cmd.args.get('duration', 1000) / 1000.0
                    self._interruptible_sleep(stop_event, duration)
                    self.agent_server.send_mouse_event(
                        agent_id, cmd.args['x'], cmd.args['y'],
                        button='left', action='release',
                    )

                elif cmd.type == CommandType.DRAG:
                    self.agent_server.send_mouse_event(
                        agent_id, cmd.args['x1'], cmd.args['y1'],
                        button='left', action='press',
                    )
                    time.sleep(0.05)
                    self.agent_server.send_mouse_event(
                        agent_id, cmd.args['x2'], cmd.args['y2'],
                        button='left', action='move',
                    )
                    time.sleep(0.05)
                    self.agent_server.send_mouse_event(
                        agent_id, cmd.args['x2'], cmd.args['y2'],
                        button='left', action='release',
                    )

                elif cmd.type == CommandType.SWIPE:
                    # 시작점에서 끝점까지 부드럽게 이동
                    x1, y1 = cmd.args['x1'], cmd.args['y1']
                    x2, y2 = cmd.args['x2'], cmd.args['y2']
                    duration_ms = cmd.args.get('duration', 300)
                    steps = max(5, duration_ms // 20)

                    self.agent_server.send_mouse_event(
                        agent_id, x1, y1, button='left', action='press',
                    )
                    for step in range(1, steps + 1):
                        if stop_event.is_set():
                            break
                        t = step / steps
                        cx = int(x1 + (x2 - x1) * t)
                        cy = int(y1 + (y2 - y1) * t)
                        self.agent_server.send_mouse_event(
                            agent_id, cx, cy, button='left', action='move',
                        )
                        time.sleep(duration_ms / steps / 1000.0)
                    self.agent_server.send_mouse_event(
                        agent_id, x2, y2, button='left', action='release',
                    )

                elif cmd.type == CommandType.SCROLL:
                    direction = cmd.args.get('direction', 'down')
                    amount = cmd.args.get('amount', 3)
                    delta = amount if direction == 'down' else -amount
                    self.agent_server.send_mouse_event(
                        agent_id, 0, 0, action='scroll', scroll_delta=delta,
                    )

                elif cmd.type == CommandType.KEY:
                    key_str = cmd.args.get('key', '')
                    modifiers = []
                    parts = key_str.split('+')
                    actual_key = parts[-1].strip()
                    for mod in parts[:-1]:
                        mod = mod.strip().lower()
                        if mod in ('ctrl', 'control'):
                            modifiers.append('ctrl')
                        elif mod in ('alt',):
                            modifiers.append('alt')
                        elif mod in ('shift',):
                            modifiers.append('shift')
                        elif mod in ('win', 'super', 'meta'):
                            modifiers.append('win')
                    self.agent_server.send_key_event(
                        agent_id, actual_key, 'press', modifiers,
                    )

                elif cmd.type == CommandType.TEXT:
                    text = cmd.args.get('text', '')
                    self.agent_server.send_clipboard_text(agent_id, text)
                    time.sleep(0.1)
                    self.agent_server.send_key_event(
                        agent_id, 'v', 'press', ['ctrl'],
                    )

                elif cmd.type == CommandType.DELAY:
                    ms = cmd.args.get('ms', 1000)
                    self._interruptible_sleep(stop_event, ms / 1000.0)

                elif cmd.type == CommandType.LOOP_START:
                    count = cmd.args.get('count', 1)
                    loop_stack.append((pc, count))

                elif cmd.type == CommandType.LOOP_END:
                    if loop_stack:
                        loop_pc, remaining = loop_stack[-1]
                        if remaining < 0:
                            # 무한 루프
                            pc = loop_pc
                            continue
                        elif remaining > 1:
                            loop_stack[-1] = (loop_pc, remaining - 1)
                            pc = loop_pc
                            continue
                        else:
                            loop_stack.pop()

                elif cmd.type == CommandType.IF_IMAGE:
                    found = False
                    if get_screenshot:
                        screenshot = get_screenshot(agent_id)
                        if screenshot:
                            image_path = cmd.args.get('image', '')
                            if not os.path.isabs(image_path):
                                image_path = os.path.join(self.scripts_dir, image_path)
                            threshold = cmd.args.get('threshold', 0.8)
                            result = ImageMatcher.match(screenshot, image_path, threshold)
                            found = result is not None

                    if not found:
                        # else 또는 endif까지 건너뛰기
                        depth = 1
                        while pc + 1 < total and depth > 0:
                            pc += 1
                            if commands[pc].type == CommandType.IF_IMAGE:
                                depth += 1
                            elif commands[pc].type == CommandType.ELSE and depth == 1:
                                break
                            elif commands[pc].type == CommandType.ENDIF:
                                depth -= 1

                elif cmd.type == CommandType.ELSE:
                    # if 블록이 실행됐으면 endif까지 건너뛰기
                    depth = 1
                    while pc + 1 < total and depth > 0:
                        pc += 1
                        if commands[pc].type == CommandType.IF_IMAGE:
                            depth += 1
                        elif commands[pc].type == CommandType.ENDIF:
                            depth -= 1

                elif cmd.type == CommandType.ENDIF:
                    pass

                elif cmd.type == CommandType.LABEL:
                    pass

                elif cmd.type == CommandType.GOTO:
                    target = cmd.args.get('label', '')
                    if target in labels:
                        pc = labels[target]
                        continue
                    else:
                        self.log_message.emit(script.name, f"라벨 없음: {target}")

                elif cmd.type == CommandType.LOG:
                    msg = cmd.args.get('message', '')
                    self.log_message.emit(script.name, msg)

                elif cmd.type == CommandType.SCREENSHOT:
                    self.agent_server.request_thumbnail(agent_id)

                elif cmd.type == CommandType.COMMAND:
                    command = cmd.args.get('command', '')
                    self.agent_server.execute_command(agent_id, command)

                elif cmd.type == CommandType.STOP:
                    break

                pc += 1

        except Exception as e:
            self.error.emit(script.name, f"실행 오류 (줄 {pc + 1}): {e}")
            logger.error(f"스크립트 오류 [{script.name}]: {e}", exc_info=True)
        finally:
            self._running.pop(key, None)
            self._threads.pop(key, None)
            self.stopped.emit(script.name)
            self.log_message.emit(script.name, "스크립트 종료")

    @staticmethod
    def _interruptible_sleep(stop_event: threading.Event, seconds: float):
        """중단 가능한 sleep"""
        end = time.time() + seconds
        while time.time() < end:
            if stop_event.is_set():
                return
            time.sleep(min(0.05, end - time.time()))
