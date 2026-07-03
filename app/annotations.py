from dataclasses import dataclass
from typing import TypeAlias
from PySide6.QtCore import QRect, QPoint
from PySide6.QtGui import QColor


@dataclass
class RectAnnotation:
    rect: QRect
    color: QColor
    line_width: float

    def copy(self) -> "RectAnnotation":
        return RectAnnotation(
            rect=QRect(self.rect),
            color=QColor(self.color),
            line_width=self.line_width,
        )


@dataclass
class FilledRectAnnotation:
    rect: QRect
    color: QColor

    def copy(self) -> "FilledRectAnnotation":
        return FilledRectAnnotation(
            rect=QRect(self.rect),
            color=QColor(self.color),
        )


@dataclass
class TextAnnotation:
    pos: QPoint
    text: str
    color: QColor
    font_size: int

    def copy(self) -> "TextAnnotation":
        return TextAnnotation(
            pos=QPoint(self.pos),
            text=self.text,
            color=QColor(self.color),
            font_size=self.font_size,
        )


Annotation: TypeAlias = RectAnnotation | FilledRectAnnotation | TextAnnotation
