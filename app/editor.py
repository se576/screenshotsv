from collections import deque

from PySide6.QtCore import Qt, QRect, QRectF, QPoint, QSize, Signal
from PySide6.QtGui import (
    QPixmap, QPainter, QColor, QPen, QFont, QCursor, QFontMetrics,
    QMouseEvent, QWheelEvent, QKeyEvent, QPaintEvent, QResizeEvent, QFocusEvent,
)
from PySide6.QtWidgets import QWidget, QSizePolicy, QInputDialog, QScrollBar, QGridLayout

from app.annotations import RectAnnotation, FilledRectAnnotation, TextAnnotation, Annotation

_UNDO_LIMIT = 50

# ズーム倍率（1.0 = 画像ピクセル等倍）
ZOOM_MIN = 0.10
ZOOM_MAX = 8.0
ZOOM_STEP = 1.25  # ホイール/ボタン1段あたりの倍率
_WHEEL_SCROLL_PX = 60  # ホイール1ノッチあたりのスクロール量（canvas px）

def _copy_annotations(annotations: list) -> list:
    """PySide6 オブジェクトを含む Annotation リストを安全にコピーする。"""
    return [ann.copy() for ann in annotations]


HANDLE_SIZE = 10  # リサイズハンドルのサイズ（canvas px）
HANDLE_HALF = HANDLE_SIZE // 2
_HANDLES = ("tl", "tr", "bl", "br")  # top-left, top-right, bottom-left, bottom-right


