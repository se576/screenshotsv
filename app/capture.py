from PySide6.QtCore import Qt, QRect, QPoint
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QCursor, QFont
from PySide6.QtWidgets import QApplication, QWidget


def _virtual_geometry() -> QRect:
    """全モニターを包含する仮想デスクトップの矩形を返す。"""
    screens = QApplication.screens()
    if not screens:
        return QApplication.primaryScreen().geometry()
    rect = screens[0].geometry()
    for s in screens[1:]:
        rect = rect.united(s.geometry())
    return rect


def capture_fullscreen() -> QPixmap:
    """全画面（全モニター）をキャプチャして返す。"""
    vg = _virtual_geometry()
    screen = QApplication.primaryScreen()
    return screen.grabWindow(0, vg.x(), vg.y(), vg.width(), vg.height())


class RegionSelector(QWidget):
    """
    スクリーンショットをオーバーレイとして表示し、範囲選択するウィジェット。
    選択中はドラッグ範囲のサイズをリアルタイム表示する。
    """

    def __init__(self, background: QPixmap, callback, cancel_callback=None):
        super().__init__()
        self._bg = background
        self._callback = callback
        self._cancel_callback = cancel_callback
        self._start: QPoint | None = None
        self._end: QPoint | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # 背景を自前で描くので TranslucentBackground は不要
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setMouseTracking(True)

    def paintEvent(self, event):
        painter = QPainter(self)

        # 1. 元のスクリーンショットを描画
        painter.drawPixmap(0, 0, self._bg)

        # 2. 暗いオーバーレイ（全体）
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))

        if self._start and self._end:
            rect = QRect(self._start, self._end).normalized()

            # 3. 選択範囲だけ元の画像を明るく再描画
            painter.drawPixmap(rect, self._bg, rect)

            # 4. 選択枠
            pen = QPen(QColor(0, 160, 255), 2)
            painter.setPen(pen)
            painter.drawRect(rect)

            # 5. サイズラベル
            size_text = f"{rect.width()} x {rect.height()}"
            font = QFont()
            font.setPixelSize(14)
            font.setBold(True)
            painter.setFont(font)

            label_x = rect.x()
            label_y = rect.y() - 20 if rect.y() > 24 else rect.bottom() + 4

            # 背景
            fm = painter.fontMetrics()
            text_rect = fm.boundingRect(size_text)
            bg_rect = text_rect.adjusted(-4, -2, 4, 2)
            bg_rect.moveTo(label_x, label_y)
            painter.fillRect(bg_rect, QColor(0, 160, 255, 200))

            painter.setPen(QColor(255, 255, 255))
            painter.drawText(label_x, label_y + text_rect.height(), size_text)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.position().toPoint()
            self._end = self._start

    def mouseMoveEvent(self, event):
        if self._start:
            self._end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._start:
            self._end = event.position().toPoint()
            rect = QRect(self._start, self._end).normalized()
            self.close()
            if rect.width() > 4 and rect.height() > 4:
                pixmap = self._bg.copy(rect)
                self._callback(pixmap)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            if self._cancel_callback:
                self._cancel_callback()


def start_region_capture(callback, cancel_callback=None):
    """
    全画面をキャプチャしてオーバーレイとして表示し、範囲選択を開始する。
    選択完了後、callbackにQPixmapを渡す。Escキャンセル時はcancel_callbackを呼ぶ。
    """
    vg = _virtual_geometry()
    bg = QApplication.primaryScreen().grabWindow(0, vg.x(), vg.y(), vg.width(), vg.height())
    selector = RegionSelector(bg, callback, cancel_callback)
    selector.setGeometry(vg)
    selector.show()
    return selector
