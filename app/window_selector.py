"""
ウィンドウ選択オーバーレイ。
EnumWindows でZ順にウィンドウ一覧を事前取得し、
カーソル位置から最前面のウィンドウを正確に特定する。
"""
import ctypes
import ctypes.wintypes
from dataclasses import dataclass

from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QFont, QCursor
from PySide6.QtWidgets import QApplication, QWidget

from app.capture import _virtual_geometry

_user32 = ctypes.windll.user32
_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)


@dataclass
class WindowInfo:
    hwnd: int
    rect: QRect
    title: str


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

        rect = ctypes.wintypes.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
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

    _user32.EnumWindows(_WNDENUMPROC(callback), 0)
    return results  # Z順: インデックスが小さいほど前面


def _find_window_at(windows: list[WindowInfo], pos: QPoint) -> WindowInfo | None:
    """Z順リストからカーソル位置を含む最前面のウィンドウを返す。"""
    for win in windows:
        if win.rect.contains(pos):
            return win
    return None


class WindowSelector(QWidget):
    def __init__(self, background: QPixmap, windows: list[WindowInfo], callback,
                 cancel_callback=None):
        super().__init__()
        self._bg = background
        self._windows = windows
        self._callback = callback
        self._cancel_callback = cancel_callback
        self._current: WindowInfo | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setMouseTracking(True)

    def showEvent(self, event):
        super().showEvent(event)
        # 初期カーソル位置でウィンドウを特定
        cursor_pos = QCursor.pos()
        win = _find_window_at(self._windows, cursor_pos)
        if win is not self._current:
            self._current = win
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._bg)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        if self._current:
            # 仮想デスクトップ内でのウィンドウ矩形をウィジェット座標に変換
            vg = _virtual_geometry()
            r = self._current.rect.translated(-vg.x(), -vg.y())

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

    def mouseMoveEvent(self, event):
        # グローバル座標でウィンドウを検索（スクリーン座標と一致）
        global_pos = event.globalPosition().toPoint()
        win = _find_window_at(self._windows, global_pos)
        if win is not self._current:
            self._current = win
            self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        target = self._current
        self.close()
        if target:
            vg = _virtual_geometry()
            # ウィジェット座標で bg をコピー
            local_rect = target.rect.translated(-vg.x(), -vg.y())
            pixmap = self._bg.copy(local_rect)
            self._callback(pixmap)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            if self._cancel_callback:
                self._cancel_callback()


def start_window_capture(callback, cancel_callback=None):
    """ウィンドウ選択オーバーレイを起動する。選択後 callback に QPixmap を渡す。"""
    windows = _enum_visible_windows()
    vg = _virtual_geometry()
    bg = QApplication.primaryScreen().grabWindow(0, vg.x(), vg.y(), vg.width(), vg.height())
    selector = WindowSelector(bg, windows, callback, cancel_callback)
    selector.setGeometry(vg)
    selector.show()
    return selector
