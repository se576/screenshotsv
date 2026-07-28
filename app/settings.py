import copy
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".screenshotsv"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_PROFILE_NAME = "デフォルト"

# グローバルホットキースロット（プロファイル横断、ルートに保存）
# profile: "__active__" = アクティブプロファイル使用、それ以外はプロファイル名
DEFAULT_HOTKEY_SLOTS = [
    {"action": "full",   "combo": "ctrl+alt+f", "profile": "__active__"},
    {"action": "full",   "combo": "none",        "profile": "__active__"},
    {"action": "region", "combo": "ctrl+alt+r",  "profile": "__active__"},
    {"action": "region", "combo": "none",         "profile": "__active__"},
    {"action": "window", "combo": "ctrl+alt+w",  "profile": "__active__"},
    {"action": "window", "combo": "none",         "profile": "__active__"},
    {"action": "save",   "combo": "ctrl+alt+s",  "profile": "__active__"},
    {"action": "save",   "combo": "none",         "profile": "__active__"},
]

# ×ボタンの動作（ルートに保存）: "ask"=毎回確認 / "tray"=トレイ常駐 / "quit"=終了
DEFAULT_CLOSE_ACTION = "ask"
CLOSE_ACTIONS = ("ask", "tray", "quit")

# トレイ常駐時の通知バルーン表示（ルートに保存）
DEFAULT_TRAY_NOTIFY = True

# プロファイル指定ホットキーで撮影したときの動作（ルートに保存）:
# "edit"=編集画面に表示 / "quicksave"=プロファイルの保存先へ即時保存（従来動作）
DEFAULT_HOTKEY_CAPTURE_ACTION = "edit"
HOTKEY_CAPTURE_ACTIONS = ("edit", "quicksave")

# 多重起動防止用のアプリ識別子（エントリポイントとメインウィンドウで共有）
SINGLE_INSTANCE_MUTEX_NAME = "screenshotsv_single_instance"
IPC_SERVER_NAME = "screenshotsv_ipc"

# 読み込みに失敗（既存ファイルが一過性に読めない）したことを示す内部フラグ。
# このキーが root にある間は save() を抑止し、既存ファイルをデフォルトで潰さない。
_LOAD_ERROR_KEY = "_load_error"

# ホットキースロットが備えるべき必須キー
_HOTKEY_SLOT_KEYS = ("action", "combo", "profile")

PROFILE_DEFAULTS: dict[str, object] = {
    "save_folder": str(Path.home() / "Pictures"),
    "save_format": "png",
    "auto_backup_enabled": True,
    "open_folder_after_save": False,
    "auto_border_enabled": False,
    "auto_border_color": "#ff0000",
    "auto_border_width": 4.0,  # 0.01px 単位（旧設定の int 値もそのまま読める）
}


def _new_profile() -> dict:
    return dict(PROFILE_DEFAULTS)


def _migrate_old(data: dict) -> dict:
    """旧形式（フラットなconfig）を新形式に変換する。"""
    profile = {}
    for k in PROFILE_DEFAULTS:
        profile[k] = data.get(k, PROFILE_DEFAULTS[k])
    return {
        "active_profile": DEFAULT_PROFILE_NAME,
        "profiles": {DEFAULT_PROFILE_NAME: profile},
    }


def _default_root() -> dict:
    """デフォルトの設定ルートを返す。"""
    return {
        "active_profile": DEFAULT_PROFILE_NAME,
        "profiles": {DEFAULT_PROFILE_NAME: _new_profile()},
        "hotkey_slots": copy.deepcopy(DEFAULT_HOTKEY_SLOTS),
        "close_action": DEFAULT_CLOSE_ACTION,
        "hotkey_capture_action": DEFAULT_HOTKEY_CAPTURE_ACTION,
        "tray_notify": DEFAULT_TRAY_NOTIFY,
    }


def _normalize_hotkey_slots(slots: object) -> list[dict]:
    """ホットキースロットの構造を検証する。
    必須キー欠損・非dict要素はスキップし、全滅・非リストならデフォルトに戻す。"""
    if not isinstance(slots, list):
        return copy.deepcopy(DEFAULT_HOTKEY_SLOTS)
    valid = [s for s in slots
             if isinstance(s, dict) and all(k in s for k in _HOTKEY_SLOT_KEYS)]
    if not valid and slots:
        # 要素はあるが全て不正 → デフォルトへ復帰
        return copy.deepcopy(DEFAULT_HOTKEY_SLOTS)
    return valid