class EditorCanvas(QWidget):
    """
    スクリーンショット表示 + アノテーション描画キャンバス。
    ツール: "select" | "rect" | "filled_rect" | "text" | "crop" | None

    Undo/Redo スタックの各エントリは (pixmap, annotations) のタプル。
    QPixmap は暗黙共有のため参照保持のコストはほぼゼロで、
    トリミングで画像自体が変わる操作も同じスタックで巻き戻せる。
    """

    undo_stack_changed = Signal(int)  # Undoスタックのサイズを emit
    redo_stack_changed = Signal(int)  # Redoスタックのサイズを emit
    crop_applied = Signal(int, int)   # トリミング後の画像サイズ (width, height) を emit
    zoom_changed = Signal(float)      # 実効表示倍率（フィット時も実値）を emit
    view_changed = Signal()           # ズーム/パン/画像サイズなど表示領域の変化を emit

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._annotations: list[Annotation] = []
        self._undo_stack: deque = deque(maxlen=_UNDO_LIMIT)
        self._redo_stack: deque = deque(maxlen=_UNDO_LIMIT)

        # スケール済みキャッシュ（paintEvent の二重スケール防止）
        self._scaled_cache: QPixmap | None = None
        self._scaled_cache_size: QSize | None = None

        # ツール設定
        self._active_tool: str | None = None
        self._color: QColor = QColor(255, 0, 0)
        self._line_width: float = 2.0
        self._font_size: int = 16

        # テキスト注釈のcanvas矩形推定用 QFontMetrics キャッシュ（ピクセルサイズ→FM）。
        # ドラッグ中の毎フレーム生成を避ける
        self._fm_cache: dict[int, QFontMetrics] = {}

        # 描画ドラッグ用（rect / filled_rect）
        self._drag_start: QPoint | None = None
        self._drag_end: QPoint | None = None

        # 選択ツール用
        self._selected_idx: int | None = None
        self._drag_mode: str | None = None        # "move" | "resize_tl/tr/bl/br"
        self._drag_start_pos: QPoint | None = None
        self._drag_orig_ann: Annotation | None = None
        self._moved: bool = False  # ドラッグで実際に動いたか（Undo判定用）

        # ズーム / パン
        self._zoom: float | None = None    # None = フィット表示（従来動作）
        self._pan = QPoint(0, 0)           # スクロール量（画像がはみ出すときのみ有効）
        self._space_down: bool = False     # スペース押下中（手のひらツール）
        self._panning: bool = False        # パンドラッグ中
        self._pan_anchor_mouse: QPoint | None = None
        self._pan_anchor: QPoint | None = None

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def set_pixmap(self, pixmap: QPixmap) -> None:
        # 「画像px = キャンバスpx 1:1」を全座標変換の前提にしているため、
        # 高DPIモニタの grabWindow が DPR>1 の pixmap を返しても倍率がずれないよう固定する
        pixmap.setDevicePixelRatio(1.0)
        self._pixmap = pixmap
        self._annotations.clear()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._scaled_cache = None
        self._scaled_cache_size = None
        self._drag_start = None
        self._drag_end = None
        self._selected_idx = None
        self._drag_mode = None
        self._zoom = None  # 新しいキャプチャはフィット表示から始める
        self._pan = QPoint(0, 0)
        self.undo_stack_changed.emit(0)
        self.redo_stack_changed.emit(0)
        self._refresh_view()

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
        self._update_cursor()
        self.update()

    def set_color(self, color: QColor) -> None:
        self._color = color

    def set_line_width(self, width: float) -> None:
        self._line_width = width

    def set_font_size(self, size: int) -> None:
        self._font_size = size

    def _snapshot(self) -> tuple:
        """現在の編集状態（画像 + 注釈）のスナップショットを返す。"""
        return (self._pixmap, _copy_annotations(self._annotations))

    def _restore(self, snap: tuple) -> None:
        """スナップショットから編集状態を復元する。"""
        pixmap, annotations = snap
        if pixmap is not self._pixmap:
            self._pixmap = pixmap
            self._scaled_cache = None
            self._scaled_cache_size = None
        self._annotations = annotations

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        self._redo_stack.append(self._snapshot())
        self._restore(self._undo_stack.pop())
        self._selected_idx = None
        self.undo_stack_changed.emit(len(self._undo_stack))
        self.redo_stack_changed.emit(len(self._redo_stack))
        self._refresh_view()  # トリミング undo で画像サイズが変わることがある
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._undo_stack.append(self._snapshot())
        self._restore(self._redo_stack.pop())
        self._selected_idx = None
        self.undo_stack_changed.emit(len(self._undo_stack))
        self.redo_stack_changed.emit(len(self._redo_stack))
        self._refresh_view()
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
    # 内部: スケールキャッシュ
    # ------------------------------------------------------------------

    def _get_scaled(self) -> QPixmap | None:
        """縮小表示用のスケール済みキャッシュを返す（等倍以下でのみ使用）。"""
        if self._pixmap is None:
            return None
        target = self._image_rect().size()
        if target.isEmpty():
            return None
        if self._scaled_cache is None or self._scaled_cache_size != target:
            # target は _display_size() でアスペクト比計算済み。
            # KeepAspectRatio だと丸め誤差で 1px ずれるため Ignore で正確に合わせる
            self._scaled_cache = self._pixmap.scaled(
                target,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scaled_cache_size = target
        return self._scaled_cache

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        # フィット時は実効倍率が変わり、ズーム時ははみ出し量が変わる
        self._refresh_view()

    # ------------------------------------------------------------------
    # ズーム / パン
    # ------------------------------------------------------------------

    def current_zoom(self) -> float:
        """実効表示倍率（画像px → キャンバスpx）を返す。"""
        return self._zoom if self._zoom is not None else self._fit_zoom()

    def is_fit(self) -> bool:
        return self._zoom is None

    def _fit_zoom(self) -> float:
        if (self._pixmap is None
                or self._pixmap.width() == 0 or self._pixmap.height() == 0):
            return 1.0
        return min(self.width() / self._pixmap.width(),
                   self.height() / self._pixmap.height())

    def _display_size(self) -> QSize:
        """現在の倍率での画像の表示サイズ（canvas px）を返す。"""
        if (self._pixmap is None
                or self._pixmap.width() == 0 or self._pixmap.height() == 0):
            return QSize()
        zoom = self.current_zoom()
        return QSize(max(1, round(self._pixmap.width() * zoom)),
                     max(1, round(self._pixmap.height() * zoom)))

    def set_zoom(self, zoom: float | None, anchor: QPoint | None = None) -> None:
        """表示倍率を設定する。None でフィット表示に戻す。
        anchor（キャンバス座標）直下の画像上の点を固定して拡縮する。省略時は中央固定。"""
        if self._pixmap is None:
            return
        if zoom is None:
            self._zoom = None
            self._pan = QPoint(0, 0)
        else:
            # フィット倍率が標準範囲外になる画像（超横長・極小キャプチャ等）でも
            # ＋/−ボタンが逆方向に働かないよう、クランプ範囲にフィット倍率を含める
            lo = min(ZOOM_MIN, self._fit_zoom())
            hi = max(ZOOM_MAX, self._fit_zoom())
            zoom = max(lo, min(hi, zoom))
            old_ir = self._image_rect()
            old_zoom = self.current_zoom()
            if anchor is None:
                anchor = self.rect().center()
            self._zoom = zoom
            if not old_ir.isEmpty() and old_zoom > 0:
                # anchor 直下の画像上の点が拡縮後も同じ画面位置に来るよう pan を合わせる
                img_x = (anchor.x() - old_ir.x()) / old_zoom
                img_y = (anchor.y() - old_ir.y()) / old_zoom
                self._pan = QPoint(round(img_x * zoom - anchor.x()),
                                   round(img_y * zoom - anchor.y()))
            self._clamp_pan()
        self.update()
        self.zoom_changed.emit(self.current_zoom())
        self.view_changed.emit()

    def zoom_in(self) -> None:
        self.set_zoom(self.current_zoom() * ZOOM_STEP)

    def zoom_out(self) -> None:
        self.set_zoom(self.current_zoom() / ZOOM_STEP)

    def pan(self) -> QPoint:
        return QPoint(self._pan)

    def set_pan(self, x: int, y: int) -> None:
        """スクロール位置を設定する（スクロールバーからの呼び出し用。view_changed は発火しない）。"""
        new = QPoint(x, y)
        if new == self._pan:
            return
        self._pan = new
        self._clamp_pan()
        self.update()

    def scroll_info(self) -> tuple[int, int, int, int]:
        """(横スクロール最大値, 縦最大値, 横ページ幅, 縦ページ幅) を返す。最大値 0 = スクロール不要。"""
        ds = self._display_size()
        return (max(0, ds.width() - self.width()),
                max(0, ds.height() - self.height()),
                self.width(), self.height())

    def _clamp_pan(self) -> None:
        ds = self._display_size()
        max_x = max(0, ds.width() - self.width())
        max_y = max(0, ds.height() - self.height())
        self._pan = QPoint(min(max(0, self._pan.x()), max_x),
                           min(max(0, self._pan.y()), max_y))

    def _refresh_view(self) -> None:
        """画像サイズ・ビューポートの変化後に pan を再クランプし、表示系シグナルを発火する。"""
        self._clamp_pan()
        self.update()
        self.zoom_changed.emit(self.current_zoom())
        self.view_changed.emit()

    def _update_cursor(self) -> None:
        if self._panning:
            shape = Qt.CursorShape.ClosedHandCursor
        elif self._space_down:
            shape = Qt.CursorShape.OpenHandCursor
        elif self._active_tool in (None, "select"):
            shape = Qt.CursorShape.ArrowCursor
        else:
            shape = Qt.CursorShape.CrossCursor
        self.setCursor(QCursor(shape))

    # ------------------------------------------------------------------
    # 内部: 座標変換
    # ------------------------------------------------------------------

    def _image_rect(self) -> QRect:
        """表示中画像のキャンバス上の矩形（ズーム・パン反映済み）。全座標変換の基準。"""
        ds = self._display_size()
        if ds.isEmpty():
            return QRect()
        if ds.width() <= self.width():
            x = (self.width() - ds.width()) // 2
        else:
            x = -min(max(0, self._pan.x()), ds.width() - self.width())
        if ds.height() <= self.height():
            y = (self.height() - ds.height()) // 2
        else:
            y = -min(max(0, self._pan.y()), ds.height() - self.height())
        return QRect(x, y, ds.width(), ds.height())

    # 変換は int() 切り捨てではなく round() を使う（切り捨ては常に左上方向へ
    # 偏るため、配置確定時にプレビューからずれて見える）。矩形は x/y/w/h を
    # 独立に丸めると右下エッジがドリフトするため、四隅の座標を丸めて差を取る。

    def _canvas_to_image(self, point: QPoint) -> QPoint:
        ir = self._image_rect()
        if not self._pixmap or ir.isEmpty():
            return point
        sx = self._pixmap.width() / ir.width()
        sy = self._pixmap.height() / ir.height()
        return QPoint(round((point.x() - ir.x()) * sx), round((point.y() - ir.y()) * sy))

    def _image_to_canvas_rect(self, img_rect: QRect) -> QRect:
        ir = self._image_rect()
        if not self._pixmap or ir.isEmpty():
            return img_rect
        sx = ir.width() / self._pixmap.width()
        sy = ir.height() / self._pixmap.height()
        x1 = round(img_rect.x() * sx + ir.x())
        y1 = round(img_rect.y() * sy + ir.y())
        x2 = round((img_rect.x() + img_rect.width()) * sx + ir.x())
        y2 = round((img_rect.y() + img_rect.height()) * sy + ir.y())
        return QRect(x1, y1, x2 - x1, y2 - y1)

    def _canvas_delta_to_image(self, delta: QPoint) -> QPoint:
        ir = self._image_rect()
        if not self._pixmap or ir.isEmpty():
            return delta
        sx = self._pixmap.width() / ir.width()
        sy = self._pixmap.height() / ir.height()
        return QPoint(round(delta.x() * sx), round(delta.y() * sy))

    def _canvas_rect_to_image(self, canvas_rect: QRect) -> QRect | None:
        """キャンバス座標の矩形を画像座標に変換する。画像未表示なら None。"""
        ir = self._image_rect()
        if not self._pixmap or ir.isEmpty():
            return None
        sx = self._pixmap.width() / ir.width()
        sy = self._pixmap.height() / ir.height()
        x1 = round((canvas_rect.left() - ir.x()) * sx)
        y1 = round((canvas_rect.top() - ir.y()) * sy)
        x2 = round((canvas_rect.left() + canvas_rect.width() - ir.x()) * sx)
        y2 = round((canvas_rect.top() + canvas_rect.height() - ir.y()) * sy)
        return QRect(x1, y1, x2 - x1, y2 - y1)

    # ------------------------------------------------------------------
    # 内部: トリミング
    # ------------------------------------------------------------------

    def _apply_crop(self, img_rect: QRect) -> None:
        """画像を img_rect（画像座標）で切り抜き、注釈を新しい原点に合わせて移動する。"""
        rect = img_rect.normalized().intersected(self._pixmap.rect())
        if rect.width() < 2 or rect.height() < 2:
            return
        if rect == self._pixmap.rect():
            return  # 全体選択は何もしない
        self._push_undo()
        self._pixmap = self._pixmap.copy(rect)
        for ann in self._annotations:
            if isinstance(ann, (RectAnnotation, FilledRectAnnotation)):
                ann.rect.translate(-rect.x(), -rect.y())
            elif isinstance(ann, TextAnnotation):
                ann.pos = QPoint(ann.pos.x() - rect.x(), ann.pos.y() - rect.y())
        self._selected_idx = None
        self._scaled_cache = None
        self._scaled_cache_size = None
        self._refresh_view()  # 画像サイズが変わるため pan 再クランプ + 倍率表示更新
        self.crop_applied.emit(rect.width(), rect.height())

    # ------------------------------------------------------------------
    # 内部: ヒットテスト / ハンドル
    # ------------------------------------------------------------------

    def _text_metrics(self, pixel_size: int) -> QFontMetrics:
        """指定ピクセルサイズの太字 QFontMetrics をキャッシュ経由で返す。"""
        fm = self._fm_cache.get(pixel_size)
        if fm is None:
            font = QFont()
            font.setPixelSize(pixel_size)
            font.setBold(True)
            fm = QFontMetrics(font)
            self._fm_cache[pixel_size] = fm
        return fm

    def _ann_canvas_rect(self, ann: Annotation) -> QRect:
        """アノテーションのキャンバス上の矩形を返す。"""
        if isinstance(ann, (RectAnnotation, FilledRectAnnotation)):
            return self._image_to_canvas_rect(ann.rect)
        elif isinstance(ann, TextAnnotation):
            ir = self._image_rect()
            if not self._pixmap or ir.isEmpty():
                return QRect()
            sx = ir.width() / self._pixmap.width()
            sy = ir.height() / self._pixmap.height()
            cx = round(ann.pos.x() * sx + ir.x())
            cy = round(ann.pos.y() * sy + ir.y())
            # QFontMetrics でテキスト幅を正確に推定
            fm = self._text_metrics(max(1, round(ann.font_size * sy)))
            est_w = fm.horizontalAdvance(ann.text)
            est_h = fm.height()
            return QRect(cx, cy - est_h, est_w, est_h + 4)
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
                # 枠線判定: 外側 + line_width分の余裕（QRect.adjusted は int 専用）
                margin = int(max(ann.line_width if isinstance(ann, RectAnnotation) else 0, 5))
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
                          cosmetic: bool = False) -> None:
        # 0.1px 単位の線幅を正しく表現するためアンチエイリアスを有効にする
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
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

    def _draw_selection(self, painter: QPainter) -> None:
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
            painter.setPen(QPen(QColor(0, 160, 255), 1))
            painter.setBrush(QColor(255, 255, 255))
            for hrect in self._handle_rects(cr).values():
                painter.drawRect(hrect)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        if self._pixmap is None:
            painter.fillRect(self.rect(), Qt.GlobalColor.darkGray)
            return
        painter.fillRect(self.rect(), Qt.GlobalColor.darkGray)

        ir = self._image_rect()
        if ir.isEmpty():
            return
        zoom = self.current_zoom()
        if zoom <= 1.0:
            # 等倍以下: スムージング済みキャッシュを使用（従来動作）
            scaled = self._get_scaled()
            if scaled is None:
                return
            painter.drawPixmap(ir.x(), ir.y(), scaled)
        else:
            # 拡大時: 巨大なスケール済み画像を作らず、可視領域のみ直接描画する。
            # スムージングなし = ピクセル忠実（細部確認用途）かつ高速
            visible = ir.intersected(self.rect())
            if visible.isEmpty():
                return
            source = QRectF((visible.x() - ir.x()) / zoom,
                            (visible.y() - ir.y()) / zoom,
                            visible.width() / zoom,
                            visible.height() / zoom)
            painter.drawPixmap(QRectF(visible), self._pixmap, source)

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

        # ドラッグ中プレビュー（描画ツール）: 先に画像座標へスナップし、確定後と
        # 同一の変換・描画経路を通すことで、配置確定の瞬間にずれが生じないようにする
        if self._drag_start and self._drag_end and self._active_tool in ("rect", "filled_rect"):
            img_rect = self._canvas_rect_to_image(
                QRect(self._drag_start, self._drag_end).normalized())
            if img_rect is not None:
                if self._active_tool == "rect":
                    preview = RectAnnotation(rect=img_rect, color=QColor(self._color),
                                             line_width=self._line_width)
                else:
                    preview = FilledRectAnnotation(rect=img_rect, color=QColor(self._color))
                painter.save()
                painter.translate(ir.x(), ir.y())
                painter.scale(sx, sy)
                self._draw_annotations(painter, [preview], cosmetic=True)
                painter.restore()

        # ドラッグ中プレビュー（トリミング: 範囲外を暗転 + 破線枠 + サイズ表示）
        if self._drag_start and self._drag_end and self._active_tool == "crop":
            self._draw_crop_preview(painter)

    def _draw_crop_preview(self, painter: QPainter) -> None:
        sel = QRect(self._drag_start, self._drag_end).normalized()
        full = self.rect()
        overlay = QColor(0, 0, 0, 120)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(overlay)
        painter.drawRect(QRect(full.left(), full.top(), full.width(), sel.top() - full.top()))
        painter.drawRect(QRect(full.left(), sel.bottom() + 1,
                               full.width(), full.bottom() - sel.bottom()))
        painter.drawRect(QRect(full.left(), sel.top(), sel.left() - full.left(), sel.height()))
        painter.drawRect(QRect(sel.right() + 1, sel.top(),
                               full.right() - sel.right(), sel.height()))

        pen = QPen(QColor(0, 160, 255), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(sel)

        # 画像座標でのサイズを選択枠の上に表示
        img_rect = self._canvas_rect_to_image(sel)
        if img_rect is not None:
            clamped = img_rect.intersected(self._pixmap.rect())
            label = f"{clamped.width()} x {clamped.height()} px"
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(sel.left() + 4, max(sel.top() - 6, 12), label)

    # ------------------------------------------------------------------
    # マウスイベント
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._pixmap is None or event.button() != Qt.MouseButton.LeftButton:
            return

        # --- スペース+ドラッグ: パン開始 ---
        if self._space_down:
            self._panning = True
            self._pan_anchor_mouse = event.position().toPoint()
            self._pan_anchor = QPoint(self._pan)
            self._update_cursor()
            return

        pos = event.position().toPoint()

        # --- 選択ツール ---
        if self._active_tool == "select":
            handle = self._hit_handle(pos)
            if handle:
                self._drag_mode = f"resize_{handle}"
                self._drag_start_pos = pos
                self._drag_orig_ann = self._annotations[self._selected_idx].copy()
                self._moved = False
                return

            idx = self._hit_annotation(pos)
            if idx is not None:
                self._selected_idx = idx
                self._drag_mode = "move"
                self._drag_start_pos = pos
                self._drag_orig_ann = self._annotations[idx].copy()
                self._moved = False
            else:
                self._selected_idx = None
                self._drag_mode = None
            self.update()
            return

        # --- 描画ツール ---
        if self._active_tool is None:
            return

        if self._active_tool in ("rect", "filled_rect", "crop"):
            self._drag_start = pos
            self._drag_end = pos
        # text ツールはダブルクリックで入力（mouseDoubleClickEvent 参照）

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()

        # --- パンドラッグ ---
        if self._panning:
            delta = pos - self._pan_anchor_mouse
            self._pan = self._pan_anchor - delta  # 画像がマウスに追従する向き
            self._clamp_pan()
            self.update()
            self.view_changed.emit()
            return
        if self._space_down:
            return  # 手のひらツール中は他ツールを動かさない

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

        # --- 描画ツール / トリミング: ドラッグ ---
        if self._active_tool in ("rect", "filled_rect", "crop") and self._drag_start:
            self._drag_end = pos
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        # --- パンドラッグ終了 ---
        if self._panning:
            self._panning = False
            self._pan_anchor_mouse = None
            self._pan_anchor = None
            self._update_cursor()
            return

        # --- 選択ツール ---
        if self._active_tool == "select" and self._drag_mode:
            if (self._moved
                    and self._selected_idx is not None
                    and self._selected_idx < len(self._annotations)
                    and self._annotations[self._selected_idx] != self._drag_orig_ann):
                # 実内容が変化したドラッグのみ Undo に積む（微小ジグルで delta が
                # 0px に丸まった場合は空振りエントリを作らない）。
                # ドラッグ前の状態を直接構築してundoスタックに積む
                pre_drag = _copy_annotations(self._annotations)
                pre_drag[self._selected_idx] = self._drag_orig_ann
                self._undo_stack.append((self._pixmap, pre_drag))
                self._redo_stack.clear()
                self.undo_stack_changed.emit(len(self._undo_stack))
                self.redo_stack_changed.emit(0)
            self._drag_mode = None
            self._drag_start_pos = None
            self._drag_orig_ann = None
            self._moved = False
            self.update()
            return

        # --- トリミングツール ---
        if self._active_tool == "crop" and self._drag_start:
            drag_rect = QRect(self._drag_start, event.position().toPoint()).normalized()
            self._drag_start = None
            self._drag_end = None
            if drag_rect.width() > 4 and drag_rect.height() > 4:
                img_rect = self._canvas_rect_to_image(drag_rect)
                if img_rect is not None:
                    self._apply_crop(img_rect)
            self.update()
            return

        # --- 描画ツール ---
        if self._active_tool in ("rect", "filled_rect") and self._drag_start:
            self._drag_end = event.position().toPoint()
            drag_rect = QRect(self._drag_start, self._drag_end).normalized()
            if drag_rect.width() > 4 and drag_rect.height() > 4:
                img_rect = self._canvas_rect_to_image(drag_rect)
                # 高ズーム時は数canvas pxが1画像px未満に丸まり得る。0幅/0高の
                # 矩形（drawRect が線になる無意味な注釈）は確定しない
                if img_rect is not None and img_rect.width() >= 1 and img_rect.height() >= 1:
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

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self._pixmap is None or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._active_tool == "text":
            pos = event.position().toPoint()
            img_pos = self._canvas_to_image(pos)
            text, ok = QInputDialog.getText(self, "テキスト入力", "テキスト:")
            if ok and text.strip():
                self._push_undo()
                self._annotations.append(
                    TextAnnotation(pos=img_pos, text=text.strip(),
                                   color=QColor(self._color), font_size=self._font_size)
                )
                self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._pixmap is None:
            return
        # Ctrl+ホイール: カーソル位置を中心にズーム
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                factor = ZOOM_STEP if delta > 0 else 1.0 / ZOOM_STEP
                self.set_zoom(self.current_zoom() * factor,
                              anchor=event.position().toPoint())
            event.accept()
            return
        # ズーム中はホイールで縦スクロール（Shift または水平ホイールで横スクロール）
        if not self.is_fit():
            horizontal = (event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                          or event.angleDelta().x() != 0)
            delta = event.angleDelta().x() if event.angleDelta().x() else event.angleDelta().y()
            step = -round(delta / 120 * _WHEEL_SCROLL_PX)
            self._pan += QPoint(step, 0) if horizontal else QPoint(0, step)
            self._clamp_pan()
            self.update()
            self.view_changed.emit()
            event.accept()
            return
        event.ignore()  # フィット表示中の素のホイールは親へ伝播させる

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = True
            if not self._panning:
                self._update_cursor()
            return
        if event.key() == Qt.Key.Key_Delete:
            self.delete_selected()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = False
            if not self._panning:  # ドラッグ中はマウスリリースまで手のひらを維持
                self._update_cursor()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        # フォーカス喪失でスペースが押しっぱなし扱いになるのを防ぐ
        self._space_down = False
        self._panning = False
        self._pan_anchor_mouse = None
        self._pan_anchor = None
        self._update_cursor()
        super().focusOutEvent(event)

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------

    def _push_undo(self):
        self._undo_stack.append(self._snapshot())
        # deque(maxlen=_UNDO_LIMIT) が上限超過時に自動で古いエントリを削除する
        self._redo_stack.clear()
        self.undo_stack_changed.emit(len(self._undo_stack))
        self.redo_stack_changed.emit(0)


class CanvasScrollView(QWidget):
    """EditorCanvas に「ズームではみ出したときのみ表示する」スクロールバーを付けるコンテナ。

    QScrollArea は使わず、キャンバスの pan とスクロールバーを双方向同期する。
    キャンバス自体のサイズ・描画・座標変換には一切関与しない。
    """

    def __init__(self, canvas: EditorCanvas, parent=None):
        super().__init__(parent)
        self._canvas = canvas
        self._syncing = False  # setValue → valueChanged の往復を防ぐ

        self._hbar = QScrollBar(Qt.Orientation.Horizontal)
        self._vbar = QScrollBar(Qt.Orientation.Vertical)
        self._hbar.hide()
        self._vbar.hide()

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(canvas, 0, 0)
        grid.addWidget(self._vbar, 0, 1)
        grid.addWidget(self._hbar, 1, 0)
        grid.setRowStretch(0, 1)
        grid.setColumnStretch(0, 1)

        canvas.view_changed.connect(self._sync_scrollbars)
        self._hbar.valueChanged.connect(self._on_scrolled)
        self._vbar.valueChanged.connect(self._on_scrolled)

    def _sync_scrollbars(self) -> None:
        max_x, max_y, page_x, page_y = self._canvas.scroll_info()
        pan = self._canvas.pan()
        self._syncing = True
        try:
            for bar, maximum, page, value in (
                    (self._hbar, max_x, page_x, pan.x()),
                    (self._vbar, max_y, page_y, pan.y())):
                if maximum > 0:
                    bar.setRange(0, maximum)
                    bar.setPageStep(page)
                    bar.setSingleStep(max(1, page // 20))
                    bar.setValue(value)
                    bar.show()
                else:
                    bar.hide()
                    bar.setRange(0, 0)
        finally:
            self._syncing = False

    def _on_scrolled(self) -> None:
        if self._syncing:
            return
        self._canvas.set_pan(self._hbar.value(), self._vbar.value())
