"""
グローバルホットキー管理。
keyboard ライブラリのフックは別スレッドで動くため、
コールバックでは Signal.emit() のみ実行して Qt UI スレッドに転送する。
"""
import keyboard
import logging
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

# アクションキーとラベル
ACTIONS = {
    "full":   "全画面キャプチャ",
    "region": "範囲選択キャプチャ",
    "window": "ウィンドウキャプチャ",
    "save":   "即時保存",
}


class HotkeyManager(QObject):
    sig_full            = Signal()
    sig_region          = Signal()
    sig_window          = Signal()
    sig_save            = Signal()
    sig_capture_profile = Signal(str, str)  # (action, profile_name)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._registered: list[str] = []

    def start(self, slots: list) -> None:
        """スロットリストからホットキーを登録する。"""
        self.stop()
        sig_map = {
            "full":   self.sig_full,
            "region": self.sig_region,
            "window": self.sig_window,
            "save":   self.sig_save,
        }
        for slot in slots:
            combo = slot.get("combo", "none")
            if not combo or combo == "none":
                continue
            action = slot.get("action", "")
            profile = slot.get("profile", "__active__")
            try:
                if profile == "__active__":
                    sig = sig_map.get(action)
                    if sig:
                        keyboard.add_hotkey(combo, sig.emit, suppress=False)
                else:
                    keyboard.add_hotkey(
                        combo,
                        lambda a=action, p=profile: self.sig_capture_profile.emit(a, p),
                        suppress=False,
                    )
                self._registered.append(combo)
            except Exception as e:
                logger.warning("ホットキー登録失敗: combo=%r action=%r: %s", combo, action, e)

    def stop(self) -> None:
        """登録済みのホットキーをすべて解除する。"""
        for combo in self._registered:
            try:
                keyboard.remove_hotkey(combo)
            except Exception as e:
                logger.warning("ホットキー解除失敗: combo=%r: %s", combo, e)
        self._registered.clear()

    def update(self, slots: list) -> None:
        """スロット変更時に再登録する。"""
        self.start(slots)
