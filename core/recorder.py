"""녹화/재생 시스템 — 마우스/키보드 이벤트 녹화 및 재생

LinkIO 녹화 패턴 참고:
  녹화: 키보드/마우스/키매핑 이벤트를 타임스탬프와 함께 기록
  재생: 랜덤 딜레이 옵션, 반복 재생, 에이전트별 재생
"""

import json
import os
import time
import logging
import threading
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum

from PyQt6.QtCore import QObject, pyqtSignal

from config import settings

logger = logging.getLogger(__name__)


class RecordEventType(Enum):
    """녹화 이벤트 타입"""
    MOUSE_CLICK = "mouse_click"
    MOUSE_DOUBLE_CLICK = "mouse_double_click"
    MOUSE_MOVE = "mouse_move"
    MOUSE_PRESS = "mouse_press"
    MOUSE_RELEASE = "mouse_release"
    MOUSE_SCROLL = "mouse_scroll"
    KEY_PRESS = "key_press"
    KEY_RELEASE = "key_release"
    DELAY = "delay"


@dataclass
class RecordEvent:
    """녹화된 단일 이벤트"""
    type: RecordEventType
    timestamp: float           # 녹화 시작 기준 경과 시간 (초)
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'type': self.type.value,
            'timestamp': self.timestamp,
            'data': self.data,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'RecordEvent':
        return cls(
            type=RecordEventType(data['type']),
            timestamp=data.get('timestamp', 0.0),
            data=data.get('data', {}),
        )


@dataclass
class Recording:
    """녹화 데이터"""
    name: str
    events: List[RecordEvent] = field(default_factory=list)
    duration: float = 0.0
    description: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            'name': self.name,
            'events': [e.to_dict() for e in self.events],
            'duration': self.duration,
            'description': self.description,
            'created_at': self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Recording':
        rec = cls(
            name=data.get('name', ''),
            duration=data.get('duration', 0.0),
            description=data.get('description', ''),
            created_at=data.get('created_at', ''),
        )
        for ev in data.get('events', []):
            try:
                rec.events.append(RecordEvent.from_dict(ev))
            except (ValueError, KeyError):
                pass
        return rec


class Recorder(QObject):
    """이벤트 녹화기"""

    recording_started = pyqtSignal(str)    # recording_name
    recording_stopped = pyqtSignal(str)    # recording_name
    event_recorded = pyqtSignal(int)       # event_count

    def __init__(self):
        super().__init__()
        self._recording = False
        self._current_name = ""
        self._events: List[RecordEvent] = []
        self._start_time = 0.0

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self, name: str):
        """녹화 시작"""
        self._recording = True
        self._current_name = name
        self._events = []
        self._start_time = time.time()
        self.recording_started.emit(name)
        logger.info(f"녹화 시작: {name}")

    def stop(self) -> Optional[Recording]:
        """녹화 중지 → Recording 반환"""
        if not self._recording:
            return None

        self._recording = False
        duration = time.time() - self._start_time

        from datetime import datetime
        recording = Recording(
            name=self._current_name,
            events=list(self._events),
            duration=duration,
            created_at=datetime.now().isoformat(),
        )

        self.recording_stopped.emit(self._current_name)
        logger.info(f"녹화 종료: {self._current_name} ({len(self._events)}개 이벤트, {duration:.1f}초)")

        self._events = []
        self._current_name = ""
        return recording

    def record_mouse_event(self, x: int, y: int, button: str, action: str,
                           scroll_delta: int = 0):
        """마우스 이벤트 기록"""
        if not self._recording:
            return

        if action == 'click':
            event_type = RecordEventType.MOUSE_CLICK
        elif action == 'press':
            event_type = RecordEventType.MOUSE_PRESS
        elif action == 'release':
            event_type = RecordEventType.MOUSE_RELEASE
        elif action == 'scroll':
            event_type = RecordEventType.MOUSE_SCROLL
        elif action == 'move':
            event_type = RecordEventType.MOUSE_MOVE
        else:
            return

        event = RecordEvent(
            type=event_type,
            timestamp=time.time() - self._start_time,
            data={
                'x': x, 'y': y,
                'button': button,
                'scroll_delta': scroll_delta,
            },
        )
        self._events.append(event)
        self.event_recorded.emit(len(self._events))

    def record_key_event(self, key: str, action: str, modifiers: list = None):
        """키보드 이벤트 기록"""
        if not self._recording:
            return

        event_type = (RecordEventType.KEY_PRESS if action == 'press'
                      else RecordEventType.KEY_RELEASE)

        event = RecordEvent(
            type=event_type,
            timestamp=time.time() - self._start_time,
            data={
                'key': key,
                'modifiers': modifiers or [],
            },
        )
        self._events.append(event)
        self.event_recorded.emit(len(self._events))


