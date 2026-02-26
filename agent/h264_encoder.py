"""H.264 인코더 — PyAV 기반 하드웨어 가속 지원

하드웨어 인코더 우선순위: h264_nvenc → h264_qsv → h264_amf → libx264
MJPEG 대비 ~1/10 대역폭으로 동일 화질 스트리밍 가능.

사용:
    encoder = H264Encoder(1920, 1080, fps=15, quality=60)
    packets = encoder.encode_frame(pil_image)  # → list[(is_keyframe, nal_bytes)]
    encoder.force_keyframe()
    encoder.close()
"""

import logging
import struct
from typing import List, Tuple, Optional

logger = logging.getLogger('WellcomAgent.H264Encoder')

try:
    import av
    AV_AVAILABLE = True
except ImportError:
    AV_AVAILABLE = False
    logger.warning("PyAV(av) 미설치 — H.264 인코딩 불가, MJPEG 폴백")

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

# 하드웨어 인코더 우선순위
_HW_ENCODERS = ['h264_nvenc', 'h264_qsv', 'h264_amf']
_SW_ENCODER = 'libx264'


def _quality_to_crf(quality: int) -> int:
    """quality (1-100) → CRF (0-51) 변환

    quality=100 → CRF=10 (최고 화질)
    quality=60  → CRF=26 (중간)
    quality=10  → CRF=47 (최저 화질)
    """
    quality = max(1, min(100, quality))
    crf = int(51 - (quality / 100.0) * 41)
    return max(0, min(51, crf))


