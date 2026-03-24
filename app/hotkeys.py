"""
グローバルホットキー管理。
keyboard ライブラリのフックは別スレッドで動くため、
コールバックでは queue に積むだけにして Qt メインスレッドで処理する。
（PySide6 では非 QThread から Signal.emit() を呼ぶとキューイングが不安定になるため）
"""
import queue
import keyboard
import logging
from PySide6.QtCore import QObject, Signal, QTimer

logger = logging.getLogger(__name__)

# アクションキーとラベル
ACTIONS = {
    "full":   "全画面キャプチャ",
    "region": "範囲選択キャプチャ",
    "window": "ウィンドウキャプチャ",
    "save":   "即時保存",
}

_POLL_INTERVAL_MS = 30  # メインスレッドでキューを処理する間隔


class HotkeyManager(QObject):
    sig_full            = Signal()
    sig_region          = Signal()
    sig_window          = Signal()
    sig_save            = Signal()
    sig_capture_profile = Signal(str, str)  # (action, profile_name)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._handlers: list = []
        # keyboard スレッド → Qt メインスレッドへの受け渡しキュー
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._flush_queue)

    def _flush_queue(self) -> None:
        """QTimer で定期的に呼ばれ、キューに積まれたイベントを Qt メインスレッドで処理する。"""
        sig_map = {
            "full":   self.sig_full,
            "region": self.sig_region,
            "window": self.sig_window,
            "save":   self.sig_save,
        }
        try:
            while True:
                item = self._queue.get_nowait()
                action: str = item["action"]
                profile: str = item["profile"]
                if profile == "__active__":
                    sig = sig_map.get(action)
                    if sig:
                        logger.debug("ホットキー発火: action=%r", action)
                        sig.emit()
                else:
                    logger.debug("ホットキー発火: action=%r profile=%r", action, profile)
                    self.sig_capture_profile.emit(action, profile)
        except queue.Empty:
            pass

    def start(self, slots: list[dict]) -> None:
        """スロットリストからホットキーを登録する。"""
        self.stop()
        self._poll_timer.start()  # stop() で停止したタイマーを再開する
        seen_combos: set[str] = set()
        for slot in slots:
            combo = slot.get("combo", "none")
            if not combo or combo == "none":
                continue
            if combo in seen_combos:
                logger.warning("ホットキー重複スキップ: combo=%r", combo)
                continue
            action = slot.get("action", "")
            profile = slot.get("profile", "__active__")
            try:
                # keyboard スレッドからは queue に積むだけ（スレッドセーフ）
                handler = keyboard.add_hotkey(
                    combo,
                    lambda a=action, p=profile: self._queue.put({"action": a, "profile": p}),
                    suppress=False,
                )
                self._handlers.append(handler)
                seen_combos.add(combo)
                logger.info("ホットキー登録: combo=%r action=%r profile=%r", combo, action, profile)
            except Exception as e:
                logger.warning("ホットキー登録失敗: combo=%r action=%r: %s", combo, action, e)

    def stop(self) -> None:
        """登録済みのホットキーをすべて解除する。"""
        self._poll_timer.stop()
        for handler in self._handlers:
            try:
                keyboard.remove_hotkey(handler)
            except Exception as e:
                logger.warning("ホットキー解除失敗: handler=%r: %s", handler, e)
        self._handlers.clear()
        # キューに残った未処理イベントも捨てる
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def update(self, slots: list[dict]) -> None:
        """スロット変更時に再登録する。"""
        self.start(slots)
