"""
グローバルホットキー管理。
pynput.keyboard.GlobalHotKeys でフックを設定し、
コールバックでは queue に積むだけにして Qt メインスレッドで処理する。
（pynput のコールバックは別スレッドで動くため、直接 Signal.emit() はしない）
"""
import queue
import logging
import re
import time
from pynput import keyboard as _pynput_kb
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
_DEBOUNCE_SEC = 0.4  # 同一ホットキーの連打・多重発火を無視する間隔

# keyboard ライブラリ形式 (ctrl+alt+f) → pynput GlobalHotKeys 形式 (<ctrl>+<alt>+f)
_SPECIAL_KEYS = frozenset({
    "ctrl", "alt", "shift", "cmd", "win", "super",
    "space", "enter", "return", "tab", "esc", "escape", "backspace", "delete",
    "home", "end", "insert", "pause", "print_screen",
    "up", "down", "left", "right",
    "caps_lock", "num_lock", "scroll_lock",
    "page_up", "page_down",
})


def _to_pynput_combo(combo: str) -> str:
    """keyboard ライブラリ形式のコンボ文字列を pynput GlobalHotKeys 形式に変換する。"""
    parts = [p.strip() for p in combo.lower().split("+")]
    converted = []
    for part in parts:
        if part in _SPECIAL_KEYS or re.match(r"^f\d+$", part):
            converted.append(f"<{part}>")
        else:
            converted.append(part)
    return "+".join(converted)


class HotkeyManager(QObject):
    sig_full            = Signal()
    sig_region          = Signal()
    sig_window          = Signal()
    sig_save            = Signal()
    sig_capture_profile = Signal(str, str)  # (action, profile_name)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener: _pynput_kb.GlobalHotKeys | None = None
        # pynput スレッド → Qt メインスレッドへの受け渡しキュー
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        # (action, profile) → 最終発火時刻。連打によるキャプチャ・保存の多重実行を防ぐ
        self._last_fired: dict[tuple[str, str], float] = {}
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
                now = time.monotonic()
                if now - self._last_fired.get((action, profile), 0.0) < _DEBOUNCE_SEC:
                    logger.debug("ホットキー連打を無視: action=%r profile=%r", action, profile)
                    continue
                self._last_fired[(action, profile)] = now
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
        hotkey_map: dict = {}
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
                pynput_combo = _to_pynput_combo(combo)
                # pynput スレッドからは queue に積むだけ（スレッドセーフ）
                hotkey_map[pynput_combo] = (
                    lambda a=action, p=profile: self._queue.put({"action": a, "profile": p})
                )
                seen_combos.add(combo)
                logger.info("ホットキー登録: combo=%r → %r action=%r profile=%r",
                            combo, pynput_combo, action, profile)
            except Exception as e:
                logger.warning("ホットキー登録失敗: combo=%r action=%r: %s", combo, action, e)

        if hotkey_map:
            try:
                self._listener = _pynput_kb.GlobalHotKeys(hotkey_map)
                self._listener.start()
                self._poll_timer.start()
                logger.info("ホットキーリスナー起動: %d 件登録", len(hotkey_map))
            except Exception as e:
                logger.warning("ホットキーリスナー起動失敗: %s", e)
                self._listener = None

    def stop(self) -> None:
        """ホットキーリスナーを停止してキューを空にする。"""
        self._poll_timer.stop()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception as e:
                logger.warning("ホットキーリスナー停止失敗: %s", e)
            self._listener = None
        # キューに残った未処理イベントも捨てる
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def update(self, slots: list[dict]) -> None:
        """スロット変更時に再登録する。"""
        self.start(slots)
