import copy
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QKeySequence, QShortcut, QColor, QPainter, QPen, QIcon
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFileDialog,
    QMessageBox,
    QStatusBar,
    QApplication,
    QSpinBox,
    QColorDialog,
    QFrame,
    QComboBox,
    QMenu,
    QSystemTrayIcon,
)

from app import capture, settings
from app.window_selector import start_window_capture
from app.save_options_dialog import SaveOptionsDialog
from app.profile_dialog import ProfileDialog
from app.hotkey_dialog import HotkeyDialog
from app.hotkeys import HotkeyManager
from app.editor import EditorCanvas
from app.ui_utils import color_icon

logger = logging.getLogger(__name__)


def _apply_border_effect(pixmap: QPixmap, prof: dict) -> QPixmap:
    """プロファイル設定に基づいて外枠エフェクトを適用する。無効なら元画像を返す。"""
    if not prof.get("auto_border_enabled", False):
        return pixmap
    result = pixmap.copy()
    painter = QPainter(result)
    w = prof.get("auto_border_width", 4)
    color = QColor(prof.get("auto_border_color", "#ff0000"))
    pen = QPen(color, w)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    offset = w // 2
    painter.drawRect(offset, offset, result.width() - w, result.height() - w)
    painter.end()
    return result


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._root = settings.load()          # {"active_profile":…, "profiles":{…}}
        self._current_color = QColor(255, 0, 0)
        self._backup_path: Path | None = None
        self._active_tool: str | None = None
        self._hotkey_manager = HotkeyManager(self)
        self._selector = None
        self._countdown_timer: QTimer | None = None
        self._countdown_remaining: int = 0
        self._countdown_action = None
        self._tray: QSystemTrayIcon | None = None
        self._setup_ui()
        self._setup_shortcuts()
        self._start_hotkeys()
        self._setup_tray()

    @property
    def _config(self) -> dict:
        """アクティブプロファイルの設定を返す（参照）。"""
        return settings.active_profile(self._root)

    def _setup_ui(self):
        self.setWindowTitle("スクリーンショット")
        self.resize(960, 680)

        # ========== キャプチャツールバー ==========
        capture_bar = QWidget()
        cb_layout = QHBoxLayout(capture_bar)
        cb_layout.setContentsMargins(8, 6, 8, 6)
        cb_layout.setSpacing(6)

        # キャプチャ操作
        self._btn_full = QPushButton("全画面 [F1]")
        self._btn_full.setToolTip("全画面キャプチャ (F1)")
        self._btn_region = QPushButton("範囲選択 [F2]")
        self._btn_region.setToolTip("範囲選択キャプチャ (F2) — Esc でキャンセル")
        self._btn_window = QPushButton("ウィンドウ [F3]")
        self._btn_window.setToolTip("ウィンドウキャプチャ (F3) — Esc でキャンセル")
        lbl_delay = QLabel("遅延:")
        self._spin_delay = QSpinBox()
        self._spin_delay.setRange(0, 30)
        self._spin_delay.setValue(0)
        self._spin_delay.setSuffix(" 秒")
        self._spin_delay.setFixedWidth(72)
        self._spin_delay.setToolTip("キャプチャ前の待機時間（秒）")

        # キャプチャ後操作
        self._btn_copy = QPushButton("コピー [Ctrl+C]")
        self._btn_copy.setToolTip("クリップボードにコピー (Ctrl+C)")
        self._btn_quicksave = QPushButton("即時保存 [Ctrl+Shift+S]")
        self._btn_quicksave.setToolTip("プロファイルの保存先へ即時保存 (Ctrl+Shift+S)")
        self._btn_save = QPushButton("保存... [Ctrl+S]")
        self._btn_save.setToolTip("名前を付けて保存 (Ctrl+S)")

        for btn in (self._btn_copy, self._btn_quicksave, self._btn_save):
            btn.setEnabled(False)

        # ⚙ 設定メニュー（保存先・保存設定・ホットキー設定をまとめる）
        self._btn_settings = QPushButton("⚙ 設定")
        settings_menu = QMenu(self)
        self._action_folder = settings_menu.addAction("保存先...", self._on_choose_folder)
        settings_menu.addAction("保存設定...", self._on_save_options)
        settings_menu.addAction("ホットキー設定...", self._on_hotkey_settings)
        self._btn_settings.setMenu(settings_menu)

        # プロファイル（右端）
        sep_v = QFrame()
        sep_v.setFrameShape(QFrame.Shape.VLine)
        sep_v.setFrameShadow(QFrame.Shadow.Sunken)
        lbl_profile = QLabel("プロファイル:")
        self._combo_profile = QComboBox()
        self._combo_profile.setMinimumWidth(110)
        self._combo_profile.setToolTip("使用するプロファイルを選択")
        self._btn_profile_mgr = QPushButton("管理...")
        self._btn_profile_mgr.setToolTip("プロファイルの追加・削除・設定変更")

        self._btn_full.clicked.connect(self._on_capture_full)
        self._btn_region.clicked.connect(self._on_capture_region)
        self._btn_window.clicked.connect(self._on_capture_window)
        self._btn_copy.clicked.connect(self._on_copy_clipboard)
        self._btn_save.clicked.connect(self._on_save)
        self._btn_quicksave.clicked.connect(self._on_quicksave)
        self._combo_profile.currentTextChanged.connect(self._on_profile_changed)
        self._btn_profile_mgr.clicked.connect(self._on_profile_manage)
        self._refresh_profile_combo()

        for w in (self._btn_full, self._btn_region, self._btn_window,
                  lbl_delay, self._spin_delay,
                  self._btn_copy, self._btn_quicksave, self._btn_save):
            cb_layout.addWidget(w)
        cb_layout.addStretch()
        for w in (self._btn_settings, sep_v,
                  lbl_profile, self._combo_profile, self._btn_profile_mgr):
            cb_layout.addWidget(w)

        # ========== 編集ツールバー ==========
        edit_bar = QWidget()
        eb_layout = QHBoxLayout(edit_bar)
        eb_layout.setContentsMargins(8, 4, 8, 4)
        eb_layout.setSpacing(6)

        # ツール選択ボタン（トグル）
        self._btn_tool_none = QPushButton("閲覧")
        self._btn_tool_none.setToolTip("閲覧モード — スクロール・確認のみ (V)")
        self._btn_tool_select = QPushButton("▶ 選択")
        self._btn_tool_select.setToolTip("選択ツール — クリックで選択/ドラッグで移動 (S)")
        self._btn_tool_rect = QPushButton("■ 矩形")
        self._btn_tool_rect.setToolTip("矩形ツール — ドラッグで枠線を描画 (R)")
        self._btn_tool_filled_rect = QPushButton("█ 四角形")
        self._btn_tool_filled_rect.setToolTip("塗りつぶし四角形ツール — ドラッグで描画 (F)")
        self._btn_tool_text = QPushButton("T テキスト")
        self._btn_tool_text.setToolTip("テキストツール — ダブルクリックで文字を入力 (T)")
        self._tool_buttons = {
            None: self._btn_tool_none,
            "select": self._btn_tool_select,
            "rect": self._btn_tool_rect,
            "filled_rect": self._btn_tool_filled_rect,
            "text": self._btn_tool_text,
        }
        for key, btn in self._tool_buttons.items():
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, k=key: self._on_select_tool(k))

        # 色ピッカー
        self._btn_color = QPushButton("  色")
        self._btn_color.setIcon(color_icon(self._current_color))
        self._btn_color.setToolTip("描画色を選択（選択中のオブジェクトがあれば色を変更）")
        self._btn_color.clicked.connect(self._on_pick_color)

        # 線幅
        lbl_width = QLabel("線幅:")
        self._spin_width = QSpinBox()
        self._spin_width.setRange(1, 20)
        self._spin_width.setValue(2)
        self._spin_width.setSuffix(" px")
        self._spin_width.setToolTip("矩形の線幅")
        self._spin_width.valueChanged.connect(self._on_line_width_changed)

        # フォントサイズ
        lbl_font = QLabel("フォント:")
        self._spin_font_size = QSpinBox()
        self._spin_font_size.setRange(8, 120)
        self._spin_font_size.setValue(16)
        self._spin_font_size.setSuffix(" px")
        self._spin_font_size.setToolTip("テキストのフォントサイズ")
        self._spin_font_size.valueChanged.connect(self._on_font_size_changed)

        # Undo / Redo
        self._btn_undo = QPushButton("↩ Undo [Ctrl+Z]")
        self._btn_undo.setToolTip("直前の操作を元に戻す (Ctrl+Z)")
        self._btn_undo.setEnabled(False)
        self._btn_undo.clicked.connect(self._on_undo)

        self._btn_redo = QPushButton("↪ Redo [Ctrl+Y]")
        self._btn_redo.setToolTip("操作をやり直す (Ctrl+Y)")
        self._btn_redo.setEnabled(False)
        self._btn_redo.clicked.connect(self._on_redo)

        def _make_sep():
            s = QFrame()
            s.setFrameShape(QFrame.Shape.VLine)
            s.setFrameShadow(QFrame.Shadow.Sunken)
            return s

        for w in (self._btn_tool_none, self._btn_tool_select, self._btn_tool_rect,
                  self._btn_tool_filled_rect, self._btn_tool_text,
                  _make_sep(), self._btn_color,
                  lbl_width, self._spin_width,
                  lbl_font, self._spin_font_size,
                  _make_sep(), self._btn_undo, self._btn_redo):
            eb_layout.addWidget(w)
        eb_layout.addStretch()

        # 初期ツール選択
        self._btn_tool_none.setChecked(True)

        # ========== キャンバス ==========
        self._canvas = EditorCanvas()
        self._canvas.set_tool(None)
        self._canvas.undo_stack_changed.connect(self._update_undo_button)
        self._canvas.redo_stack_changed.connect(self._update_redo_button)

        # ========== 中央ウィジェット ==========
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(capture_bar)
        layout.addWidget(edit_bar)
        layout.addWidget(self._canvas, stretch=1)
        self.setCentralWidget(central)

        # ========== ステータスバー ==========
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("F1: 全画面  F2: 範囲選択  F3: ウィンドウ")

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("F1"), self, self._on_capture_full)
        QShortcut(QKeySequence("F2"), self, self._on_capture_region)
        QShortcut(QKeySequence("F3"), self, self._on_capture_window)
        QShortcut(QKeySequence("Ctrl+C"), self, self._on_copy_clipboard)
        QShortcut(QKeySequence("Ctrl+S"), self, self._on_save)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, self._on_quicksave)
        QShortcut(QKeySequence("Ctrl+Z"), self, self._on_undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self._on_redo)
        QShortcut(QKeySequence("Delete"), self, self._on_delete_selected)
        # ツール切り替えショートカット
        QShortcut(QKeySequence("V"), self, lambda: self._on_select_tool(None))
        QShortcut(QKeySequence("S"), self, lambda: self._on_select_tool("select"))
        QShortcut(QKeySequence("R"), self, lambda: self._on_select_tool("rect"))
        QShortcut(QKeySequence("F"), self, lambda: self._on_select_tool("filled_rect"))
        QShortcut(QKeySequence("T"), self, lambda: self._on_select_tool("text"))

    # ------------------------------------------------------------------
    # キャプチャ
    # ------------------------------------------------------------------

    def _delay_ms(self) -> int:
        """ウィンドウ非表示待ち(300ms) + ユーザー指定遅延(ms)の合計。"""
        return 300 + self._spin_delay.value() * 1000

    def _start_countdown(self, action):
        """遅延秒数のカウントダウンをステータスバーに表示してからactionを実行する。"""
        secs = self._spin_delay.value()
        if secs == 0:
            QTimer.singleShot(300, action)
            return

        # 前回のカウントダウンが残っていれば停止
        if self._countdown_timer is not None:
            self._countdown_timer.stop()
            self._countdown_timer.deleteLater()
            self._countdown_timer = None

        self._countdown_remaining = secs
        self._countdown_action = action
        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._tick_countdown)
        self._countdown_timer.start(1000)
        self._status.showMessage(f"キャプチャまで {self._countdown_remaining} 秒...")

    def _tick_countdown(self):
        self._countdown_remaining -= 1
        if self._countdown_remaining > 0:
            self._status.showMessage(f"キャプチャまで {self._countdown_remaining} 秒...")
        else:
            self._countdown_timer.stop()
            self._countdown_timer = None
            self._countdown_action()

    def _set_capture_buttons_enabled(self, enabled: bool):
        for btn in (self._btn_full, self._btn_region, self._btn_window):
            btn.setEnabled(enabled)

    def _on_capture_full(self):
        self._set_capture_buttons_enabled(False)
        if not self.isMinimized():
            self.showMinimized()
        self._start_countdown(self._do_capture_full)

    def _do_capture_full(self):
        pixmap = capture.capture_fullscreen()
        self._set_capture_buttons_enabled(True)
        self.showNormal()
        self.activateWindow()
        self._set_pixmap(pixmap)

    def _on_capture_region(self):
        self._set_capture_buttons_enabled(False)
        if not self.isMinimized():
            self.showMinimized()
        self._start_countdown(self._do_start_region)

    def _on_capture_window(self):
        self._set_capture_buttons_enabled(False)
        if not self.isMinimized():
            self.showMinimized()
        self._start_countdown(self._do_start_window)

    def _do_start_window(self):
        self._release_selector()
        self._selector = start_window_capture(self._on_region_captured, self._on_capture_cancelled)

    def _do_start_region(self):
        self._release_selector()
        self._selector = capture.start_region_capture(self._on_region_captured, self._on_capture_cancelled)

    def _release_selector(self):
        """前回のセレクタウィジェットを解放する。"""
        if self._selector is not None:
            self._selector.deleteLater()
            self._selector = None

    def _on_region_captured(self, pixmap: QPixmap):
        self._set_capture_buttons_enabled(True)
        self.showNormal()
        self.activateWindow()
        self._set_pixmap(pixmap)

    def _on_capture_cancelled(self):
        """範囲選択 / ウィンドウ選択がEscでキャンセルされたときにメインウィンドウを復元する。"""
        self._set_capture_buttons_enabled(True)
        self.showNormal()
        self.activateWindow()
        self._status.showMessage("キャプチャをキャンセルしました")

    def _set_pixmap(self, pixmap: QPixmap):
        self._set_capture_buttons_enabled(True)
        if pixmap is None or pixmap.isNull():
            self.showNormal()
            self.activateWindow()
            self._status.showMessage("キャプチャに失敗しました")
            return
        self._canvas.set_pixmap(pixmap)
        for btn in (self._btn_copy, self._btn_save, self._btn_quicksave):
            btn.setEnabled(True)
        self._btn_undo.setEnabled(False)
        self._btn_redo.setEnabled(False)
        if self._config.get("auto_backup_enabled", True):
            self._auto_backup(pixmap)
        self._status.showMessage(
            f"キャプチャ完了: {pixmap.width()} x {pixmap.height()} px  |  "
            "編集ツールを選択して注釈を追加できます"
        )

    def _auto_backup(self, pixmap: QPixmap):
        """無編集の元画像をバックアップフォルダへ自動保存する。"""
        backup_dir = Path(self._config.get("save_folder", str(Path.home() / "Pictures"))) / "backup"
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("バックアップフォルダの作成に失敗しました: %s: %s", backup_dir, e)
            self._backup_path = None
            return
        path = self._make_filename(backup_dir, "png")
        if pixmap.save(str(path), "PNG"):
            self._backup_path = path
        else:
            self._backup_path = None
            logger.warning("バックアップ保存に失敗しました: %s", path)

    # ------------------------------------------------------------------
    # 編集ツール
    # ------------------------------------------------------------------

    def _on_select_tool(self, tool: str | None):
        self._active_tool = tool
        for key, btn in self._tool_buttons.items():
            btn.setChecked(key == tool)
        self._canvas.set_tool(tool)
        names = {None: "なし", "select": "選択", "rect": "矩形", "filled_rect": "四角形（塗りつぶし）", "text": "テキスト"}
        if tool == "select":
            self._status.showMessage("選択ツール: クリックで選択 / ドラッグで移動 / 角をドラッグでリサイズ / Del で削除")
        else:
            self._status.showMessage(f"ツール: {names.get(tool, tool)}")

    def _on_pick_color(self):
        initial = (self._canvas.get_selected_color()
                   if self._active_tool == "select" and self._canvas.has_selection()
                   else self._current_color)
        color = QColorDialog.getColor(initial, self, "色を選択")
        if not color.isValid():
            return
        if self._active_tool == "select" and self._canvas.has_selection():
            self._canvas.set_selected_color(color)
            self._status.showMessage("選択オブジェクトの色を変更しました")
        else:
            self._current_color = color
            self._btn_color.setIcon(color_icon(color))
            self._canvas.set_color(color)

    def _on_delete_selected(self):
        if self._active_tool == "select":
            self._canvas.delete_selected()
            self._status.showMessage("オブジェクトを削除しました")

    def _on_line_width_changed(self, value: int):
        self._canvas.set_line_width(value)

    def _on_font_size_changed(self, value: int):
        self._canvas.set_font_size(value)

    def _update_undo_button(self, count: int):
        if count > 0:
            self._btn_undo.setText(f"↩ Undo ({count}) [Ctrl+Z]")
            self._btn_undo.setEnabled(True)
        else:
            self._btn_undo.setText("↩ Undo [Ctrl+Z]")
            self._btn_undo.setEnabled(False)

    def _update_redo_button(self, count: int):
        if count > 0:
            self._btn_redo.setText(f"↪ Redo ({count}) [Ctrl+Y]")
            self._btn_redo.setEnabled(True)
        else:
            self._btn_redo.setText("↪ Redo [Ctrl+Y]")
            self._btn_redo.setEnabled(False)

    def _on_undo(self):
        if self._canvas.undo():
            self._status.showMessage("Undo しました")

    def _on_redo(self):
        if self._canvas.redo():
            self._status.showMessage("Redo しました")

    # ------------------------------------------------------------------
    # クリップボード
    # ------------------------------------------------------------------

    def _on_copy_clipboard(self):
        pixmap = self._canvas.get_pixmap()
        if pixmap is None:
            return
        QApplication.clipboard().setPixmap(pixmap)
        self._status.showMessage("クリップボードにコピーしました")

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------

    def _show_toast(self, message: str, duration_ms: int = 2500):
        """ウィンドウ下部中央に短時間表示するトースト通知。"""
        toast = QLabel(message, self)
        toast.setStyleSheet(
            "background: rgba(30,30,30,210); color: white;"
            "border-radius: 6px; padding: 8px 16px; font-size: 13px;"
        )
        toast.adjustSize()
        toast.move((self.width() - toast.width()) // 2,
                   self.height() - toast.height() - 56)
        toast.show()
        toast.raise_()
        QTimer.singleShot(duration_ms, toast.deleteLater)

    def _make_filename(self, folder: Path, ext: str = "png") -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = folder / f"screenshot_{ts}.{ext}"
        if not path.exists():
            return path
        for i in range(2, 100):
            path = folder / f"screenshot_{ts}_{i}.{ext}"
            if not path.exists():
                return path
        # 100件すべて埋まっている場合はマイクロ秒で一意にする
        ts_us = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return folder / f"screenshot_{ts_us}.{ext}"

    def _on_quicksave(self):
        pixmap = self._canvas.get_pixmap()
        if pixmap is None:
            return
        pixmap = self._apply_save_effects(pixmap)
        folder = Path(self._config.get("save_folder", str(Path.home() / "Pictures")))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("保存フォルダの作成に失敗しました: %s: %s", folder, e)
            QMessageBox.critical(self, "エラー", f"保存先フォルダを作成できません:\n{folder}")
            return
        path = self._make_filename(folder, "png")
        if pixmap.save(str(path), "PNG"):
            self._show_toast(f"保存しました: {path.name}")
            self._status.showMessage(f"保存しました: {path}")
            if self._config.get("open_folder_after_save", False):
                try:
                    os.startfile(str(folder))
                except OSError as e:
                    logger.warning("フォルダを開けませんでした: %s: %s", folder, e)
        else:
            QMessageBox.critical(self, "エラー", f"保存に失敗しました:\n{path}")

    def _on_save(self):
        pixmap = self._canvas.get_pixmap()
        if pixmap is None:
            return
        pixmap = self._apply_save_effects(pixmap)
        folder = Path(self._config.get("save_folder", str(Path.home() / "Pictures")))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("保存フォルダの作成に失敗しました: %s: %s", folder, e)
            QMessageBox.critical(self, "エラー", f"保存先フォルダを作成できません:\n{folder}")
            return
        default_path = self._make_filename(folder, "png")
        path, _ = QFileDialog.getSaveFileName(
            self, "名前を付けて保存", str(default_path),
            "PNG画像 (*.png);;JPEG画像 (*.jpg *.jpeg)",
        )
        if not path:
            return
        fmt = "PNG" if path.lower().endswith(".png") else "JPEG"
        if pixmap.save(path, fmt):
            self._rename_backup(Path(path))
            self._show_toast(f"保存しました: {Path(path).name}")
            self._status.showMessage(f"保存しました: {path}")
            if self._config.get("open_folder_after_save", False):
                try:
                    os.startfile(str(Path(path).parent))
                except OSError as e:
                    logger.warning("フォルダを開けませんでした: %s: %s", Path(path).parent, e)
        else:
            QMessageBox.critical(self, "エラー", f"保存に失敗しました:\n{path}")

    def _apply_save_effects(self, pixmap: QPixmap) -> QPixmap:
        """保存時自動エフェクトを適用した画像を返す。"""
        return _apply_border_effect(pixmap, self._config)

    def _save_settings(self) -> None:
        """設定を保存する。失敗してもアプリは継続する。"""
        try:
            settings.save(self._root)
        except Exception as e:
            logger.warning("設定の保存に失敗しました: %s", e)

    def _on_save_options(self):
        dlg = SaveOptionsDialog(self._config, self)
        if dlg.exec():
            updated = dlg.get_config()
            self._config.update(updated)
            self._save_settings()
            self._status.showMessage("保存設定を更新しました")

    def _rename_backup(self, saved_path: Path):
        """保存したファイル名に合わせてバックアップファイルをリネームする。"""
        if self._backup_path is None or not self._backup_path.exists():
            return
        new_backup = self._backup_path.parent / saved_path.name
        if new_backup == self._backup_path:
            return
        # 同名ファイルが既にある場合は上書きしない
        if new_backup.exists():
            return
        try:
            self._backup_path.rename(new_backup)
            self._backup_path = new_backup
        except OSError as e:
            logger.warning("バックアップのリネームに失敗しました: %s → %s: %s", self._backup_path, new_backup, e)

    # ------------------------------------------------------------------
    # 保存先フォルダ
    # ------------------------------------------------------------------

    def _refresh_profile_combo(self):
        self._combo_profile.blockSignals(True)
        self._combo_profile.clear()
        for name in settings.profile_names(self._root):
            self._combo_profile.addItem(name)
        self._combo_profile.setCurrentText(self._root.get("active_profile", ""))
        self._combo_profile.blockSignals(False)
        self._update_folder_label()

    def _start_hotkeys(self):
        """HotkeyManager を起動してシグナルを接続する。"""
        self._hotkey_manager.sig_full.connect(self._on_capture_full)
        self._hotkey_manager.sig_region.connect(self._on_capture_region)
        self._hotkey_manager.sig_window.connect(self._on_capture_window)
        self._hotkey_manager.sig_save.connect(self._on_quicksave)
        self._hotkey_manager.sig_capture_profile.connect(self._on_capture_with_profile)
        self._hotkey_manager.start(self._root.get("hotkey_slots", []))

    def _on_capture_with_profile(self, action: str, profile_name: str):
        """指定プロファイルの設定でキャプチャ＋即時保存する。アクティブプロファイルは変わらない。"""
        profiles = self._root.get("profiles", {})
        if profile_name not in profiles:
            return
        # deepcopy でスナップショットを取る: 300ms 待機中にプロファイル設定が変更されても影響を受けない
        prof = copy.deepcopy(profiles[profile_name])

        self.showMinimized()
        if action == "full":
            QTimer.singleShot(300, lambda: self._do_capture_full_with_profile(prof))
        elif action == "region":
            QTimer.singleShot(300, lambda: self._do_start_region_with_profile(prof))
        elif action == "window":
            QTimer.singleShot(300, lambda: self._do_start_window_with_profile(prof))

    def _do_start_region_with_profile(self, prof: dict):
        self._release_selector()
        self._selector = capture.start_region_capture(
            lambda px: self._on_captured_with_profile(px, prof),
            self._on_capture_cancelled)

    def _do_start_window_with_profile(self, prof: dict):
        self._release_selector()
        self._selector = start_window_capture(
            lambda px: self._on_captured_with_profile(px, prof),
            self._on_capture_cancelled)

    def _do_capture_full_with_profile(self, prof: dict):
        pixmap = capture.capture_fullscreen()
        self.showNormal()
        self.activateWindow()
        self._quicksave_with_profile(pixmap, prof)

    def _on_captured_with_profile(self, pixmap: QPixmap, prof: dict):
        self.showNormal()
        self.activateWindow()
        self._quicksave_with_profile(pixmap, prof)

    def _quicksave_with_profile(self, pixmap: QPixmap, prof: dict):
        """指定プロファイルの設定で即時保存する。"""
        if pixmap is None or pixmap.isNull():
            return
        _default_folder = str(Path.home() / "Pictures")
        save_folder = prof.get("save_folder", _default_folder)

        # バックアップ
        if prof.get("auto_backup_enabled", True):
            backup_dir = Path(save_folder) / "backup"
            try:
                backup_dir.mkdir(parents=True, exist_ok=True)
                if not pixmap.save(str(self._make_filename(backup_dir, "png")), "PNG"):
                    logger.warning("バックアップ保存に失敗しました: %s", backup_dir)
            except OSError as e:
                logger.warning("バックアップフォルダの作成に失敗しました: %s: %s", backup_dir, e)

        # エフェクト適用
        result = _apply_border_effect(pixmap, prof)

        folder = Path(save_folder)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("保存フォルダの作成に失敗しました: %s: %s", folder, e)
            self._status.showMessage(f"保存先フォルダを作成できません: {folder}")
            return
        path = self._make_filename(folder, "png")
        if result.save(str(path), "PNG"):
            self._show_toast(f"保存しました: {path.name}")
            self._status.showMessage(f"保存しました [{save_folder}]: {path.name}")
            if prof.get("open_folder_after_save", False):
                try:
                    os.startfile(str(folder))
                except OSError as e:
                    logger.warning("フォルダを開けませんでした: %s: %s", folder, e)
        else:
            self._status.showMessage(f"保存に失敗しました: {path}")

    def _on_hotkey_settings(self):
        self._hotkey_manager.stop()
        try:
            profile_names = settings.profile_names(self._root)
            dlg = HotkeyDialog(self._root.get("hotkey_slots", []), profile_names, self)
            if dlg.exec():
                self._root["hotkey_slots"] = dlg.get_slots()
                self._save_settings()
                self._status.showMessage("ホットキー設定を更新しました")
        finally:
            self._hotkey_manager.update(self._root.get("hotkey_slots", []))

    def _on_profile_changed(self, name: str):
        if name and name in self._root["profiles"]:
            settings.set_active(self._root, name)
            self._save_settings()
            self._update_folder_label()
            self._hotkey_manager.update(self._root.get("hotkey_slots", []))
            self._status.showMessage(f"プロファイル切り替え: {name}")

    def _on_profile_manage(self):
        dlg = ProfileDialog(self._root, self)
        if dlg.exec():
            self._root = dlg.get_root()
            self._save_settings()
            self._refresh_profile_combo()
            self._hotkey_manager.update(self._root.get("hotkey_slots", []))
            self._status.showMessage("プロファイルを更新しました")

    def _on_choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "保存先フォルダを選択",
            self._config.get("save_folder", str(Path.home() / "Pictures")),
        )
        if folder:
            self._config["save_folder"] = folder
            self._save_settings()
            self._update_folder_label()
            self._status.showMessage(f"保存先: {folder}")

    def _update_folder_label(self):
        folder = self._config.get("save_folder", str(Path.home() / "Pictures"))
        self._action_folder.setText(f"保存先: {folder}")
        self._btn_settings.setToolTip(f"保存先: {folder}")

    # ------------------------------------------------------------------
    # タスクトレイ
    # ------------------------------------------------------------------

    def _setup_tray(self):
        """タスクトレイアイコンとメニューをセットアップする。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(self._make_tray_icon(), self)
        self._tray.setToolTip("スクリーンショット")

        # QSystemTrayIcon は QMenu の所有権を持たないため self を親に指定して保持する
        menu = QMenu(self)
        menu.addAction("ウィンドウを開く", self._show_window)
        menu.addSeparator()
        menu.addAction("全画面キャプチャ", self._on_capture_full)
        menu.addAction("範囲選択キャプチャ", self._on_capture_region)
        menu.addAction("ウィンドウキャプチャ", self._on_capture_window)
        menu.addSeparator()
        # exe実行時のみスタートアップ項目を表示
        if getattr(sys, "frozen", False):
            self._startup_action = menu.addAction("", self._toggle_startup)
            self._update_startup_action()
            menu.addSeparator()
        else:
            self._startup_action = None
        menu.addAction("終了", self._quit_app)
        self._tray.setContextMenu(menu)

        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    @staticmethod
    def _make_tray_icon() -> QIcon:
        """カメラ形のトレイアイコンをプログラムで生成する。"""
        px = QPixmap(32, 32)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            # ボディ
            p.setBrush(QColor(60, 130, 210))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(2, 9, 28, 19, 3, 3)
            # ファインダー突起
            p.drawRoundedRect(11, 5, 10, 6, 2, 2)
            # レンズ（外）
            p.setBrush(QColor(220, 235, 255))
            p.drawEllipse(9, 12, 14, 14)
            # レンズ（内）
            p.setBrush(QColor(60, 130, 210))
            p.drawEllipse(12, 15, 8, 8)
            # シャッターボタン
            p.setBrush(QColor(255, 255, 255, 180))
            p.drawEllipse(24, 11, 4, 4)
        finally:
            p.end()
        return QIcon(px)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _show_window(self):
        """ウィンドウを前面に表示する。"""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    # ------------------------------------------------------------------
    # スタートアップ登録（タスクスケジューラ経由・管理者権限で自動起動）
    # ------------------------------------------------------------------

    _TASK_NAME = "screenshotsv_autostart"

    @staticmethod
    def _is_startup_registered() -> bool:
        """タスクスケジューラにスタートアップタスクが登録されているか確認する。"""
        try:
            result = subprocess.run(
                ["schtasks", "/query", "/tn", MainWindow._TASK_NAME],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _update_startup_action(self):
        if self._startup_action is None:
            return
        if self._is_startup_registered():
            self._startup_action.setText("スタートアップから削除")
        else:
            self._startup_action.setText("スタートアップに追加（ログオン時に自動起動）")

    def _toggle_startup(self):
        if self._is_startup_registered():
            self._unregister_startup()
        else:
            self._register_startup()
        self._update_startup_action()

    def _register_startup(self):
        """ログオン時に管理者権限で起動するタスクを登録する。"""
        exe_path = sys.executable
        try:
            result = subprocess.run(
                [
                    "schtasks", "/create",
                    "/tn", self._TASK_NAME,
                    "/tr", f'"{exe_path}"',
                    "/sc", "onlogon",
                    "/rl", "highest",
                    "/f",
                ],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                self._tray.showMessage("スクリーンショット", "スタートアップに登録しました。\n次回ログオン時から自動起動します。",
                                       QSystemTrayIcon.MessageIcon.Information, 2500)
            else:
                raise RuntimeError(result.stderr.decode("cp932", errors="replace"))
        except Exception as e:
            logger.warning("スタートアップ登録に失敗しました: %s", e)
            QMessageBox.warning(self, "エラー", f"スタートアップへの登録に失敗しました:\n{e}")

    def _unregister_startup(self):
        """スタートアップタスクを削除する。"""
        try:
            result = subprocess.run(
                ["schtasks", "/delete", "/tn", self._TASK_NAME, "/f"],
                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                self._tray.showMessage("スクリーンショット", "スタートアップから削除しました。",
                                       QSystemTrayIcon.MessageIcon.Information, 2500)
            else:
                raise RuntimeError(result.stderr.decode("cp932", errors="replace"))
        except Exception as e:
            logger.warning("スタートアップ削除に失敗しました: %s", e)
            QMessageBox.warning(self, "エラー", f"スタートアップからの削除に失敗しました:\n{e}")

    def _quit_app(self):
        """トレイメニューの「終了」から完全に終了する。"""
        if self._countdown_timer is not None:
            self._countdown_timer.stop()
            self._countdown_timer = None
        self._hotkey_manager.stop()
        self._release_selector()
        QApplication.quit()

    # ------------------------------------------------------------------
    # ウィンドウ終了
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        """×ボタンはトレイに格納する。トレイが使えない場合は終了する。"""
        if self._tray is not None and self._tray.isVisible():
            event.ignore()
            if self._countdown_timer is not None:
                self._countdown_timer.stop()
                self._countdown_timer = None
            self.hide()
            self._tray.showMessage(
                "スクリーンショット",
                "バックグラウンドで動作しています。\nホットキーは引き続き有効です。",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
        else:
            # トレイが使えない環境、またはトレイが非表示になった場合は終了する
            if self._countdown_timer is not None:
                self._countdown_timer.stop()
                self._countdown_timer = None
            self._hotkey_manager.stop()
            self._release_selector()
            super().closeEvent(event)
