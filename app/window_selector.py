"""
ウィンドウ選択オーバーレイ。
EnumWindows でZ順にウィンドウ一覧を事前取得し、
カーソル位置から最前面のウィンドウを正確に特定する。
"""
import ctypes
import ctypes.wintypes
import logging
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QFont, QCursor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from app.capture import virtual_geometry

logger = logging.getLogger(__name__)

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_dwmapi = ctypes.windll.dwmapi
_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_PW_RENDERFULLCONTENT = 0x00000002  # DirectX/合成描画のウィンドウも PrintWindow で取得する

# 64bit ではハンドルが c_int (32bit) に切り詰められるため、restype/argtypes を明示する
_user32.GetWindowDC.restype = ctypes.wintypes.HDC
_user32.GetWindowDC.argtypes = [ctypes.wintypes.HWND]
_user32.ReleaseDC.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HDC]
_user32.PrintWindow.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HDC, ctypes.wintypes.UINT]
_gdi32.CreateCompatibleDC.restype = ctypes.wintypes.HDC
_gdi32.CreateCompatibleDC.argtypes = [ctypes.wintypes.HDC]
_gdi32.CreateCompatibleBitmap.restype = ctypes.wintypes.HBITMAP
_gdi32.CreateCompatibleBitmap.argtypes = [ctypes.wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.SelectObject.restype = ctypes.wintypes.HGDIOBJ
_gdi32.SelectObject.argtypes = [ctypes.wintypes.HDC, ctypes.wintypes.HGDIOBJ]
_gdi32.DeleteObject.argtypes = [ctypes.wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [ctypes.wintypes.HDC]

# HWND を受け取る user32/dwmapi 関数も同様に明示する（64bit でハンドルが c_int に
# 切り詰められ「たまたま動く」状態を防ぐ）
_user32.PrintWindow.restype = ctypes.wintypes.BOOL
_user32.GetWindowRect.restype = ctypes.wintypes.BOOL
_user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
_user32.IsWindowVisible.restype = ctypes.wintypes.BOOL
_user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
_user32.IsIconic.restype = ctypes.wintypes.BOOL
_user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int]
_user32.EnumWindows.restype = ctypes.wintypes.BOOL
_user32.EnumWindows.argtypes = [_WNDENUMPROC, ctypes.wintypes.LPARAM]
_dwmapi.DwmGetWindowAttribute.restype = ctypes.wintypes.LONG  # HRESULT
_dwmapi.DwmGetWindowAttribute.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.DWORD,
                                          ctypes.c_void_p, ctypes.wintypes.DWORD]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


_gdi32.GetDIBits.argtypes = [ctypes.wintypes.HDC, ctypes.wintypes.HBITMAP,
                             ctypes.wintypes.UINT, ctypes.wintypes.UINT,
                             ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO),
                             ctypes.wintypes.UINT]


def _get_window_rect(hwnd: int) -> ctypes.wintypes.RECT:
    """
    DwmGetWindowAttribute で実際の表示境界を取得する（ウィンドウ影を除いた正確な境界）。
    DWMが無効な場合は GetWindowRect にフォールバック。
    """
    rect = ctypes.wintypes.RECT()
    hr = _dwmapi.DwmGetWindowAttribute(
        hwnd,
        _DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect),
        ctypes.sizeof(rect),
    )
    if hr != 0:
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect


@dataclass
class WindowInfo:
    hwnd: int
    rect: QRect
    title: str


