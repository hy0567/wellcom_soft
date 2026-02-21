"""Win32 클립보드 감시 + 읽기/쓰기 (한글 완벽 지원)"""

import ctypes
import ctypes.wintypes
import threading
import io
import logging
from typing import Optional, Callable, Tuple

logger = logging.getLogger(__name__)

# Win32 상수
CF_UNICODETEXT = 13
CF_DIB = 8
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
WM_CLIPBOARDUPDATE = 0x031D

# WNDPROC 콜백 타입
WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.wintypes.HWND,
    ctypes.c_uint,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)


class WNDCLASSW(ctypes.Structure):
    """Python 3.14+ 호환 WNDCLASSW 구조체 (ctypes.wintypes에서 제거됨)"""
    _fields_ = [
        ('style', ctypes.c_uint),
        ('lpfnWndProc', WNDPROC),
        ('cbClsExtra', ctypes.c_int),
        ('cbWndExtra', ctypes.c_int),
        ('hInstance', ctypes.wintypes.HINSTANCE),
        ('hIcon', ctypes.wintypes.HICON),
        ('hCursor', ctypes.wintypes.HANDLE),
        ('hbrBackground', ctypes.wintypes.HANDLE),
        ('lpszMenuName', ctypes.wintypes.LPCWSTR),
        ('lpszClassName', ctypes.wintypes.LPCWSTR),
    ]


