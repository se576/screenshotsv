"""ホットキー設定ダイアログ。"""
import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QFrame,
    QDialogButtonBox, QGroupBox,
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
        self.setText(self._combo if self._combo else "（なし）")
        self.setStyleSheet("" if not self._waiting else
                           "background-color: #fff3cd; border: 2px solid #ffc107;")

    def _start_capture(self):
        self._waiting = True
        self.setText("キーを押してください...")
        self.setStyleSheet("background-color: #fff3cd; border: 2px solid #ffc107;")
        self.setFocus()

    def keyPressEvent(self, event):
        if not self._waiting:
            super().keyPressEvent(event)
            return
        key = event.key()
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

    def _setup_ui(self, slots: list):
        # スロットを action × slot_idx に整理
        slot_map: dict[str, list[dict]] = {a: [] for a in ACTIONS}
        for s in slots:
            action = s.get("action", "")
            if action in slot_map and len(slot_map[action]) < 2:
                slot_map[action].append(s)
        for action in ACTIONS:
            while len(slot_map[action]) < 2:
                slot_map[action].append({"action": action, "combo": "none", "profile": "__active__"})

        # 列ごとのプロファイルを先頭アクションのスロットから初期値取得
        col_profiles = [
            slot_map[next(iter(ACTIONS))][0].get("profile", "__active__"),
            slot_map[next(iter(ACTIONS))][1].get("profile", "__active__"),
        ]

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

    def _reset_defaults(self):
        defaults = copy.deepcopy(DEFAULT_HOTKEY_SLOTS)
        slot_map: dict[str, list[dict]] = {a: [] for a in ACTIONS}
        for s in defaults:
            action = s.get("action", "")
            if action in slot_map and len(slot_map[action]) < 2:
                slot_map[action].append(s)
        for action, btns in self._action_btns.items():
            for col_idx, btn in enumerate(btns):
                slots = slot_map.get(action, [])
                combo = slots[col_idx].get("combo", "none") if col_idx < len(slots) else "none"
                btn.set_combo("" if combo == "none" else combo)
        for cb in self._col_profile_combos:
            cb.setCurrentIndex(0)

    def get_slots(self) -> list:
        """設定されたスロットリストを返す。"""
        result = []
        for action in ACTIONS:
            btns = self._action_btns[action]
            for col_idx, btn in enumerate(btns):
                combo = btn.combo() if btn.combo() else "none"
                profile = self._col_profile_combos[col_idx].currentData()
                result.append({"action": action, "combo": combo, "profile": profile})
        return result
