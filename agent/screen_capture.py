"""고속 화면 캡처 엔진 (mss 라이브러리)

안정성 개선:
- mss 초기화 실패 시 자동 재시도
- 캡처 실패 시 mss 재초기화
- 모든 에러에 대한 상세 로깅
- 최후 수단: 단색 플레이스홀더 이미지 반환
"""

import io
import logging
from typing import Tuple, Optional

logger = logging.getLogger('WellcomAgent.ScreenCapture')

try:
    import mss
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False
    logger.error("mss 패키지 미설치: pip install mss")

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.error("Pillow 패키지 미설치: pip install Pillow")


class ScreenCapture:
    """고성능 화면 캡처 (mss 기반, 에러 복구 내장)"""

    def __init__(self):
        self._sct: Optional[object] = None
        self._monitor: Optional[dict] = None
        self._screen_w = 1920
        self._screen_h = 1080
        self._init_count = 0
        self._init_mss()

    def _init_mss(self) -> bool:
        """mss 초기화 (재시도 가능)"""
        self._init_count += 1

        # 기존 인스턴스 정리
        if self._sct:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
            self._monitor = None

        if not MSS_AVAILABLE:
            logger.error(f"[ScreenCapture] mss 미설치 — 캡처 불가 (시도 #{self._init_count})")
            return False

        try:
            self._sct = mss.mss()
            monitors = self._sct.monitors
            logger.info(f"[ScreenCapture] mss 초기화 성공 (시도 #{self._init_count}), 모니터 {len(monitors)}개")

            if len(monitors) > 1:
                self._monitor = monitors[1]  # 주 모니터
            elif len(monitors) == 1:
                self._monitor = monitors[0]  # 전체 가상 화면
            else:
                logger.error(f"[ScreenCapture] 모니터를 찾을 수 없음")
                return False

            self._screen_w = self._monitor['width']
            self._screen_h = self._monitor['height']
            logger.info(f"[ScreenCapture] 모니터: {self._screen_w}x{self._screen_h}")
            return True

        except Exception as e:
            logger.error(f"[ScreenCapture] mss 초기화 실패: {type(e).__name__}: {e}")
            self._sct = None
            self._monitor = None
            return False

    @property
    def screen_size(self) -> Tuple[int, int]:
        return self._screen_w, self._screen_h

    def _create_placeholder(self, width: int, height: int, text: str = "캡처 실패") -> bytes:
        """캡처 실패 시 플레이스홀더 이미지 생성"""
        if not PIL_AVAILABLE:
            # PIL 없으면 최소 JPEG 반환 (1x1 픽셀)
            return (
                b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
                b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t'
                b'\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a'
                b'\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342'
                b'\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00'
                b'\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00'
                b'\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b'
                b'\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04'
                b'\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa'
                b'\x07"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n'
                b'\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz'
                b'\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99'
                b'\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7'
                b'\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5'
                b'\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1'
                b'\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa'
                b'\xff\xda\x00\x08\x01\x01\x00\x00?\x00T\xdb\xa8\xa0\x03\xfe\xfb?'
                b'\xff\xd9'
            )

        try:
            img = Image.new('RGB', (width, height), (40, 40, 40))
            draw = ImageDraw.Draw(img)
            # 텍스트 중앙 정렬
            try:
                bbox = draw.textbbox((0, 0), text)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
            except Exception:
                tw, th = len(text) * 7, 12
            x = (width - tw) // 2
            y = (height - th) // 2
            draw.text((x, y), text, fill=(200, 200, 200))
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=30)
            return buf.getvalue()
        except Exception as e:
            logger.error(f"[ScreenCapture] 플레이스홀더 생성 실패: {e}")
            # 최소 바이트 반환
            return b''

    def capture_raw(self):
        """화면 캡처 → PIL Image (RGB) 반환 (H.264 인코더용)

        JPEG 인코딩을 거치지 않고 PIL Image를 직접 반환.
        H.264 인코더가 numpy 변환 후 인코딩에 사용.

        Returns:
            PIL.Image (RGB) 또는 None (캡처 실패)
        """
        if not PIL_AVAILABLE:
            return None

        if not self._sct or not self._monitor:
            if not self._init_mss():
                return None

        try:
            screenshot = self._sct.grab(self._monitor)
            img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')
            return img
        except Exception as e:
            logger.error(f"[ScreenCapture] capture_raw 실패: {type(e).__name__}: {e}")
            self._init_mss()
            return None

    def capture_jpeg(self, quality: int = 60, scale: float = 1.0) -> bytes:
        """화면 캡처 → JPEG 바이트

        Args:
            quality: JPEG 품질 (1-100)
            scale: 리사이즈 비율 (0.2 = 썸네일, 1.0 = 원본)
        """
        if not PIL_AVAILABLE:
            return self._create_placeholder(320, 180, "Pillow 미설치")

        # mss가 초기화 안 됐으면 재시도
        if not self._sct or not self._monitor:
            if not self._init_mss():
                return self._create_placeholder(320, 180, "mss 초기화 실패")

        try:
            screenshot = self._sct.grab(self._monitor)
            img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')

            if scale < 1.0:
                new_w = int(img.width * scale)
                new_h = int(img.height * scale)
                # v2.1.1: BILINEAR (LANCZOS 대비 2-3배 빠름, 스트리밍에 충분)
                img = img.resize((new_w, new_h), Image.BILINEAR)

            buf = io.BytesIO()
            # v2.1.1: optimize=False (CPU 절감, 스트리밍 속도 우선)
            img.save(buf, format='JPEG', quality=quality)
            return buf.getvalue()

        except Exception as e:
            logger.error(f"[ScreenCapture] capture_jpeg 실패: {type(e).__name__}: {e}")
            # mss 재초기화 시도 (다음 호출 시 사용)
            self._init_mss()
            return self._create_placeholder(320, 180, f"캡처 오류: {type(e).__name__}")

    def capture_thumbnail(self, max_width: int = 320, quality: int = 30) -> bytes:
        """저해상도 썸네일 캡처"""
        if not PIL_AVAILABLE:
            return self._create_placeholder(max_width, int(max_width * 9 / 16), "Pillow 미설치")

        # mss가 초기화 안 됐으면 재시도
        if not self._sct or not self._monitor:
            logger.warning(f"[ScreenCapture] 썸네일 캡처 — mss 미초기화, 재시도")
            if not self._init_mss():
                return self._create_placeholder(max_width, int(max_width * 9 / 16), "mss 초기화 실패")

        try:
            screenshot = self._sct.grab(self._monitor)
            img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')

            ratio = max_width / img.width
            new_h = int(img.height * ratio)
            img = img.resize((max_width, new_h), Image.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=quality)
            result = buf.getvalue()
            logger.debug(f"[ScreenCapture] 썸네일 캡처 성공: {max_width}x{new_h}, {len(result)}B")
            return result

        except Exception as e:
            logger.error(f"[ScreenCapture] capture_thumbnail 실패: {type(e).__name__}: {e}")
            # mss 재초기화 시도 (다음 호출 시 사용)
            self._init_mss()
            ph_h = int(max_width * self._screen_h / max(self._screen_w, 1))
            return self._create_placeholder(max_width, ph_h, f"캡처 오류: {type(e).__name__}")

    def capture_region(self, x: int, y: int, w: int, h: int,
                       quality: int = 60) -> bytes:
        """특정 영역 캡처"""
        if not self._sct or not PIL_AVAILABLE:
            return self._create_placeholder(w, h, "캡처 불가")

        try:
            region = {"left": x, "top": y, "width": w, "height": h}
            screenshot = self._sct.grab(region)
            img = Image.frombytes('RGB', screenshot.size, screenshot.bgra, 'raw', 'BGRX')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=quality)
            return buf.getvalue()
        except Exception as e:
            logger.error(f"[ScreenCapture] capture_region 실패: {e}")
            return self._create_placeholder(w, h, "캡처 오류")

    def close(self):
        if self._sct:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
