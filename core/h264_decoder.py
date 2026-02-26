"""H.264 디코더 — PyAV 기반 (매니저 측)

에이전트가 보낸 H.264 NAL 패킷을 디코딩하여 QImage로 변환.
에러 복구: 프레임 시퀀스 갭 감지 → 키프레임 대기 → 정상 복귀.

사용:
    decoder = H264Decoder()
    qimage = decoder.decode_frame(header_byte, raw_data)
    if qimage:
        widget.update_frame_qimage(qimage)
    decoder.close()
"""

import logging
import struct
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import av
    AV_AVAILABLE = True
except ImportError:
    AV_AVAILABLE = False
    logger.warning("PyAV(av) 미설치 — H.264 디코딩 불가")

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    from PyQt6.QtGui import QImage
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False


# 바이너리 헤더
HEADER_H264_KEYFRAME = 0x03
HEADER_H264_DELTA = 0x04


class H264Decoder:
    """H.264 디코더 (매니저 측, PyAV 기반)

    와이어 포맷:
      [1B header (0x03|0x04)] + [4B frame_seq (big-endian)] + [NAL unit(s)]

    에러 복구:
      1. frame_seq 갭 감지 → _waiting_for_keyframe = True
      2. P-frame 도착 시 스킵 (키프레임 대기)
      3. 키프레임 수신 → 디코더 리셋 → 정상 디코딩 복귀
    """

    def __init__(self):
        self._codec_ctx: Optional[av.CodecContext] = None
        self._last_seq: int = -1
        self._waiting_for_keyframe: bool = True  # 처음에는 키프레임 필요
        self._decode_errors: int = 0
        self._frames_decoded: int = 0
        self._init_decoder()

    @property
    def is_available(self) -> bool:
        """H.264 디코딩 가능 여부"""
        return AV_AVAILABLE and NUMPY_AVAILABLE and PYQT_AVAILABLE

    @property
    def waiting_for_keyframe(self) -> bool:
        return self._waiting_for_keyframe

    @property
    def frames_decoded(self) -> int:
        return self._frames_decoded

    def _init_decoder(self):
        """디코더 초기화"""
        if not AV_AVAILABLE:
            return

        try:
            codec = av.codec.Codec('h264', 'r')
            self._codec_ctx = av.CodecContext.create(codec, 'r')
            self._codec_ctx.open()
            logger.info("[H264Decoder] 디코더 초기화 성공")
        except Exception as e:
            logger.error(f"[H264Decoder] 디코더 초기화 실패: {e}")
            self._codec_ctx = None

    def reset(self):
        """디코더 리셋 (에러 복구용)"""
        if self._codec_ctx:
            try:
                self._codec_ctx.close()
            except Exception:
                pass
        self._codec_ctx = None
        self._last_seq = -1
        self._waiting_for_keyframe = True
        self._init_decoder()
        logger.info("[H264Decoder] 디코더 리셋")

    def decode_frame(self, header: int, data: bytes) -> Optional['QImage']:
        """H.264 프레임 디코딩

        Args:
            header: 0x03 (키프레임) 또는 0x04 (델타프레임)
            data: [4B frame_seq] + [NAL unit(s)]

        Returns:
            QImage (RGB888) 또는 None (디코딩 실패/스킵)
        """
        if not self._codec_ctx or not PYQT_AVAILABLE or not NUMPY_AVAILABLE:
            return None

        if len(data) < 5:
            return None

        # 시퀀스 번호 추출
        frame_seq = struct.unpack('>I', data[:4])[0]
        nal_data = data[4:]

        is_keyframe = (header == HEADER_H264_KEYFRAME)

        # 시퀀스 갭 감지
        if self._last_seq >= 0:
            expected = (self._last_seq + 1) & 0xFFFFFFFF
            if frame_seq != expected:
                gap = frame_seq - self._last_seq
                logger.warning(
                    f"[H264Decoder] 프레임 갭 감지: "
                    f"기대={expected}, 수신={frame_seq} (갭={gap})"
                )
                if not is_keyframe:
                    self._waiting_for_keyframe = True

        # 키프레임 대기 중이면 P-frame 스킵
        if self._waiting_for_keyframe:
            if is_keyframe:
                logger.info(f"[H264Decoder] 키프레임 수신 (seq={frame_seq}) — 디코딩 복귀")
                self._waiting_for_keyframe = False
                self._decode_errors = 0
                # 디코더 리셋 후 키프레임부터 새로 시작
                self._reset_decoder_context()
            else:
                # P-frame 스킵
                self._last_seq = frame_seq
                return None

        self._last_seq = frame_seq

        # NAL → av.Packet → decode → VideoFrame → numpy → QImage
        try:
            packet = av.Packet(nal_data)
            packet.pts = frame_seq
            packet.dts = frame_seq

            frames = self._codec_ctx.decode(packet)

            for frame in frames:
                # VideoFrame → numpy (RGB)
                rgb_frame = frame.to_ndarray(format='rgb24')
                h, w, ch = rgb_frame.shape
                bytes_per_line = ch * w

                # numpy → QImage
                qimage = QImage(
                    rgb_frame.data, w, h, bytes_per_line,
                    QImage.Format.Format_RGB888
                ).copy()  # .copy()로 numpy 메모리에서 분리

                self._frames_decoded += 1
                self._decode_errors = 0
                return qimage

            return None  # 디코더가 아직 출력 안 함 (버퍼링 중)

        except av.error.InvalidDataError as e:
            self._decode_errors += 1
            logger.warning(
                f"[H264Decoder] 잘못된 데이터 (seq={frame_seq}): {e} "
                f"(연속 에러: {self._decode_errors})"
            )
            if self._decode_errors >= 3:
                logger.warning("[H264Decoder] 연속 에러 3회 — 디코더 리셋")
                self.reset()
            return None

        except Exception as e:
            self._decode_errors += 1
            logger.error(
                f"[H264Decoder] 디코딩 오류 (seq={frame_seq}): "
                f"{type(e).__name__}: {e}"
            )
            if self._decode_errors >= 5:
                logger.warning("[H264Decoder] 연속 에러 5회 — 디코더 리셋")
                self.reset()
            return None

    def _reset_decoder_context(self):
        """디코더 컨텍스트만 리셋 (시퀀스 유지)"""
        if not AV_AVAILABLE:
            return

        try:
            if self._codec_ctx:
                self._codec_ctx.close()
        except Exception:
            pass

        try:
            codec = av.codec.Codec('h264', 'r')
            self._codec_ctx = av.CodecContext.create(codec, 'r')
            self._codec_ctx.open()
        except Exception as e:
            logger.error(f"[H264Decoder] 디코더 컨텍스트 리셋 실패: {e}")
            self._codec_ctx = None

    def close(self):
        """디코더 리소스 해제"""
        if self._codec_ctx:
            try:
                # 버퍼 플러시
                self._codec_ctx.decode(None)
            except Exception:
                pass
            try:
                self._codec_ctx.close()
            except Exception:
                pass
            self._codec_ctx = None
            logger.info(f"[H264Decoder] 디코더 종료 (총 {self._frames_decoded}프레임)")

    def __del__(self):
        self.close()
