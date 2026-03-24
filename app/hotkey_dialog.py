"""ホットキー設定ダイアログ。"""
import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QFrame,
    QDialogButtonBox, QGroupBox, QMessageBox,
)

from app.hotkeys import ACTIONS
from app.settings import DEFAULT_HOTKEY_SLOTS

ACTIVE_LABEL = "アクティブ"
ACTIVE_VALUE = "__active__"


class KeyCaptureButton(QPushButton):
    """クリックするとキー入力待ち状態になり、押されたキーを記録するボタン。"""

    def __init__(self, combo: str = "", parent=None):
        super().__init__(parent)
        self._combo = combo
        self._waiting = False
        self._update_text()
        self.clicked.connect(self._start_capture)
        self.setMinimumWidth(130)

    def combo(self) -> str:
        return self._combo

    def set_combo(self, combo: str):
        self._combo = combo
        self._update_text()

    def _update_text(self):
        if self._waiting:
            self.setText("キーを押してください...")
            self.setStyleSheet("background-color: #fff3cd; border: 2px solid #ffc107;")
        else:
            self.setText(self._combo if self._combo else "（なし）")
            self.setStyleSheet("")

    def _start_capture(self):
        self._waiting = True
        self._update_text()
        self.setFocus()

    def keyPressEvent(self, event):
        if not self._waiting:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._waiting = False
            self._update_text()
            return
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift,
                   Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_unknown):
            return
        mods = event.modifiers()
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        key_text = QKeySequence(key).toString().lower()
        if key_text:
            parts.append(key_text)
        self._combo = "+".join(parts) if parts else ""
        self._waiting = False
        self._update_text()
        event.accept()

    def focusOutEvent(self, event):
        if self._waiting:
            self._waiting = False
            self._update_text()
        super().focusOutEvent(event)


