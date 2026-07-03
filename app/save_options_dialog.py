"""保存時自動エフェクトの設定ダイアログ。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
    QCheckBox, QLabel, QDoubleSpinBox, QPushButton,
    QDialogButtonBox, QColorDialog,
)

from app.ui_utils import color_icon as _color_icon


class SaveOptionsDialog(QDialog):
    """保存時に自動適用するエフェクトを設定するダイアログ。"""

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("保存時の自動エフェクト設定")
        self.setMinimumWidth(340)
        self._config = dict(config)  # コピーして編集、OKで返す
        self._border_color = QColor(config.get("auto_border_color", "#ff0000"))
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ========== 外枠グループ ==========
        group = QGroupBox("外枠")
        g_layout = QVBoxLayout(group)

        self._chk_border = QCheckBox("保存時に外枠を追加する")
        self._chk_border.setChecked(self._config.get("auto_border_enabled", False))
        self._chk_border.toggled.connect(self._update_enabled)
        g_layout.addWidget(self._chk_border)

        # 色・幅の行
        detail = QHBoxLayout()

        lbl_color = QLabel("色:")
        self._btn_color = QPushButton()
        self._btn_color.setFixedSize(60, 26)
        self._btn_color.setIcon(_color_icon(self._border_color))
        self._btn_color.setToolTip(self._border_color.name())
        self._btn_color.clicked.connect(self._pick_color)

        lbl_width = QLabel("幅:")
        self._spin_width = QDoubleSpinBox()
        self._spin_width.setRange(0.01, 100.0)
        self._spin_width.setDecimals(2)
        self._spin_width.setSingleStep(0.01)
        self._spin_width.setValue(float(self._config.get("auto_border_width", 4)))
        self._spin_width.setSuffix(" px")

        detail.addWidget(lbl_color)
        detail.addWidget(self._btn_color)
        detail.addSpacing(16)
        detail.addWidget(lbl_width)
        detail.addWidget(self._spin_width)
        detail.addStretch()
        g_layout.addLayout(detail)

        layout.addWidget(group)

        # ========== OK / Cancel ==========
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_enabled(self._chk_border.isChecked())

    def _update_enabled(self, enabled: bool):
        self._btn_color.setEnabled(enabled)
        self._spin_width.setEnabled(enabled)

    def _pick_color(self):
        color = QColorDialog.getColor(self._border_color, self, "外枠の色を選択")
        if color.isValid():
            self._border_color = color
            self._btn_color.setIcon(_color_icon(color))
            self._btn_color.setToolTip(color.name())

    def accept(self) -> None:
        """OK 時に設定を確定してから閉じる。"""
        self._config["auto_border_enabled"] = self._chk_border.isChecked()
        self._config["auto_border_color"] = self._border_color.name()
        self._config["auto_border_width"] = self._spin_width.value()
        super().accept()

    def get_config(self) -> dict:
        """accept() 後に確定済み設定を返す。"""
        return self._config