def _is_mostly_black(image: QImage) -> bool:
    """画像がほぼ真っ黒（=PrintWindow が内容を描画できなかった疑い）か判定する。"""
    step_x = max(1, image.width() // 32)
    step_y = max(1, image.height() // 32)
    total = 0
    black = 0
    for y in range(0, image.height(), step_y):
        for x in range(0, image.width(), step_x):
            total += 1
            if image.pixel(x, y) & 0xFFFFFF == 0:
                black += 1
    return total > 0 and black / total >= 0.99


def _grab_window_content(hwnd: int, dwm_rect: QRect) -> QPixmap | None:
    """PrintWindow(PW_RENDERFULLCONTENT) でウィンドウ自体から画像を取得し、
    可視境界（DWM extended frame bounds）で切り出して返す。

    画面合成を経由しないため、Win11 の角丸四隅や境界1pxに背後の画面が
    写り込まず、手前に別ウィンドウが重なっていても対象だけが写る。
    取得できない場合は None（呼び出し元で画面切り抜きにフォールバック）。
    """
    wr = ctypes.wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(wr)):
        return None
    w = wr.right - wr.left
    h = wr.bottom - wr.top
    if w <= 0 or h <= 0:
        return None

    hdc_win = _user32.GetWindowDC(hwnd)
    if not hdc_win:
        return None
    image: QImage | None = None
    hdc_mem = _gdi32.CreateCompatibleDC(hdc_win)
    hbmp = _gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    old_bmp = None
    try:
        if hdc_mem and hbmp:
            old_bmp = _gdi32.SelectObject(hdc_mem, hbmp)
            ok = _user32.PrintWindow(hwnd, hdc_mem, _PW_RENDERFULLCONTENT)
            if ok:
                bmi = _BITMAPINFO()
                bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
                bmi.bmiHeader.biWidth = w
                bmi.bmiHeader.biHeight = -h  # 負値 = top-down 配置
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                bmi.bmiHeader.biCompression = 0  # BI_RGB
                buf = (ctypes.c_char * (w * h * 4))()
                if _gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0) == h:
                    # RGB32 でアルファを無視する（GDI 描画のウィンドウはアルファ0のため
                    # ARGB として扱うと全面透明になる）
                    image = QImage(bytes(buf), w, h, w * 4,
                                   QImage.Format.Format_RGB32).copy()
    finally:
        # ビットマップを DC から外してから削除する（例外時も含め確実に復元する）
        if hdc_mem and old_bmp:
            _gdi32.SelectObject(hdc_mem, old_bmp)
        if hbmp:
            _gdi32.DeleteObject(hbmp)
        if hdc_mem:
            _gdi32.DeleteDC(hdc_mem)
        _user32.ReleaseDC(hwnd, hdc_win)

    if image is None:
        logger.info("PrintWindow に失敗したため画面切り抜きにフォールバックします (hwnd=%s)", hwnd)
        return None
    # 一部の DirectComposition 系ウィンドウは成功を返しつつ真っ黒になる → フォールバック
    # （本当に真っ黒なウィンドウなら画面切り抜きでも同じ絵になるため安全側）
    if _is_mostly_black(image):
        logger.info("PrintWindow の結果がほぼ黒のため画面切り抜きにフォールバックします (hwnd=%s)", hwnd)
        return None

    # GetWindowRect（不可視の掴み枠込み）から可視境界のみを切り出す
    crop = QRect(dwm_rect.x() - wr.left, dwm_rect.y() - wr.top,
                 dwm_rect.width(), dwm_rect.height()).intersected(image.rect())
    if crop.isEmpty():
        return None
    return QPixmap.fromImage(image.copy(crop))


