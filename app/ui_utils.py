"""共有UIユーティリティ。"""
from PySide6.QtGui import QColor, QIcon, QPixmap


def color_icon(color: QColor, size: int = 16) -> QIcon:
    """指定色の正方形アイコンを生成する。"""
    if size <= 0:
        size = 16
    pm = QPixmap(size, size)
    pm.fill(color)
    return QIcon(pm)