class H264Encoder:
    """H.264 인코더 (PyAV 기반, 하드웨어 가속 폴백)

    Args:
        width: 입력 프레임 너비
        height: 입력 프레임 높이
        fps: 목표 프레임레이트
        quality: 화질 (1-100, MJPEG quality와 동일 스케일)
        gop_size: GOP 크기 (키프레임 간격, 기본 60)
    """

    def __init__(self, width: int, height: int, fps: int = 15,
                 quality: int = 60, gop_size: int = 60):
        if not AV_AVAILABLE:
            raise RuntimeError("PyAV(av) 미설치 — pip install av")

        self._width = width
        self._height = height
        self._fps = fps
        self._quality = quality
        self._gop_size = gop_size
        self._frame_seq = 0
        self._force_keyframe = False
        self._codec_ctx: Optional[av.CodecContext] = None
        self._encoder_name: str = ''

        self._init_encoder()

    @property
    def encoder_name(self) -> str:
        """현재 사용 중인 인코더 이름"""
        return self._encoder_name

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def _init_encoder(self):
        """인코더 초기화 — 하드웨어 가속 시도 후 소프트웨어 폴백"""
        # 하드웨어 인코더 먼저 시도
        for enc_name in _HW_ENCODERS:
            try:
                self._codec_ctx = self._create_encoder(enc_name)
                self._encoder_name = enc_name
                logger.info(f"[H264Encoder] 하드웨어 인코더 초기화 성공: {enc_name}")
                return
            except Exception as e:
                logger.debug(f"[H264Encoder] {enc_name} 사용 불가: {e}")
                continue

        # 소프트웨어 인코더 (libx264)
        try:
            self._codec_ctx = self._create_encoder(_SW_ENCODER)
            self._encoder_name = _SW_ENCODER
            logger.info(f"[H264Encoder] 소프트웨어 인코더 초기화: {_SW_ENCODER}")
        except Exception as e:
            logger.error(f"[H264Encoder] 모든 인코더 초기화 실패: {e}")
            raise RuntimeError(f"H.264 인코더를 사용할 수 없습니다: {e}")

    def _create_encoder(self, encoder_name: str) -> av.CodecContext:
        """특정 인코더로 CodecContext 생성"""
        codec = av.codec.Codec(encoder_name, 'w')
        ctx = av.CodecContext.create(codec, 'w')

        ctx.width = self._width
        ctx.height = self._height
        ctx.pix_fmt = 'yuv420p'
        ctx.time_base = av.Fraction(1, self._fps)
        ctx.framerate = av.Fraction(self._fps, 1)
        ctx.gop_size = self._gop_size
        ctx.max_b_frames = 0  # 저지연

        crf = _quality_to_crf(self._quality)

        if encoder_name == _SW_ENCODER:
            # libx264 옵션
            ctx.options = {
                'preset': 'ultrafast',
                'tune': 'zerolatency',
                'crf': str(crf),
            }
        elif 'nvenc' in encoder_name:
            # NVENC 옵션
            ctx.options = {
                'preset': 'p1',           # 최저 지연
                'tune': 'ull',            # ultra low latency
                'rc': 'constqp',
                'qp': str(crf),
                'zerolatency': '1',
            }
        elif 'qsv' in encoder_name:
            # QSV 옵션
            ctx.options = {
                'preset': 'veryfast',
                'global_quality': str(crf),
            }
        elif 'amf' in encoder_name:
            # AMF 옵션
            ctx.options = {
                'usage': 'ultralowlatency',
                'quality': 'speed',
                'rc': 'cqp',
                'qp_i': str(crf),
                'qp_p': str(crf),
            }

        ctx.open()
        return ctx

    def encode_frame(self, pil_image) -> List[Tuple[bool, bytes]]:
        """PIL Image → H.264 NAL 패킷 인코딩

        Args:
            pil_image: PIL.Image (RGB 모드)

        Returns:
            list of (is_keyframe, nal_bytes) — 보통 1개, 키프레임 시 SPS/PPS 포함
        """
        if not self._codec_ctx:
            return []

        try:
            # PIL Image → numpy → av.VideoFrame
            if not NUMPY_AVAILABLE:
                logger.error("[H264Encoder] numpy 미설치 — 인코딩 불가")
                return []

            # RGB PIL Image → numpy array
            rgb_array = np.array(pil_image)

            # numpy → VideoFrame (RGB → YUV 자동 변환)
            frame = av.VideoFrame.from_ndarray(rgb_array, format='rgb24')
            frame.pts = self._frame_seq
            frame.time_base = self._codec_ctx.time_base

            # 강제 키프레임 요청
            if self._force_keyframe:
                frame.pict_type = av.video.frame.PictureType.I
                self._force_keyframe = False

            # 인코딩
            packets = self._codec_ctx.encode(frame)

            result = []
            for packet in packets:
                is_key = bool(packet.is_keyframe)
                nal_bytes = bytes(packet)

                # 와이어 포맷: [4B frame_seq (big-endian)] + NAL
                seq_bytes = struct.pack('>I', self._frame_seq & 0xFFFFFFFF)
                result.append((is_key, seq_bytes + nal_bytes))

            self._frame_seq += 1
            return result

        except Exception as e:
            logger.error(f"[H264Encoder] 인코딩 오류: {type(e).__name__}: {e}")
            return []

    def force_keyframe(self):
        """다음 프레임을 키프레임으로 강제"""
        self._force_keyframe = True
        logger.debug("[H264Encoder] 키프레임 강제 요청")

    def update_quality(self, quality: int):
        """인코딩 화질 변경 (인코더 재초기화)

        참고: 실시간 변경은 인코더 재초기화가 필요하므로
        빈번한 호출은 피해야 함.
        """
        quality = max(1, min(100, quality))
        if quality == self._quality:
            return

        self._quality = quality
        logger.info(f"[H264Encoder] 화질 변경: {quality}")

        # 인코더 재초기화 (실패 시 기존 컨텍스트 유지)
        try:
            new_ctx = self._create_encoder(self._encoder_name)
            old_ctx = self._codec_ctx
            self._codec_ctx = new_ctx
            if old_ctx:
                old_ctx.close()
        except Exception as e:
            logger.error(f"[H264Encoder] 화질 변경 실패 (기존 설정 유지): {e}")

    def update_fps(self, fps: int):
        """FPS 변경 (인코더 재초기화)"""
        fps = max(1, min(60, fps))
        if fps == self._fps:
            return

        self._fps = fps
        logger.info(f"[H264Encoder] FPS 변경: {fps}")

        try:
            new_ctx = self._create_encoder(self._encoder_name)
            old_ctx = self._codec_ctx
            self._codec_ctx = new_ctx
            if old_ctx:
                old_ctx.close()
        except Exception as e:
            logger.error(f"[H264Encoder] FPS 변경 실패 (기존 설정 유지): {e}")

    def close(self):
        """인코더 리소스 해제"""
        if self._codec_ctx:
            try:
                # 버퍼 플러시
                self._codec_ctx.encode(None)
            except Exception:
                pass
            try:
                self._codec_ctx.close()
            except Exception:
                pass
            self._codec_ctx = None
            logger.info(f"[H264Encoder] 인코더 종료: {self._encoder_name}")

    def __del__(self):
        self.close()
