"""고속 화면 캡처 엔진 (mss 라이브러리)"""

import io
from typing import Tuple

import mss
from PIL import Image


class ScreenCapture:
    """고성능 화면 캡처"""

    def __init__(self):
        self._sct = mss.mss()
        self._monitor = self._sct.monitors[1]  # 주 모니터

    @property
    def screen_size(self) -> Tuple[int, int]:
        return self._monitor['width'], self._monitor['height']

    def capture_jpeg(self, quality: int = 60, scale: float = 1.0) -> bytes:
        """화면 캡처 → JPEG 바이트

        Args:
            quality: JPEG 품질 (1-100)
            scale: 리사이즈 비율 (0.2 = 썸네일, 1.0 = 원본)
        """
        screenshot = self._sct.grab(self._monitor)
        img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')

        if scale < 1.0:
            new_w = int(img.width * scale)
            new_h = int(img.height * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality, optimize=True)
        return buf.getvalue()

    def capture_thumbnail(self, max_width: int = 320, quality: int = 30) -> bytes:
        """저해상도 썸네일 캡처"""
        screenshot = self._sct.grab(self._monitor)
        img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')

        ratio = max_width / img.width
        new_h = int(img.height * ratio)
        img = img.resize((max_width, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality)
        return buf.getvalue()

    def close(self):
        self._sct.close()
