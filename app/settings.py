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

PROFILE_DEFAULTS = {
    "save_folder": str(Path.home() / "Pictures"),
    "save_format": "png",
    "auto_backup_enabled": True,
    "open_folder_after_save": False,
    "auto_border_enabled": False,
    "auto_border_color": "#ff0000",
    "auto_border_width": 4,
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


def load() -> dict:
    """
    {"active_profile": str, "profiles": {name: {…}}} を返す。
    ファイルがなければデフォルト構造を返す。
    旧形式（プロファイルキーなし）は自動移行する。
    """
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if "profiles" not in data:
                data = _migrate_old(data)
            # 各プロファイルに不足キーを補完
            for name, prof in data["profiles"].items():
                for k, v in PROFILE_DEFAULTS.items():
                    prof.setdefault(k, v)
            # グローバルホットキースロットを補完
            data.setdefault("hotkey_slots", copy.deepcopy(DEFAULT_HOTKEY_SLOTS))
            return data
        except json.JSONDecodeError:
            logger.warning("設定ファイルが破損しています。デフォルト設定を使用します: %s", CONFIG_FILE)
        except Exception as e:
            logger.warning("設定ファイルの読み込みに失敗しました: %s", e)
    return {
        "active_profile": DEFAULT_PROFILE_NAME,
        "profiles": {DEFAULT_PROFILE_NAME: _new_profile()},
        "hotkey_slots": copy.deepcopy(DEFAULT_HOTKEY_SLOTS),
    }


def save(root: dict) -> None:
    """設定を原子的に保存する（書き込み中クラッシュによる破損を防ぐ）。"""
    tmp = CONFIG_DIR / f"config.tmp.{os.getpid()}"
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(root, f, ensure_ascii=False, indent=2)
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
    return list(root["profiles"].keys())


def set_active(root: dict, name: str) -> None:
    if name in root["profiles"]:
        root["active_profile"] = name


def add_profile(root: dict, name: str) -> bool:
    """新規プロファイルを追加。既存名なら False を返す。"""
    if name in root["profiles"]:
        return False
    root["profiles"][name] = _new_profile()
    return True


def delete_profile(root: dict, name: str) -> bool:
    """プロファイルを削除。最後の1件・アクティブは削除不可。"""
    if len(root["profiles"]) <= 1:
        return False
    if name not in root["profiles"]:
        return False
    del root["profiles"][name]
    if root["active_profile"] == name:
        root["active_profile"] = next(iter(root["profiles"]))
    return True


def rename_profile(root: dict, old: str, new: str) -> bool:
    """プロファイル名を変更。新名が既存の場合は False を返す。"""
    if old not in root["profiles"] or new in root["profiles"] or not new.strip():
        return False
    profiles = root["profiles"]
    # 順序を保ちつつリネーム
    root["profiles"] = {(new if k == old else k): v for k, v in profiles.items()}
    if root["active_profile"] == old:
        root["active_profile"] = new
    return True
