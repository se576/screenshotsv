"""プロファイル管理ダイアログ。"""
import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QLineEdit, QCheckBox, QDoubleSpinBox, QFileDialog,
    QDialogButtonBox, QColorDialog, QMessageBox, QInputDialog,
    QSplitter, QWidget,
)

from app import settings as S
from app.ui_utils import color_icon as _color_icon


class ProfileDialog(QDialog):
    """プロファイルの一覧・作成・削除・編集を行うダイアログ。"""

    def __init__(self, root: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("プロファイル管理")
        self.setMinimumSize(560, 400)
        # 編集用にディープコピー（Cancel で破棄できるように）
        self._root = copy.deepcopy(root)
        # 構成変更（追加・削除・名前変更）の検出用スナップショット
        self._initial_names = list(self._root.get("profiles", {}).keys())
        # 選択中プロファイルの保存先フルパスの真値（表示ラベルの切り詰めに依存しない）
        self._folder = ""
        self._setup_ui()
        self._refresh_list()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setSpacing(8)

        # ========== 左: プロファイル一覧 ==========
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left.setFixedWidth(170)

        left_layout.addWidget(QLabel("プロファイル"))
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_profile_selected)
        left_layout.addWidget(self._list)

        btn_layout = QHBoxLayout()
        self._btn_add = QPushButton("追加")
        self._btn_rename = QPushButton("名前変更")
        self._btn_del = QPushButton("削除")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_rename.clicked.connect(self._on_rename)
        self._btn_del.clicked.connect(self._on_delete)
        for b in (self._btn_add, self._btn_rename, self._btn_del):
            btn_layout.addWidget(b)
        left_layout.addLayout(btn_layout)

        # ========== 右: 設定編集 ==========
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # 保存先
        folder_group = QGroupBox("保存先フォルダ")
        fg_layout = QHBoxLayout(folder_group)
        self._lbl_folder = QLabel()
        self._lbl_folder.setWordWrap(False)
        self._btn_browse = QPushButton("参照...")
        self._btn_browse.setFixedWidth(64)
        self._btn_browse.clicked.connect(self._on_browse_folder)
        fg_layout.addWidget(self._lbl_folder, stretch=1)
        fg_layout.addWidget(self._btn_browse)

        # バックアップ
        backup_group = QGroupBox("バックアップ")
        bg_layout = QVBoxLayout(backup_group)
        self._chk_backup = QCheckBox("キャプチャ時に無編集の原本を自動保存する")
        self._chk_backup.toggled.connect(self._save_ui_to_profile)
        bg_layout.addWidget(self._chk_backup)
        self._chk_open_folder = QCheckBox("保存後にフォルダを開く")
        self._chk_open_folder.toggled.connect(self._save_ui_to_profile)
        bg_layout.addWidget(self._chk_open_folder)

        # 自動エフェクト
        effect_group = QGroupBox("保存時の自動エフェクト")
        eg_layout = QVBoxLayout(effect_group)
        self._chk_border = QCheckBox("外枠を追加する")
        self._chk_border.toggled.connect(self._on_effect_changed)
        eg_layout.addWidget(self._chk_border)

        detail = QHBoxLayout()
        self._btn_border_color = QPushButton("  色")
        self._btn_border_color.setFixedSize(64, 26)
        self._btn_border_color.clicked.connect(self._on_pick_border_color)
        lbl_w = QLabel("幅:")
        self._spin_border_width = QDoubleSpinBox()
        self._spin_border_width.setRange(0.01, 100.0)
        self._spin_border_width.setDecimals(2)
        self._spin_border_width.setSingleStep(0.01)
        self._spin_border_width.setSuffix(" px")
        self._spin_border_width.valueChanged.connect(self._on_effect_changed)
        detail.addWidget(QLabel("色:"))
        detail.addWidget(self._btn_border_color)
        detail.addSpacing(12)
        detail.addWidget(lbl_w)
        detail.addWidget(self._spin_border_width)
        detail.addStretch()
        eg_layout.addLayout(detail)

        right_layout.addWidget(folder_group)
        right_layout.addWidget(backup_group)
        right_layout.addWidget(effect_group)
        right_layout.addStretch()

        # ========== OK / Cancel ==========
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        outer = QVBoxLayout()
        splitter_layout = QHBoxLayout()
        splitter_layout.addWidget(left)
        splitter_layout.addWidget(right, stretch=1)
        outer.addLayout(splitter_layout)
        outer.addWidget(buttons)

        # メインlayoutにouterを詰める
        layout.addLayout(outer)

        self._editing = False  # UIからの変更でプロファイルに書き戻す制御

    # ------------------------------------------------------------------
    # リスト操作
    # ------------------------------------------------------------------

    def _refresh_list(self, select_name: str | None = None):
        self._list.blockSignals(True)
        self._list.clear()
        names = S.profile_names(self._root)
        for name in names:
            item = QListWidgetItem(name)
            self._list.addItem(item)
        self._list.blockSignals(False)

        # 選択
        target = select_name or self._root["active_profile"]
        for i, name in enumerate(names):
            if name == target:
                self._list.setCurrentRow(i)
                break

    def _current_name(self) -> str | None:
        item = self._list.currentItem()
        return item.text() if item else None

    def _on_profile_selected(self, row: int):
        names = S.profile_names(self._root)
        if 0 <= row < len(names):
            self._load_profile_to_ui(names[row])
        can_del = len(names) > 1
        self._btn_del.setEnabled(can_del)
        self._btn_rename.setEnabled(row >= 0)

    @staticmethod
    def _truncate_path(path: str, max_len: int = 40) -> str:
        return path if len(path) <= max_len else "..." + path[-(max_len - 3):]

    def _set_folder_display(self, folder: str) -> None:
        """保存先フルパスを真値として保持しつつ、ラベルに切り詰め表示する。"""
        self._folder = folder
        self._lbl_folder.setText(self._truncate_path(folder))
        self._lbl_folder.setToolTip(folder)

    def _load_profile_to_ui(self, name: str) -> None:
        if name not in self._root.get("profiles", {}):
            return
        prof = self._root["profiles"][name]
        d = S.PROFILE_DEFAULTS
        self._editing = True
        self._set_folder_display(prof.get("save_folder", d["save_folder"]))

        self._chk_backup.setChecked(prof.get("auto_backup_enabled", d["auto_backup_enabled"]))
        self._chk_open_folder.setChecked(prof.get("open_folder_after_save", d["open_folder_after_save"]))
        self._chk_border.setChecked(prof.get("auto_border_enabled", d["auto_border_enabled"]))
        color = QColor(prof.get("auto_border_color", d["auto_border_color"]))
        self._btn_border_color.setIcon(_color_icon(color))
        self._btn_border_color.setProperty("color", color.name())
        self._spin_border_width.setValue(float(prof.get("auto_border_width", d["auto_border_width"])))
        self._editing = False
        self._update_effect_enabled()

    def _save_ui_to_profile(self):
        name = self._current_name()
        if name is None or self._editing:
            return
        prof = self._root["profiles"][name]
        prof["save_folder"] = self._folder
        prof["auto_backup_enabled"] = self._chk_backup.isChecked()
        prof["open_folder_after_save"] = self._chk_open_folder.isChecked()
        prof["auto_border_enabled"] = self._chk_border.isChecked()
        prof["auto_border_color"] = (
            self._btn_border_color.property("color") or S.PROFILE_DEFAULTS["auto_border_color"])
        prof["auto_border_width"] = self._spin_border_width.value()

    def _update_effect_enabled(self):
        enabled = self._chk_border.isChecked()
        self._btn_border_color.setEnabled(enabled)
        self._spin_border_width.setEnabled(enabled)

    # ------------------------------------------------------------------
    # プロファイル CRUD
    # ------------------------------------------------------------------

    def _on_add(self):
        name, ok = QInputDialog.getText(self, "プロファイル追加", "プロファイル名:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if not S.add_profile(self._root, name):
            QMessageBox.warning(self, "エラー", f"「{name}」は既に存在します。")
            return
        # 新規作成したプロファイルへ自動で切り替える（OK 確定時にメインウィンドウへ反映）
        S.set_active(self._root, name)
        self._refresh_list(select_name=name)

    def _on_rename(self):
        old = self._current_name()
        if old is None:
            return
        new, ok = QInputDialog.getText(self, "名前変更", "新しい名前:", text=old)
        if not ok or not new.strip():
            return
        self._save_ui_to_profile()
        if not S.rename_profile(self._root, old, new.strip()):
            QMessageBox.warning(self, "エラー", f"「{new.strip()}」は既に存在します。")
            return
        self._refresh_list(select_name=new.strip())

    def _on_delete(self):
        name = self._current_name()
        if name is None:
            return
        if QMessageBox.question(
            self, "削除確認", f"「{name}」を削除しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        S.delete_profile(self._root, name)
        self._refresh_list()

    # ------------------------------------------------------------------
    # 設定編集
    # ------------------------------------------------------------------

    def _on_browse_folder(self):
        current = self._folder or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "保存先フォルダを選択", current)
        if folder:
            self._set_folder_display(folder)
            self._save_ui_to_profile()

    def _on_effect_changed(self):
        self._update_effect_enabled()
        self._save_ui_to_profile()

    def _on_pick_border_color(self):
        current = QColor(
            self._btn_border_color.property("color") or S.PROFILE_DEFAULTS["auto_border_color"])
        color = QColorDialog.getColor(current, self, "外枠の色を選択")
        if color.isValid():
            self._btn_border_color.setIcon(_color_icon(color))
            self._btn_border_color.setProperty("color", color.name())
            self._save_ui_to_profile()

    # ------------------------------------------------------------------
    # 結果取得
    # ------------------------------------------------------------------

    def accept(self):
        self._save_ui_to_profile()
        super().accept()

    def reject(self):
        """キャンセル・✕・Esc の共通経路。
        削除・追加・名前変更は個別に確認済みのため、無言で破棄せず適用するか確認する。"""
        if list(self._root.get("profiles", {}).keys()) != self._initial_names:
            choice = self._ask_unapplied_changes()
            if choice == "apply":
                self.accept()
                return
            if choice == "cancel":
                return  # ダイアログに戻る
        super().reject()

    def _ask_unapplied_changes(self) -> str:
        """未適用の構成変更をどうするか確認する。"apply" / "discard" / "cancel" を返す。"""
        box = QMessageBox(self)
        box.setWindowTitle("プロファイル管理")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("プロファイルの追加・削除・名前変更がまだ適用されていません。")
        box.setInformativeText("適用せずに閉じると、これらの変更は失われます。")
        btn_apply = box.addButton("適用して閉じる", QMessageBox.ButtonRole.AcceptRole)
        btn_discard = box.addButton("破棄して閉じる", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(btn_apply)
        box.exec()
        # 親付きで手動生成したダイアログは明示的に破棄する
        box.deleteLater()
        clicked = box.clickedButton()
        if clicked is btn_apply:
            return "apply"
        if clicked is btn_discard:
            return "discard"
        return "cancel"

    def get_root(self) -> dict:
        return self._root