def _enum_visible_windows() -> list[WindowInfo]:
    """
    EnumWindows でトップレベルの可視ウィンドウをZ順（前面→背面）で列挙する。
    タイトルのないウィンドウ・最小化ウィンドウは除外。
    """
    results: list[WindowInfo] = []

    def callback(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        # 最小化は除外
        if _user32.IsIconic(hwnd):
            return True

        rect = _get_window_rect(hwnd)
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return True

        buf = ctypes.create_unicode_buffer(256)
        _user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value
        if not title:
            return True

        results.append(WindowInfo(
            hwnd=hwnd,
            rect=QRect(rect.left, rect.top, w, h),
            title=title,
        ))
        return True

    ret = _user32.EnumWindows(_WNDENUMPROC(callback), 0)
    if not ret and not results:
        logger.warning("EnumWindows が失敗しました (戻り値: %d)", ret)
    return results  # Z順: インデックスが小さいほど前面


def _to_logical(rect: QRect, dpr: float) -> QRect:
    """Win32 の物理ピクセル矩形を Qt の論理座標へ換算する。

    Win32 API (GetWindowRect / DWM 境界) は物理ピクセルを返すが、Qt6 の
    カーソル座標・スクリーン座標・ウィジェット描画は論理ピクセル。両者を
    そのまま比較・描画すると拡大率 100% 以外でずれる。dpr==1.0 では恒等
    （開発機・等倍環境の挙動を一切変えない）。
    """
    if dpr == 1.0:
        return rect
    return QRect(round(rect.x() / dpr), round(rect.y() / dpr),
                 round(rect.width() / dpr), round(rect.height() / dpr))


def _find_window_at(windows: list[WindowInfo], pos: QPoint, dpr: float) -> WindowInfo | None:
    """Z順リストからカーソル位置（論理座標）を含む最前面のウィンドウを返す。"""
    for win in windows:
        if _to_logical(win.rect, dpr).contains(pos):
            return win
    return None


class WindowSelector(QWidget):
    def __init__(self, background: QPixmap, windows: list[WindowInfo], vg: QRect,
                 callback: Callable[[QPixmap], None],
                 cancel_callback: Callable[[], None] | None = None):
        super().__init__()
        self._bg = background
        self._windows = windows
        self._callback = callback
        self._cancel_callback = cancel_callback
        self._finished = False
        self._current: WindowInfo | None = None
        self._vg = vg  # paintEvent キャッシュ（呼び出し元と共有し二重計算を避ける）
        screen = QApplication.primaryScreen()
        self._dpr = screen.devicePixelRatio() if screen is not None else 1.0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMouseTracking(True)

    def showEvent(self, event):
        super().showEvent(event)
        self.activateWindow()
        self.setFocus()
        self.grabKeyboard()
        # 初期カーソル位置でウィンドウを特定
        cursor_pos = QCursor.pos()
        win = _find_window_at(self._windows, cursor_pos, self._dpr)
        if win is not self._current:
            self._current = win
            self.update()

    def closeEvent(self, event):
        self.releaseKeyboard()
        # callback もキャンセルも実行しないまま閉じた場合（Alt+F4 等）はキャンセル扱いにし、
        # メインウィンドウがロックされたままになるのを防ぐ
        if not self._finished:
            self._finished = True
            if self._cancel_callback:
                self._cancel_callback()
        super().closeEvent(event)

    def hideEvent(self, event):
        self.releaseKeyboard()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._bg)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        if self._current:
            # 物理ピクセルのウィンドウ矩形を論理座標へ換算し、ウィジェット座標に変換
            r = _to_logical(self._current.rect, self._dpr).translated(
                -self._vg.x(), -self._vg.y())

            # 選択ウィンドウ領域を明るく再描画
            painter.drawPixmap(r, self._bg, r)

            # 枠線
            pen = QPen(QColor(0, 180, 255), 3)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(r)

            # タイトルラベル
            font = QFont()
            font.setPixelSize(13)
            font.setBold(True)
            painter.setFont(font)
            fm = painter.fontMetrics()
            label = f"  {self._current.title}  "
            text_w = fm.horizontalAdvance(label)
            text_h = fm.height()
            lx = max(r.left(), 0)
            ly = r.top() - text_h - 6
            if ly < 0:
                ly = r.top() + 4
            painter.fillRect(lx, ly, text_w, text_h + 4, QColor(0, 180, 255, 220))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(lx, ly + text_h, label)

        # 下部ヒント
        hint_font = QFont()
        hint_font.setPixelSize(13)
        painter.setFont(hint_font)
        painter.fillRect(0, self.height() - 28, self.width(), 28, QColor(0, 0, 0, 160))
        painter.setPen(QColor(220, 220, 220))
        painter.drawText(10, self.height() - 8,
                         "クリック: キャプチャ  /  Esc: キャンセル")
        painter.end()

    def mouseMoveEvent(self, event):
        # グローバル座標でウィンドウを検索（スクリーン座標と一致）
        global_pos = event.globalPosition().toPoint()
        win = _find_window_at(self._windows, global_pos, self._dpr)
        if win is not self._current:
            self._current = win
            self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        target = self._current
        self._finished = True
        self.close()
        if target:
            # 第一候補: ウィンドウ自体から取得（背景の写り込み・角丸の欠けが起きない）
            pixmap = _grab_window_content(target.hwnd, target.rect)
            if pixmap is None or pixmap.isNull():
                # フォールバック: 従来どおり画面全体のスクリーンショットから切り抜く。
                # 論理座標へ換算した後、QPixmap.copy はデバイスピクセル指定のため
                # 背景 pixmap の devicePixelRatio でスケールし直す
                local_rect = _to_logical(target.rect, self._dpr).translated(
                    -self._vg.x(), -self._vg.y())
                bg_dpr = self._bg.devicePixelRatio()
                if bg_dpr != 1.0:
                    local_rect = QRect(round(local_rect.x() * bg_dpr),
                                       round(local_rect.y() * bg_dpr),
                                       round(local_rect.width() * bg_dpr),
                                       round(local_rect.height() * bg_dpr))
                pixmap = self._bg.copy(local_rect)
            self._callback(pixmap)
        elif self._cancel_callback:
            self._cancel_callback()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._finished = True
            self.close()
            if self._cancel_callback:
                self._cancel_callback()


def start_window_capture(callback: Callable[[QPixmap], None],
                         cancel_callback: Callable[[], None] | None = None) -> "WindowSelector | None":
    """ウィンドウ選択オーバーレイを起動する。選択後 callback に QPixmap を渡す。"""
    windows = _enum_visible_windows()
    vg = virtual_geometry()
    screen = QApplication.primaryScreen()
    if screen is None:
        if cancel_callback:
            cancel_callback()
        return None
    bg = screen.grabWindow(0, vg.x(), vg.y(), vg.width(), vg.height())
    selector = WindowSelector(bg, windows, vg, callback, cancel_callback)
    selector.setGeometry(vg)
    selector.show()
    return selector
