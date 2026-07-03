"""共有UIユーティリティ。"""
import sys
from pathlib import Path

# QIcon が SVG を描画できるよう SVG アイコンエンジンを確実にロードする
# （PyInstaller にも QtSvg プラグインを同梱させる）
from PySide6 import QtSvg  # noqa: F401
from PySide6.QtGui import QColor, QIcon, QPixmap


def color_icon(color: QColor, size: int = 16) -> QIcon:
    """指定色の正方形アイコンを生成する。"""
    if size <= 0:
        size = 16
    pm = QPixmap(size, size)
    pm.fill(color)
    return QIcon(pm)


def resource_path(relative: str) -> Path:
    """リソースファイルの絶対パスを返す。PyInstaller の onefile 展開先にも対応する。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative


def load_icon(name: str) -> QIcon:
    """icons/ ディレクトリの SVG アイコンを読み込む。見つからなければ空アイコンを返す。"""
    path = resource_path(f"icons/{name}.svg")
    if not path.exists():
        return QIcon()
    return QIcon(str(path))