class Player(QObject):
    """녹화 재생기"""

    playback_started = pyqtSignal(str)     # recording_name
    playback_stopped = pyqtSignal(str)     # recording_name
    playback_progress = pyqtSignal(str, int, int)  # name, current, total

    def __init__(self, agent_server):
        super().__init__()
        self.agent_server = agent_server
        self._running: Dict[str, threading.Event] = {}  # agent_id → stop_event
        self._threads: Dict[str, threading.Thread] = {}

    def play(self, recording: Recording, agent_id: str,
             repeat: int = 1, random_delay: bool = False):
        """녹화 재생 (백그라운드 스레드)"""
        key = f"{agent_id}:{recording.name}"
        if key in self._running:
            return

        stop_event = threading.Event()
        self._running[key] = stop_event

        thread = threading.Thread(
            target=self._play_loop,
            args=(recording, agent_id, stop_event, repeat, random_delay),
            daemon=True,
            name=f"Player-{key}",
        )
        self._threads[key] = thread
        thread.start()
        self.playback_started.emit(recording.name)

    def stop(self, recording_name: str = None, agent_id: str = None):
        """재생 중지"""
        keys_to_stop = []
        for key in list(self._running.keys()):
            if recording_name and agent_id:
                if key == f"{agent_id}:{recording_name}":
                    keys_to_stop.append(key)
            elif agent_id:
                if key.startswith(f"{agent_id}:"):
                    keys_to_stop.append(key)
            else:
                keys_to_stop.append(key)

        for key in keys_to_stop:
            stop_event = self._running.pop(key, None)
            if stop_event:
                stop_event.set()
            self._threads.pop(key, None)
            name = key.split(':', 1)[1] if ':' in key else key
            self.playback_stopped.emit(name)

    def is_playing(self, agent_id: str = None) -> bool:
        if agent_id:
            return any(k.startswith(f"{agent_id}:") for k in self._running)
        return len(self._running) > 0

    def _play_loop(self, recording: Recording, agent_id: str,
                   stop_event: threading.Event, repeat: int, random_delay: bool):
        """재생 루프"""
        key = f"{agent_id}:{recording.name}"
        events = recording.events
        total = len(events)

        try:
            for iteration in range(repeat if repeat > 0 else 999999):
                if stop_event.is_set():
                    break

                # 랜덤 딜레이 후 시작
                if random_delay and iteration > 0:
                    import random
                    delay_min = settings.get('multi_control.random_delay_min', 300) / 1000.0
                    delay_max = settings.get('multi_control.random_delay_max', 2000) / 1000.0
                    delay = random.uniform(delay_min, delay_max)
                    self._interruptible_sleep(stop_event, delay)

                prev_time = 0.0

                for i, event in enumerate(events):
                    if stop_event.is_set():
                        break

                    # 이벤트 간 딜레이
                    delta = event.timestamp - prev_time
                    if delta > 0:
                        self._interruptible_sleep(stop_event, delta)
                        if stop_event.is_set():
                            break
                    prev_time = event.timestamp

                    # 이벤트 실행
                    self._execute_event(event, agent_id)
                    self.playback_progress.emit(recording.name, i + 1, total)

        except Exception as e:
            logger.error(f"재생 오류 [{recording.name}]: {e}", exc_info=True)
        finally:
            self._running.pop(key, None)
            self._threads.pop(key, None)
            self.playback_stopped.emit(recording.name)

    def _execute_event(self, event: RecordEvent, agent_id: str):
        """단일 이벤트 실행"""
        d = event.data

        if event.type == RecordEventType.MOUSE_CLICK:
            self.agent_server.send_mouse_event(
                agent_id, d['x'], d['y'],
                button=d.get('button', 'left'), action='click',
            )

        elif event.type == RecordEventType.MOUSE_DOUBLE_CLICK:
            self.agent_server.send_mouse_event(
                agent_id, d['x'], d['y'],
                button=d.get('button', 'left'), action='click',
            )
            time.sleep(0.05)
            self.agent_server.send_mouse_event(
                agent_id, d['x'], d['y'],
                button=d.get('button', 'left'), action='click',
            )

        elif event.type == RecordEventType.MOUSE_PRESS:
            self.agent_server.send_mouse_event(
                agent_id, d['x'], d['y'],
                button=d.get('button', 'left'), action='press',
            )

        elif event.type == RecordEventType.MOUSE_RELEASE:
            self.agent_server.send_mouse_event(
                agent_id, d['x'], d['y'],
                button=d.get('button', 'left'), action='release',
            )

        elif event.type == RecordEventType.MOUSE_MOVE:
            self.agent_server.send_mouse_event(
                agent_id, d['x'], d['y'],
                button='none', action='move',
            )

        elif event.type == RecordEventType.MOUSE_SCROLL:
            self.agent_server.send_mouse_event(
                agent_id, d.get('x', 0), d.get('y', 0),
                action='scroll', scroll_delta=d.get('scroll_delta', 0),
            )

        elif event.type == RecordEventType.KEY_PRESS:
            self.agent_server.send_key_event(
                agent_id, d['key'], 'press', d.get('modifiers', []),
            )

        elif event.type == RecordEventType.KEY_RELEASE:
            self.agent_server.send_key_event(
                agent_id, d['key'], 'release', d.get('modifiers', []),
            )

    @staticmethod
    def _interruptible_sleep(stop_event: threading.Event, seconds: float):
        end = time.time() + seconds
        while time.time() < end:
            if stop_event.is_set():
                return
            time.sleep(min(0.05, end - time.time()))


