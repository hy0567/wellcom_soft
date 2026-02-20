"""파일 수신 및 저장"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class FileReceiver:
    """WebSocket을 통해 파일을 수신하여 로컬에 저장"""

    def __init__(self, save_dir: str):
        self.save_dir = Path(save_dir)
        self._current_file = None
        self._current_name = None
        self._current_size = 0
        self._received_bytes = 0

    def begin_file(self, name: str, size: int) -> bool:
        """파일 수신 시작"""
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            save_path = self._get_unique_path(name)
            self._current_file = open(save_path, 'wb')
            self._current_name = save_path.name
            self._current_size = size
            self._received_bytes = 0
            logger.info(f"파일 수신 시작: {self._current_name} ({size} bytes)")
            return True
        except Exception as e:
            logger.error(f"파일 수신 시작 실패: {e}")
            return False

    def write_chunk(self, data: bytes) -> int:
        """파일 청크 쓰기"""
        if not self._current_file:
            return 0
        self._current_file.write(data)
        self._received_bytes += len(data)
        return self._received_bytes

    def finish_file(self) -> Optional[str]:
        """파일 수신 완료"""
        if not self._current_file:
            return None

        try:
            name = self._current_name
            self._current_file.close()
            save_path = self.save_dir / name
            logger.info(
                f"파일 수신 완료: {name} "
                f"({self._received_bytes} / {self._current_size} bytes)"
            )
            return str(save_path)
        except Exception as e:
            logger.error(f"파일 수신 완료 실패: {e}")
            return None
        finally:
            self._current_file = None
            self._current_name = None
            self._current_size = 0
            self._received_bytes = 0

    def cancel(self):
        """수신 취소"""
        if self._current_file:
            try:
                path = self.save_dir / self._current_name
                self._current_file.close()
                if path.exists():
                    path.unlink()
            except Exception:
                pass
            self._current_file = None
            self._current_name = None

    @property
    def progress(self) -> float:
        if self._current_size <= 0:
            return 0.0
        return min(1.0, self._received_bytes / self._current_size)

    @property
    def is_receiving(self) -> bool:
        return self._current_file is not None

    def _get_unique_path(self, name: str) -> Path:
        """중복 파일명 방지"""
        path = self.save_dir / name
        if not path.exists():
            return path

        stem = path.stem
        suffix = path.suffix
        counter = 1
        while True:
            new_path = self.save_dir / f"{stem} ({counter}){suffix}"
            if not new_path.exists():
                return new_path
            counter += 1
