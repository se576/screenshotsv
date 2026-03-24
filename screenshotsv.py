import ctypes
import logging
import subprocess
import sys


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_as_admin() -> bool:
    """UACプロンプトを出して管理者権限で再起動する。成功した場合 True を返す。"""
    # ShellExecuteW は成功時に 32 より大きい値を返す
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, subprocess.list2cmdline(sys.argv), None, 1
    )
    return int(result) > 32


def main():
    if not _is_admin():
        if _relaunch_as_admin():
            sys.exit(0)
        # UAC が拒否された、または失敗した場合はそのまま起動を続ける
        logging.getLogger(__name__).warning(
            "管理者権限なしで起動しています。"
            "他の管理者権限アプリが前面にある場合、グローバルホットキーが動作しないことがあります。"
        )

    from PySide6.QtWidgets import QApplication
    from app.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("スクリーンショット")
    # ウィンドウを閉じてもトレイ常駐できるようにする
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