class ClipboardMonitor:
    """Win32 API로 클립보드 변경 감지 + 읽기/쓰기"""

    def __init__(self):
        self._monitoring = False
        self._thread: Optional[threading.Thread] = None
        self._callback: Optional[Callable] = None
        self._hwnd = None
        self._ignore_next = False

    def start_monitoring(self, callback: Callable):
        """클립보드 변경 시 callback(format_type, data) 호출"""
        if self._monitoring:
            return

        self._callback = callback
        self._monitoring = True
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name='ClipboardMonitor'
        )
        self._thread.start()
        logger.info("클립보드 감시 시작")

    def stop_monitoring(self):
        self._monitoring = False
        if self._hwnd:
            try:
                user32 = ctypes.windll.user32
                user32.RemoveClipboardFormatListener(self._hwnd)
                user32.DestroyWindow(self._hwnd)
            except Exception:
                pass
        self._hwnd = None
        logger.info("클립보드 감시 중지")

    def _monitor_loop(self):
        """메시지 루프 스레드"""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # DefWindowProcW 인자 타입 명시 (Python 3.14 int overflow 방지)
        user32.DefWindowProcW.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_uint,
            ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = ctypes.c_long

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_CLIPBOARDUPDATE:
                if not self._ignore_next:
                    self._on_clipboard_changed()
                else:
                    self._ignore_next = False
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_ref = WNDPROC(wnd_proc)

        class_name = 'WellcomAgentClipboard'
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wnd_proc_ref
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = class_name

        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            logger.error("RegisterClassW 실패")
            return

        HWND_MESSAGE = ctypes.wintypes.HWND(-3)
        self._hwnd = user32.CreateWindowExW(
            0, class_name, 'WellcomAgent Clipboard', 0,
            0, 0, 0, 0, HWND_MESSAGE, None, wc.hInstance, None
        )

        if not self._hwnd:
            logger.error("CreateWindowExW 실패")
            return

        if not user32.AddClipboardFormatListener(self._hwnd):
            logger.error("AddClipboardFormatListener 실패")
            return

        logger.info("클립보드 리스너 등록 완료")

        msg = ctypes.wintypes.MSG()
        while self._monitoring:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _on_clipboard_changed(self):
        try:
            fmt, data = self.get_clipboard()
            if fmt and data and self._callback:
                self._callback(fmt, data)
        except Exception as e:
            logger.debug(f"클립보드 읽기 실패: {e}")

    def get_clipboard(self) -> Tuple[Optional[str], Optional[object]]:
        """현재 클립보드 내용 반환"""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        if not user32.OpenClipboard(None):
            return None, None

        try:
            if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if handle:
                    ptr = kernel32.GlobalLock(handle)
                    if ptr:
                        try:
                            text = ctypes.wstring_at(ptr)
                            if text:
                                return 'text', text
                        finally:
                            kernel32.GlobalUnlock(handle)

            if user32.IsClipboardFormatAvailable(CF_DIB):
                handle = user32.GetClipboardData(CF_DIB)
                if handle:
                    size = kernel32.GlobalSize(handle)
                    ptr = kernel32.GlobalLock(handle)
                    if ptr and size:
                        try:
                            raw = ctypes.string_at(ptr, size)
                            png_data = self._dib_to_png(raw)
                            if png_data:
                                return 'image', png_data
                        finally:
                            kernel32.GlobalUnlock(handle)

            return None, None
        finally:
            user32.CloseClipboard()

    def set_clipboard_text(self, text: str):
        """클립보드에 텍스트 설정"""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        self._ignore_next = True

        if not user32.OpenClipboard(None):
            self._ignore_next = False
            return False

        try:
            user32.EmptyClipboard()
            encoded = text.encode('utf-16-le') + b'\x00\x00'
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
            if not h:
                return False
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                kernel32.GlobalFree(h)
                return False
            ctypes.memmove(ptr, encoded, len(encoded))
            kernel32.GlobalUnlock(h)
            user32.SetClipboardData(CF_UNICODETEXT, h)
            return True
        except Exception as e:
            logger.error(f"클립보드 텍스트 설정 실패: {e}")
            self._ignore_next = False
            return False
        finally:
            user32.CloseClipboard()

    def set_clipboard_image(self, png_data: bytes):
        """클립보드에 이미지 설정 (PNG → DIB)"""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        dib_data = self._png_to_dib(png_data)
        if not dib_data:
            return False

        self._ignore_next = True

        if not user32.OpenClipboard(None):
            self._ignore_next = False
            return False

        try:
            user32.EmptyClipboard()
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib_data))
            if not h:
                return False
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                kernel32.GlobalFree(h)
                return False
            ctypes.memmove(ptr, dib_data, len(dib_data))
            kernel32.GlobalUnlock(h)
            user32.SetClipboardData(CF_DIB, h)
            return True
        except Exception as e:
            logger.error(f"클립보드 이미지 설정 실패: {e}")
            self._ignore_next = False
            return False
        finally:
            user32.CloseClipboard()

    @staticmethod
    def _dib_to_png(dib_data: bytes) -> Optional[bytes]:
        try:
            from PIL import Image
            import struct

            if len(dib_data) < 40:
                return None

            (header_size, width, height, planes, bpp,
             compression, img_size, xppm, yppm,
             colors_used, colors_important) = struct.unpack_from(
                '<IiiHHIIiiII', dib_data, 0
            )

            top_down = height < 0
            height = abs(height)

            if bpp == 32:
                mode = 'BGRA'
            elif bpp == 24:
                mode = 'BGR'
            else:
                return None

            pixel_offset = header_size
            if colors_used > 0:
                pixel_offset += colors_used * 4

            row_size = ((width * (bpp // 8) + 3) & ~3)
            pixel_data = dib_data[pixel_offset:]

            img = Image.frombytes(
                'RGBA' if bpp == 32 else 'RGB',
                (width, height),
                pixel_data,
                'raw',
                mode,
                row_size,
                -1 if not top_down else 1,
            )

            buf = io.BytesIO()
            img.save(buf, format='PNG')
            return buf.getvalue()
        except Exception as e:
            logger.debug(f"DIB→PNG 변환 실패: {e}")
            return None

    @staticmethod
    def _png_to_dib(png_data: bytes) -> Optional[bytes]:
        try:
            from PIL import Image
            import struct

            img = Image.open(io.BytesIO(png_data))
            img = img.convert('BGRA' if img.mode == 'RGBA' else 'BGR')

            width, height = img.size
            bpp = 32 if img.mode == 'BGRA' else 24
            row_size = ((width * (bpp // 8) + 3) & ~3)
            img_size = row_size * height

            header = struct.pack(
                '<IiiHHIIiiII',
                40, width, height, 1, bpp,
                0, img_size, 0, 0, 0, 0
            )

            raw = img.tobytes('raw', img.mode, row_size, -1)
            return header + raw
        except Exception as e:
            logger.debug(f"PNG→DIB 변환 실패: {e}")
            return None