def _normalize(data: dict) -> dict:
    """読み込んだ設定を新形式へ移行し、不足キー・不正値を補完する。"""
    if "profiles" not in data:
        data = _migrate_old(data)
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        profiles = {DEFAULT_PROFILE_NAME: _new_profile()}
    # 不正な型のプロファイルのみ個別に置換し、正常なものは保持する
    for name in list(profiles):
        prof = profiles[name]
        if not isinstance(prof, dict):
            prof = _new_profile()
            profiles[name] = prof
        for k, v in PROFILE_DEFAULTS.items():
            prof.setdefault(k, v)
    data["profiles"] = profiles
    # アクティブプロファイルが存在しなければ先頭にフォールバック
    if data.get("active_profile") not in profiles:
        data["active_profile"] = next(iter(profiles))
    # グローバルホットキースロット（構造検証込み）
    data["hotkey_slots"] = _normalize_hotkey_slots(data.get("hotkey_slots"))
    # ×ボタンの動作を補完（不正値はデフォルトに戻す）
    if data.get("close_action") not in CLOSE_ACTIONS:
        data["close_action"] = DEFAULT_CLOSE_ACTION
    # ホットキー撮影後の動作を補完（不正値はデフォルトに戻す）
    if data.get("hotkey_capture_action") not in HOTKEY_CAPTURE_ACTIONS:
        data["hotkey_capture_action"] = DEFAULT_HOTKEY_CAPTURE_ACTION
    # トレイ常駐時の通知表示を補完
    data["tray_notify"] = bool(data.get("tray_notify", DEFAULT_TRAY_NOTIFY))
    return data


def load() -> dict:
    """
    {"active_profile": str, "profiles": {name: {…}}} を返す。
    ファイルがなければデフォルト構造を返す。
    旧形式（プロファイルキーなし）は自動移行する。
    破損（JSON不正・型不正）はデフォルトで復旧する。
    一過性の読み取り失敗（ロック等）は既存ファイル保護のためデフォルトを返しつつ
    保存抑止フラグを立て、次回 save() が実データをデフォルトで潰さないようにする。
    """
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("設定ファイルが破損しています。デフォルト設定を使用します: %s", CONFIG_FILE)
            return _default_root()
        except OSError as e:
            # 一過性の読み取り失敗（AVロック等）。既存ファイルを守るため保存を抑止する。
            logger.warning("設定ファイルを読み込めませんでした。既存設定を保護します: %s", e)
            root = _default_root()
            root[_LOAD_ERROR_KEY] = True
            return root
        if not isinstance(data, dict):
            logger.warning("設定ファイルの形式が不正です。デフォルト設定を使用します: %s", CONFIG_FILE)
            return _default_root()
        return _normalize(data)
    return _default_root()


def save(root: dict) -> None:
    """設定を原子的に保存する（書き込み中クラッシュによる破損を防ぐ）。"""
    if root.get(_LOAD_ERROR_KEY):
        # 読み込みに失敗して既存ファイルを読めなかったセッション。
        # デフォルトで実データを上書きしないよう保存を抑止する。
        logger.warning("読み込みに失敗した設定のため保存を抑止します（既存ファイル保護）")
        return
    tmp = CONFIG_DIR / f"config.tmp.{os.getpid()}"
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(root, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(CONFIG_FILE)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------
# プロファイル操作ヘルパー
# ------------------------------------------------------------------

def active_profile(root: dict) -> dict:
    """アクティブプロファイルの設定 dict を返す（参照）。"""
    profiles = root.setdefault("profiles", {})
    name = root.get("active_profile", "")
    if name not in profiles:
        # 設定不整合時: 先頭プロファイルにフォールバック
        name = next(iter(profiles), DEFAULT_PROFILE_NAME)
        if name not in profiles:
            profiles[name] = _new_profile()
        root["active_profile"] = name
    return profiles[name]


def profile_names(root: dict) -> list[str]:
    return list(root.setdefault("profiles", {}).keys())


def set_active(root: dict, name: str) -> None:
    if name in root.setdefault("profiles", {}):
        root["active_profile"] = name


def add_profile(root: dict, name: str) -> bool:
    """新規プロファイルを追加。既存名なら False を返す。"""
    profiles = root.setdefault("profiles", {})
    if name in profiles:
        return False
    profiles[name] = _new_profile()
    return True


def delete_profile(root: dict, name: str) -> bool:
    """プロファイルを削除。最後の1件・アクティブは削除不可。"""
    profiles = root.setdefault("profiles", {})
    if len(profiles) <= 1:
        return False
    if name not in profiles:
        return False
    del profiles[name]
    if root.get("active_profile") == name:
        root["active_profile"] = next(iter(profiles))
    return True


def rename_profile(root: dict, old: str, new: str) -> bool:
    """プロファイル名を変更。新名が既存の場合は False を返す。"""
    profiles = root.setdefault("profiles", {})
    if old not in profiles or new in profiles or not new.strip():
        return False
    # 順序を保ちつつリネーム
    root["profiles"] = {(new if k == old else k): v for k, v in profiles.items()}
    if root.get("active_profile") == old:
        root["active_profile"] = new
    return True
