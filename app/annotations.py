from dataclasses import dataclass
from PySide6.QtCore import QRect, QPoint
from PySide6.QtGui import QColor


@dataclass
class RectAnnotation:
    rect: QRect
    color: QColor
    line_width: int


@dataclass
class FilledRectAnnotation:
    rect: QRect
    color: QColor


@dataclass
class TextAnnotation:
    pos: QPoint
    text: str
    color: QColor
    font_size: int


Annotation = RectAnnotation | FilledRectAnnotation | TextAnnotation
