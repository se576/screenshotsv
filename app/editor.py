import copy

from PySide6.QtCore import Qt, QRect, QPoint, QSize
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QFont, QCursor
from PySide6.QtWidgets import QWidget, QSizePolicy, QInputDialog

from app.annotations import RectAnnotation, FilledRectAnnotation, TextAnnotation, Annotation

HANDLE_SIZE = 10  # リサイズハンドルのサイズ（canvas px）
HANDLE_HALF = HANDLE_SIZE // 2
_HANDLES = ("tl", "tr", "bl", "br")  # top-left, top-right, bottom-left, bottom-right


class EditorCanvas(QWidget):
    """
    スクリーンショット表示 + アノテーション描画キャンバス。
    ツール: "select" | "rect" | "filled_rect" | "text" | None
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._annotations: list[Annotation] = []
        self._undo_stack: list[list[Annotation]] = []

        # ツール設定
        self._active_tool: str | None = None
        self._color: QColor = QColor(255, 0, 0)
        self._line_width: int = 2
        self._font_size: int = 16

        # 描画ドラッグ用（rect / filled_rect）
        self._drag_start: QPoint | None = None
        self._drag_end: QPoint | None = None

        # 選択ツール用
        self._selected_idx: int | None = None
        self._drag_mode: str | None = None        # "move" | "resize_tl/tr/bl/br"
        self._drag_start_pos: QPoint | None = None
        self._drag_orig_ann: Annotation | None = None
        self._moved: bool = False  # ドラッグで実際に動いたか（Undo判定用）

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._annotations.clear()
        self._undo_stack.clear()
        self._drag_start = None
        self._drag_end = None
        self._selected_idx = None
        self._drag_mode = None
        self.update()

    def get_pixmap(self) -> QPixmap | None:
        """アノテーションを焼き込んだ画像を返す。"""
        if self._pixmap is None:
            return None
        result = self._pixmap.copy()
        painter = QPainter(result)
        self._draw_annotations(painter, self._annotations)
        painter.end()
        return result

    def set_tool(self, tool: str | None) -> None:
        self._active_tool = tool
        self._selected_idx = None
        self._drag_mode = None
        self._drag_start = None
        self._drag_end = None
        cursor = Qt.CursorShape.ArrowCursor if tool in (None, "select") else Qt.CursorShape.CrossCursor
        self.setCursor(QCursor(cursor))
        self.update()

    def set_color(self, color: QColor) -> None:
        self._color = color

    def set_line_width(self, width: int) -> None:
        self._line_width = width

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        self._annotations = self._undo_stack.pop()
        self._selected_idx = None
        self.update()
        return True

    def has_pixmap(self) -> bool:
        return self._pixmap is not None

    def has_selection(self) -> bool:
        return self._selected_idx is not None and self._selected_idx < len(self._annotations)

    def get_selected_color(self) -> QColor | None:
        if not self.has_selection():
            return None
        ann = self._annotations[self._selected_idx]
        return QColor(ann.color)

    def set_selected_color(self, color: QColor) -> None:
        if not self.has_selection():
            return
        self._push_undo()
        self._annotations[self._selected_idx].color = QColor(color)
        self.update()

    def delete_selected(self) -> None:
        if not self.has_selection():
            return
        self._push_undo()
        del self._annotations[self._selected_idx]
        self._selected_idx = None
        self.update()

    # ------------------------------------------------------------------
    # 内部: 座標変換
    # ------------------------------------------------------------------

    def _image_rect(self) -> QRect:
        if self._pixmap is None:
            return QRect()
        scaled = self._pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        return QRect(x, y, scaled.width(), scaled.height())

    def _canvas_to_image(self, point: QPoint) -> QPoint:
        ir = self._image_rect()
        if not self._pixmap or ir.isEmpty():
            return point
        sx = self._pixmap.width() / ir.width()
        sy = self._pixmap.height() / ir.height()
        return QPoint(int((point.x() - ir.x()) * sx), int((point.y() - ir.y()) * sy))

    def _image_to_canvas_rect(self, img_rect: QRect) -> QRect:
        ir = self._image_rect()
        if not self._pixmap or ir.isEmpty():
            return img_rect
        sx = ir.width() / self._pixmap.width()
        sy = ir.height() / self._pixmap.height()
        return QRect(
            int(img_rect.x() * sx + ir.x()),
            int(img_rect.y() * sy + ir.y()),
            int(img_rect.width() * sx),
            int(img_rect.height() * sy),
        )

    def _canvas_delta_to_image(self, delta: QPoint) -> QPoint:
        ir = self._image_rect()
        if not self._pixmap or ir.isEmpty():
            return delta
        sx = self._pixmap.width() / ir.width()
        sy = self._pixmap.height() / ir.height()
        return QPoint(int(delta.x() * sx), int(delta.y() * sy))

    # ------------------------------------------------------------------
    # 内部: ヒットテスト / ハンドル
    # ------------------------------------------------------------------

    def _ann_canvas_rect(self, ann: Annotation) -> QRect:
        """アノテーションのキャンバス上の矩形を返す（テキストは推定）。"""
        if isinstance(ann, (RectAnnotation, FilledRectAnnotation)):
            return self._image_to_canvas_rect(ann.rect)
        elif isinstance(ann, TextAnnotation):
            ir = self._image_rect()
            if not self._pixmap or ir.isEmpty():
                return QRect()
            sx = ir.width() / self._pixmap.width()
            sy = ir.height() / self._pixmap.height()
            cx = int(ann.pos.x() * sx + ir.x())
            cy = int(ann.pos.y() * sy + ir.y())
            est_w = max(ann.font_size * len(ann.text), 30)
            return QRect(cx, cy - ann.font_size, est_w, ann.font_size + 4)
        return QRect()

    def _handle_rects(self, canvas_rect: QRect) -> dict[str, QRect]:
        """4コーナーのリサイズハンドル矩形（canvas座標）を返す。"""
        def h(cx, cy):
            return QRect(cx - HANDLE_HALF, cy - HANDLE_HALF, HANDLE_SIZE, HANDLE_SIZE)
        r = canvas_rect
        return {
            "tl": h(r.left(),  r.top()),
            "tr": h(r.right(), r.top()),
            "bl": h(r.left(),  r.bottom()),
            "br": h(r.right(), r.bottom()),
        }

    def _hit_handle(self, pos: QPoint) -> str | None:
        """posがリサイズハンドルに当たっていればキー名を返す。"""
        if not self.has_selection():
            return None
        ann = self._annotations[self._selected_idx]
        if not isinstance(ann, (RectAnnotation, FilledRectAnnotation)):
            return None
        cr = self._ann_canvas_rect(ann)
        for key, hrect in self._handle_rects(cr).items():
            if hrect.contains(pos):
                return key
        return None

    def _hit_annotation(self, pos: QPoint) -> int | None:
        """posに当たっている最前面（後ろのインデックス優先）のアノテーション番号を返す。"""
        img_pos = self._canvas_to_image(pos)
        for i in range(len(self._annotations) - 1, -1, -1):
            ann = self._annotations[i]
            if isinstance(ann, (RectAnnotation, FilledRectAnnotation)):
                # 枠線判定: 外側 + line_width分の余裕
                margin = max(ann.line_width if isinstance(ann, RectAnnotation) else 0, 5)
                outer = ann.rect.adjusted(-margin, -margin, margin, margin)
                if outer.contains(img_pos):
                    return i
            elif isinstance(ann, TextAnnotation):
                cr = self._ann_canvas_rect(ann)
                if cr.contains(pos):
                    return i
        return None

    # ------------------------------------------------------------------
    # 内部: 描画
    # ------------------------------------------------------------------

    def _draw_annotations(self, painter: QPainter, annotations: list[Annotation],
                          cosmetic: bool = False):
        for ann in annotations:
            if isinstance(ann, RectAnnotation):
                pen = QPen(ann.color, ann.line_width)
                pen.setCosmetic(cosmetic)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(ann.rect)
            elif isinstance(ann, FilledRectAnnotation):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(ann.color)
                painter.drawRect(ann.rect)
            elif isinstance(ann, TextAnnotation):
                font = QFont()
                font.setPixelSize(ann.font_size)
                font.setBold(True)
                painter.setFont(font)
                painter.setPen(ann.color)
                painter.drawText(ann.pos, ann.text)

    def _draw_selection(self, painter: QPainter):
        """選択中アノテーションのハイライトとハンドルを描画する。"""
        if not self.has_selection():
            return
        ann = self._annotations[self._selected_idx]
        cr = self._ann_canvas_rect(ann)
        if cr.isEmpty():
            return

        # 青破線ボーダー
        pen = QPen(QColor(0, 160, 255), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(cr.adjusted(-2, -2, 2, 2))

        # 矩形系のみリサイズハンドル
        if isinstance(ann, (RectAnnotation, FilledRectAnnotation)):
            for hrect in self._handle_rects(cr).values():
                painter.setPen(QPen(QColor(0, 160, 255), 1))
                painter.setBrush(QColor(255, 255, 255))
                painter.drawRect(hrect)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.darkGray)

        if not self._pixmap:
            return

        ir = self._image_rect()
        scaled = self._pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(ir.x(), ir.y(), scaled)

        # アノテーション描画（画像スケール空間）
        sx = ir.width() / self._pixmap.width()
        sy = ir.height() / self._pixmap.height()
        painter.save()
        painter.translate(ir.x(), ir.y())
        painter.scale(sx, sy)
        self._draw_annotations(painter, self._annotations, cosmetic=True)
        painter.restore()

        # 選択ハイライト（canvas空間）
        self._draw_selection(painter)

        # ドラッグ中プレビュー（描画ツール）
        if self._drag_start and self._drag_end and self._active_tool in ("rect", "filled_rect"):
            preview_rect = QRect(self._drag_start, self._drag_end).normalized()
            if self._active_tool == "rect":
                pen = QPen(self._color, self._line_width)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self._color)
            painter.drawRect(preview_rect)

    # ------------------------------------------------------------------
    # マウスイベント
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if self._pixmap is None or event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position().toPoint()

        # --- 選択ツール ---
        if self._active_tool == "select":
            handle = self._hit_handle(pos)
            if handle:
                self._drag_mode = f"resize_{handle}"
                self._drag_start_pos = pos
                self._drag_orig_ann = copy.deepcopy(self._annotations[self._selected_idx])
                self._moved = False
                return

            idx = self._hit_annotation(pos)
            if idx is not None:
                self._selected_idx = idx
                self._drag_mode = "move"
                self._drag_start_pos = pos
                self._drag_orig_ann = copy.deepcopy(self._annotations[idx])
                self._moved = False
            else:
                self._selected_idx = None
                self._drag_mode = None
            self.update()
            return

        # --- 描画ツール ---
        if self._active_tool is None:
            return

        if self._active_tool in ("rect", "filled_rect"):
            self._drag_start = pos
            self._drag_end = pos

        elif self._active_tool == "text":
            img_pos = self._canvas_to_image(pos)
            text, ok = QInputDialog.getText(self, "テキスト入力", "テキスト:")
            if ok and text.strip():
                self._push_undo()
                self._annotations.append(
                    TextAnnotation(pos=img_pos, text=text.strip(),
                                   color=QColor(self._color), font_size=self._font_size)
                )
                self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()

        # --- 選択ツール: ドラッグ ---
        if self._active_tool == "select" and self._drag_mode and self._drag_start_pos:
            if not self.has_selection():
                return
            delta_canvas = pos - self._drag_start_pos
            ann = self._annotations[self._selected_idx]
            orig = self._drag_orig_ann
            delta_img = self._canvas_delta_to_image(delta_canvas)

            if self._drag_mode == "move":
                if isinstance(ann, (RectAnnotation, FilledRectAnnotation)):
                    ann.rect = orig.rect.translated(delta_img)
                elif isinstance(ann, TextAnnotation):
                    ann.pos = orig.pos + delta_img
            elif self._drag_mode.startswith("resize_"):
                if isinstance(ann, (RectAnnotation, FilledRectAnnotation)):
                    corner = self._drag_mode[7:]  # "tl"/"tr"/"bl"/"br"
                    r = QRect(orig.rect)
                    if "l" in corner:
                        r.setLeft(orig.rect.left() + delta_img.x())
                    else:
                        r.setRight(orig.rect.right() + delta_img.x())
                    if "t" in corner:
                        r.setTop(orig.rect.top() + delta_img.y())
                    else:
                        r.setBottom(orig.rect.bottom() + delta_img.y())
                    ann.rect = r.normalized()

            self._moved = True
            self.update()
            return

        # --- 描画ツール: ドラッグ ---
        if self._active_tool in ("rect", "filled_rect") and self._drag_start:
            self._drag_end = pos
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return

        # --- 選択ツール ---
        if self._active_tool == "select" and self._drag_mode:
            if self._moved:
                # undo スタックに push（ドラッグ前の状態）
                before = self._undo_stack
                snapshot = copy.deepcopy(self._annotations)
                # orig を元に戻してからpush、その後現在値を再設定
                saved_current = copy.deepcopy(self._annotations)
                self._annotations[self._selected_idx] = self._drag_orig_ann
                self._push_undo()
                self._annotations = saved_current
            self._drag_mode = None
            self._drag_start_pos = None
            self._drag_orig_ann = None
            self._moved = False
            self.update()
            return

        # --- 描画ツール ---
        if self._active_tool in ("rect", "filled_rect") and self._drag_start:
            self._drag_end = event.position().toPoint()
            drag_rect = QRect(self._drag_start, self._drag_end).normalized()
            if drag_rect.width() > 4 and drag_rect.height() > 4:
                ir = self._image_rect()
                sx = self._pixmap.width() / ir.width()
                sy = self._pixmap.height() / ir.height()
                img_rect = QRect(
                    int((drag_rect.x() - ir.x()) * sx),
                    int((drag_rect.y() - ir.y()) * sy),
                    int(drag_rect.width() * sx),
                    int(drag_rect.height() * sy),
                )
                self._push_undo()
                if self._active_tool == "rect":
                    self._annotations.append(
                        RectAnnotation(rect=img_rect, color=QColor(self._color),
                                       line_width=self._line_width)
                    )
                else:
                    self._annotations.append(
                        FilledRectAnnotation(rect=img_rect, color=QColor(self._color))
                    )
            self._drag_start = None
            self._drag_end = None
            self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            self.delete_selected()

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def _push_undo(self):
        self._undo_stack.append(copy.deepcopy(self._annotations))