class HotkeyDialog(QDialog):
    """
    レイアウト:
      アクション | [プロファイル1 ▼] | [プロファイル2 ▼]
      ───────────────────────────────────────────
      全画面     | [ホットキー] [✕]  | [ホットキー] [✕]
      範囲選択   | [ホットキー] [✕]  | [ホットキー] [✕]
      ...
    """

    def __init__(self, slots: list, profile_names: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("ホットキー設定")
        self._profile_names = profile_names
        self._setup_ui(slots)

    @staticmethod
    def _build_slot_map(slots: list[dict]) -> dict[str, list[dict]]:
        """スロットリストを action → [slot, slot] の辞書に整理する（列数は常に 2）。"""
        slot_map: dict[str, list[dict]] = {a: [] for a in ACTIONS}
        for s in slots:
            action = s.get("action", "")
            if action in slot_map and len(slot_map[action]) < 2:
                slot_map[action].append(s)
        for action in ACTIONS:
            while len(slot_map[action]) < 2:
                slot_map[action].append({"action": action, "combo": "none", "profile": "__active__"})
        return slot_map

    def _setup_ui(self, slots: list[dict]) -> None:
        # スロットを action × slot_idx に整理
        slot_map = self._build_slot_map(slots)

        # 列ごとのプロファイルを全アクションのコンセンサスで初期化
        # 列内の全アクションが同じプロファイルを持つ場合のみそれを使い、
        # 不一致の場合は "__active__" にフォールバック
        col_profiles = []
        for col_idx in range(2):
            profiles_in_col = [slot_map[action][col_idx].get("profile", "__active__")
                               for action in ACTIONS]
            unique = set(profiles_in_col)
            col_profiles.append(profiles_in_col[0] if len(unique) == 1 else "__active__")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        group = QGroupBox("グローバルホットキー（アプリが背面でも動作します）")
        grid = QGridLayout(group)
        grid.setSpacing(8)
        grid.setContentsMargins(12, 16, 12, 12)
        grid.setColumnMinimumWidth(0, 90)

        # ── ヘッダー行: プロファイルドロップダウン ──
        grid.addWidget(QLabel("アクション"), 0, 0)

        self._col_profile_combos: list[QComboBox] = []
        for col_idx in range(2):
            cb = QComboBox()
            cb.addItem(ACTIVE_LABEL, ACTIVE_VALUE)
            for name in self._profile_names:
                cb.addItem(name, name)
            idx = cb.findData(col_profiles[col_idx])
            cb.setCurrentIndex(idx if idx >= 0 else 0)
            cb.setMinimumWidth(110)
            # col 1,2 に配置（クリアボタン分を考慮してspan）
            grid.addWidget(cb, 0, 1 + col_idx * 3, 1, 2)
            self._col_profile_combos.append(cb)

        # 区切り線
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        grid.addWidget(sep, 1, 0, 1, 7)

        # ── アクション行 ──
        self._action_btns: dict[str, list[KeyCaptureButton]] = {}

        for row_idx, (action, label) in enumerate(ACTIONS.items()):
            grid_row = row_idx + 2
            grid.addWidget(QLabel(label), grid_row, 0)

            btns: list[KeyCaptureButton] = []
            for col_idx, slot in enumerate(slot_map[action]):
                combo_val = slot.get("combo", "none")
                btn = KeyCaptureButton("" if combo_val == "none" else combo_val)
                col_base = 1 + col_idx * 3
                grid.addWidget(btn, grid_row, col_base)

                btn_clr = QPushButton("✕")
                btn_clr.setFixedWidth(28)
                btn_clr.setToolTip("クリア")
                btn_clr.clicked.connect(lambda _, b=btn: b.set_combo(""))
                grid.addWidget(btn_clr, grid_row, col_base + 1)

                # 列間の縦区切り
                if col_idx == 0:
                    vsep = QFrame()
                    vsep.setFrameShape(QFrame.Shape.VLine)
                    vsep.setFrameShadow(QFrame.Shadow.Sunken)
                    grid.addWidget(vsep, grid_row, col_base + 2)

                btns.append(btn)
            self._action_btns[action] = btns

        layout.addWidget(group)

        # フッター
        footer = QHBoxLayout()
        btn_reset = QPushButton("デフォルトに戻す")
        btn_reset.clicked.connect(self._reset_defaults)
        footer.addWidget(btn_reset)
        footer.addStretch()
        layout.addLayout(footer)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _reset_defaults(self) -> None:
        slot_map = self._build_slot_map(copy.deepcopy(DEFAULT_HOTKEY_SLOTS))
        for action, btns in self._action_btns.items():
            for col_idx, btn in enumerate(btns):
                slots = slot_map.get(action, [])
                combo = slots[col_idx].get("combo", "none") if col_idx < len(slots) else "none"
                btn.set_combo("" if combo == "none" else combo)
        for cb in self._col_profile_combos:
            cb.setCurrentIndex(0)

    def accept(self) -> None:
        """OK前に重複キーがないか検証する。"""
        seen: set[str] = set()
        dups: set[str] = set()
        for btns in self._action_btns.values():
            for btn in btns:
                combo = btn.combo()
                if combo:
                    if combo in seen:
                        dups.add(combo)
                    else:
                        seen.add(combo)
        if dups:
            QMessageBox.warning(
                self, "重複するキーがあります",
                "以下のキーが複数のアクションに割り当てられています:\n"
                + "\n".join(f"  {d}" for d in sorted(dups))
                + "\n\n重複を解消してから保存してください。",
            )
            return
        super().accept()

    def get_slots(self) -> list[dict]:
        """設定されたスロットリストを返す。"""
        result = []
        for action in ACTIONS:
            btns = self._action_btns[action]
            for col_idx, btn in enumerate(btns):
                combo = btn.combo() if btn.combo() else "none"
                profile = self._col_profile_combos[col_idx].currentData()
                result.append({"action": action, "combo": combo, "profile": profile})
        return result
