import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QKeySequence, QShortcut, QColor, QIcon, QPixmap as QPixmapIcon
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
    QSizePolicy,
    QApplication,
    QSpinBox,
    QColorDialog,
    QFrame,
    QComboBox,
)

from app import capture, settings
from app.window_selector import start_window_capture
from app.save_options_dialog import SaveOptionsDialog
from app.profile_dialog import ProfileDialog
from app.hotkey_dialog import HotkeyDialog
from app.hotkeys import HotkeyManager
from app.editor import EditorCanvas


def _color_icon(color: QColor, size: int = 16) -> QIcon:
    """指定色の正方形アイコンを生成する。"""
    pm = QPixmapIcon(size, size)
    pm.fill(color)
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._root = settings.load()          # {"active_profile":…, "profiles":{…}}
        self._selector = None
        self._current_color = QColor(255, 0, 0)
        self._backup_path: Path | None = None
        self._active_tool: str | None = None
        self._hotkey_manager = HotkeyManager(self)
        self._setup_ui()
        self._setup_shortcuts()
        self._start_hotkeys()

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

        self._btn_full = QPushButton("全画面 [F1]")
        self._btn_region = QPushButton("範囲選択 [F2]")
        self._btn_window = QPushButton("ウィンドウ [F3]")
        self._btn_copy = QPushButton("コピー [Ctrl+C]")
        self._btn_quicksave = QPushButton("即時保存 [Ctrl+Shift+S]")
        self._btn_save = QPushButton("保存... [Ctrl+S]")
        self._btn_folder = QPushButton("保存先...")
        self._btn_save_options = QPushButton("保存設定...")
        self._btn_hotkey_settings = QPushButton("ホットキー設定...")
        # プロファイル
        lbl_profile = QLabel("プロファイル:")
        self._combo_profile = QComboBox()
        self._combo_profile.setMinimumWidth(110)
        self._btn_profile_mgr = QPushButton("管理...")
        self._lbl_folder = QLabel()
        self._lbl_folder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._update_folder_label()

        for btn in (self._btn_copy, self._btn_quicksave, self._btn_save):
            btn.setEnabled(False)

        # 遅延設定
        lbl_delay = QLabel("遅延:")
        self._spin_delay = QSpinBox()
        self._spin_delay.setRange(0, 30)
        self._spin_delay.setValue(0)
        self._spin_delay.setSuffix(" 秒")
        self._spin_delay.setFixedWidth(72)

        self._btn_full.clicked.connect(self._on_capture_full)
        self._btn_region.clicked.connect(self._on_capture_region)
        self._btn_window.clicked.connect(self._on_capture_window)
        self._btn_copy.clicked.connect(self._on_copy_clipboard)
        self._btn_save.clicked.connect(self._on_save)
        self._btn_quicksave.clicked.connect(self._on_quicksave)
        self._btn_folder.clicked.connect(self._on_choose_folder)
        self._btn_save_options.clicked.connect(self._on_save_options)
        self._btn_hotkey_settings.clicked.connect(self._on_hotkey_settings)
        self._combo_profile.currentTextChanged.connect(self._on_profile_changed)
        self._btn_profile_mgr.clicked.connect(self._on_profile_manage)
        self._refresh_profile_combo()

        for w in (self._btn_full, self._btn_region, self._btn_window,
                  lbl_delay, self._spin_delay,
                  self._btn_copy,
                  self._btn_folder, self._lbl_folder,
                  self._btn_quicksave, self._btn_save, self._btn_save_options,
                  self._btn_hotkey_settings,
                  lbl_profile, self._combo_profile, self._btn_profile_mgr):
            cb_layout.addWidget(w)

        # ========== 編集ツールバー ==========
        edit_bar = QWidget()
        eb_layout = QHBoxLayout(edit_bar)
        eb_layout.setContentsMargins(8, 4, 8, 4)
        eb_layout.setSpacing(6)

        # ツール選択ボタン（トグル）
        self._btn_tool_none = QPushButton("選択解除")
        self._btn_tool_select = QPushButton("▶ 選択")
        self._btn_tool_rect = QPushButton("■ 矩形")
        self._btn_tool_filled_rect = QPushButton("█ 四角形")
        self._btn_tool_text = QPushButton("T テキスト")
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
        self._btn_color.setIcon(_color_icon(self._current_color))
        self._btn_color.clicked.connect(self._on_pick_color)

        # 線幅
        lbl_width = QLabel("線幅:")
        self._spin_width = QSpinBox()
        self._spin_width.setRange(1, 20)
        self._spin_width.setValue(2)
        self._spin_width.setSuffix(" px")
        self._spin_width.valueChanged.connect(self._on_line_width_changed)

        # Undo
        self._btn_undo = QPushButton("↩ Undo [Ctrl+Z]")
        self._btn_undo.setEnabled(False)
        self._btn_undo.clicked.connect(self._on_undo)

        # セパレータ
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)

        for w in (self._btn_tool_none, self._btn_tool_select, self._btn_tool_rect, self._btn_tool_filled_rect, self._btn_tool_text,
                  sep, self._btn_color, lbl_width, self._spin_width,
                  sep, self._btn_undo):
            eb_layout.addWidget(w)
        eb_layout.addStretch()

        # 初期ツール選択
        self._btn_tool_none.setChecked(True)

        # ========== キャンバス ==========
        self._canvas = EditorCanvas()
        self._canvas.set_tool(None)

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
        self._status.showMessage("F1: 全画面  F2: 範囲選択")

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("F1"), self, self._on_capture_full)
        QShortcut(QKeySequence("F2"), self, self._on_capture_region)
        QShortcut(QKeySequence("F3"), self, self._on_capture_window)
        QShortcut(QKeySequence("Ctrl+C"), self, self._on_copy_clipboard)
        QShortcut(QKeySequence("Ctrl+S"), self, self._on_save)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, self._on_quicksave)
        QShortcut(QKeySequence("Ctrl+Z"), self, self._on_undo)
        QShortcut(QKeySequence("Delete"), self, self._on_delete_selected)

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
            self._countdown_action()

    def _on_capture_full(self):
        self.showMinimized()
        self._start_countdown(self._do_capture_full)

    def _do_capture_full(self):
        pixmap = capture.capture_fullscreen()
        self.showNormal()
        self.activateWindow()
        self._set_pixmap(pixmap)

    def _on_capture_region(self):
        self.showMinimized()
        self._start_countdown(self._do_start_region)

    def _on_capture_window(self):
        self.showMinimized()
        self._start_countdown(self._do_start_window)

    def _do_start_window(self):
        self._selector = start_window_capture(self._on_region_captured)

    def _do_start_region(self):
        self._selector = capture.start_region_capture(self._on_region_captured)

    def _on_region_captured(self, pixmap: QPixmap):
        self.showNormal()
        self.activateWindow()
        self._set_pixmap(pixmap)

    def _set_pixmap(self, pixmap: QPixmap):
        self._canvas.set_pixmap(pixmap)
        for btn in (self._btn_copy, self._btn_save, self._btn_quicksave):
            btn.setEnabled(True)
        self._btn_undo.setEnabled(False)
        if self._config.get("auto_backup_enabled", True):
            self._auto_backup(pixmap)
        self._status.showMessage(
            f"キャプチャ完了: {pixmap.width()} x {pixmap.height()} px  |  "
            "編集ツールを選択して注釈を追加できます"
        )

    def _auto_backup(self, pixmap: QPixmap):
        """無編集の元画像をバックアップフォルダへ自動保存する。"""
        backup_dir = Path(self._config["save_folder"]) / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        path = self._make_filename(backup_dir, "png")
        pixmap.save(str(path), "PNG")
        self._backup_path = path

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
            self._btn_color.setIcon(_color_icon(color))
            self._canvas.set_color(color)

    def _on_delete_selected(self):
        if self._active_tool == "select":
            self._canvas.delete_selected()
            self._status.showMessage("オブジェクトを削除しました")

    def _on_line_width_changed(self, value: int):
        self._canvas.set_line_width(value)

    def _on_undo(self):
        if self._canvas.undo():
            self._status.showMessage("Undo しました")

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

    def _make_filename(self, folder: Path, ext: str = "png") -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = folder / f"screenshot_{ts}.{ext}"
        if path.exists():
            for i in range(2, 100):
                path = folder / f"screenshot_{ts}_{i}.{ext}"
                if not path.exists():
                    break
        return path

    def _on_quicksave(self):
        pixmap = self._canvas.get_pixmap()
        if pixmap is None:
            return
        pixmap = self._apply_save_effects(pixmap)
        folder = Path(self._config["save_folder"])
        folder.mkdir(parents=True, exist_ok=True)
        path = self._make_filename(folder, "png")
        if pixmap.save(str(path), "PNG"):
            self._status.showMessage(f"保存しました: {path}")
            if self._config.get("open_folder_after_save", False):
                os.startfile(str(folder))
        else:
            QMessageBox.critical(self, "エラー", f"保存に失敗しました:\n{path}")

    def _on_save(self):
        pixmap = self._canvas.get_pixmap()
        if pixmap is None:
            return
        pixmap = self._apply_save_effects(pixmap)
        folder = Path(self._config["save_folder"])
        folder.mkdir(parents=True, exist_ok=True)
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
            self._status.showMessage(f"保存しました: {path}")
            if self._config.get("open_folder_after_save", False):
                os.startfile(str(Path(path).parent))
        else:
            QMessageBox.critical(self, "エラー", f"保存に失敗しました:\n{path}")

    def _apply_save_effects(self, pixmap: QPixmap) -> QPixmap:
        """保存時自動エフェクトを適用した画像を返す。設定が無効なら元画像をそのまま返す。"""
        if not self._config.get("auto_border_enabled", False):
            return pixmap

        result = pixmap.copy()
        from PySide6.QtGui import QPainter, QPen, QColor
        from PySide6.QtCore import Qt
        painter = QPainter(result)
        w = self._config.get("auto_border_width", 4)
        color = QColor(self._config.get("auto_border_color", "#ff0000"))
        pen = QPen(color, w)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # 線幅の半分が内側にはみ出すので offset で調整
        offset = w // 2
        painter.drawRect(offset, offset,
                         result.width() - w, result.height() - w)
        painter.end()
        return result

    def _on_save_options(self):
        dlg = SaveOptionsDialog(self._config, self)
        if dlg.exec():
            updated = dlg.get_config()
            self._config.update(updated)
            settings.save(self._root)
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
        self._backup_path.rename(new_backup)
        self._backup_path = new_backup

    # ------------------------------------------------------------------
    # 保存先フォルダ
    # ------------------------------------------------------------------

    def _refresh_profile_combo(self):
        self._combo_profile.blockSignals(True)
        self._combo_profile.clear()
        for name in settings.profile_names(self._root):
            self._combo_profile.addItem(name)
        self._combo_profile.setCurrentText(self._root["active_profile"])
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
        if profile_name not in self._root["profiles"]:
            return
        prof = self._root["profiles"][profile_name]
        self._pending_capture_profile = prof

        self.showMinimized()
        if action == "full":
            QTimer.singleShot(300, self._do_capture_full_with_profile)
        elif action == "region":
            QTimer.singleShot(300, lambda: capture.start_region_capture(self._on_region_captured_with_profile))
        elif action == "window":
            QTimer.singleShot(300, lambda: start_window_capture(self._on_region_captured_with_profile))

    def _do_capture_full_with_profile(self):
        pixmap = capture.capture_fullscreen()
        self.showNormal()
        self.activateWindow()
        self._quicksave_with_profile(pixmap, self._pending_capture_profile)

    def _on_region_captured_with_profile(self, pixmap: QPixmap):
        self.showNormal()
        self.activateWindow()
        self._quicksave_with_profile(pixmap, self._pending_capture_profile)

    def _quicksave_with_profile(self, pixmap: QPixmap, prof: dict):
        """指定プロファイルの設定で即時保存する。"""
        # バックアップ
        if prof.get("auto_backup_enabled", True):
            backup_dir = Path(prof["save_folder"]) / "backup"
            backup_dir.mkdir(parents=True, exist_ok=True)
            pixmap.save(str(self._make_filename(backup_dir, "png")), "PNG")

        # エフェクト適用
        result = pixmap
        if prof.get("auto_border_enabled", False):
            from PySide6.QtGui import QPainter, QPen
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

        folder = Path(prof["save_folder"])
        folder.mkdir(parents=True, exist_ok=True)
        path = self._make_filename(folder, "png")
        if result.save(str(path), "PNG"):
            self._status.showMessage(f"保存しました [{prof.get('save_folder', '')}]: {path.name}")
            if prof.get("open_folder_after_save", False):
                os.startfile(str(folder))
        else:
            self._status.showMessage(f"保存に失敗しました: {path}")

    def _on_hotkey_settings(self):
        profile_names = settings.profile_names(self._root)
        dlg = HotkeyDialog(self._root.get("hotkey_slots", []), profile_names, self)
        if dlg.exec():
            self._root["hotkey_slots"] = dlg.get_slots()
            settings.save(self._root)
            self._hotkey_manager.update(self._root["hotkey_slots"])
            self._status.showMessage("ホットキー設定を更新しました")

    def _on_profile_changed(self, name: str):
        if name and name in self._root["profiles"]:
            settings.set_active(self._root, name)
            settings.save(self._root)
            self._update_folder_label()
            self._hotkey_manager.update(self._root.get("hotkey_slots", []))
            self._status.showMessage(f"プロファイル切り替え: {name}")

    def _on_profile_manage(self):
        dlg = ProfileDialog(self._root, self)
        if dlg.exec():
            self._root = dlg.get_root()
            settings.save(self._root)
            self._refresh_profile_combo()
            self._hotkey_manager.update(self._root.get("hotkey_slots", []))
            self._status.showMessage("プロファイルを更新しました")

    def _on_choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "保存先フォルダを選択", self._config["save_folder"],
        )
        if folder:
            self._config["save_folder"] = folder
            settings.save(self._root)
            self._update_folder_label()
            self._status.showMessage(f"保存先: {folder}")

    def _update_folder_label(self):
        folder = self._config["save_folder"]
        max_len = 45
        display = folder if len(folder) <= max_len else "..." + folder[-(max_len - 3):]
        self._lbl_folder.setText(display)
        self._lbl_folder.setToolTip(folder)