class RecordingManager(QObject):
    """녹화 파일 관리"""

    recordings_changed = pyqtSignal()

    def __init__(self, data_dir: str = ""):
        super().__init__()
        self.data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'recordings'
        )
        os.makedirs(self.data_dir, exist_ok=True)
        self._recordings: Dict[str, Recording] = {}
        self._load()

    def _load(self):
        index_path = os.path.join(self.data_dir, 'recordings.json')
        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for item in data.get('recordings', []):
                    rec = Recording.from_dict(item)
                    if rec.name:
                        self._recordings[rec.name] = rec
            except Exception as e:
                logger.error(f"녹화 목록 로드 실패: {e}")

    def _save(self):
        index_path = os.path.join(self.data_dir, 'recordings.json')
        try:
            data = {
                'recordings': [r.to_dict() for r in self._recordings.values()]
            }
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"녹화 목록 저장 실패: {e}")

    def get_recordings(self) -> List[Recording]:
        return list(self._recordings.values())

    def get_recording(self, name: str) -> Optional[Recording]:
        return self._recordings.get(name)

    def add_recording(self, recording: Recording):
        self._recordings[recording.name] = recording
        self._save()
        self.recordings_changed.emit()

    def delete_recording(self, name: str):
        self._recordings.pop(name, None)
        self._save()
        self.recordings_changed.emit()

    def rename_recording(self, old_name: str, new_name: str) -> bool:
        rec = self._recordings.pop(old_name, None)
        if not rec:
            return False
        rec.name = new_name
        self._recordings[new_name] = rec
        self._save()
        self.recordings_changed.emit()
        return True
