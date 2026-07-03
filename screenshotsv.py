import ctypes
import logging
import subprocess
import sys

from app.settings import SINGLE_INSTANCE_MUTEX_NAME, IPC_SERVER_NAME


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ERROR_ALREADY_EXISTS = 183
_SYNCHRONIZE = 0x00100000

# 単一インスタンスを保証するミューテックスハンドル。
# プロセス終了時に OS が解放するため、生存中はモジュール変数で保持し続ける。
_instance_mutex = None


def _instance_already_running() -> bool:
    """既存インスタンスがミューテックスを保持しているか確認する（取得はしない）。"""
    handle = _kernel32.OpenMutexW(_SYNCHRONIZE, False, SINGLE_INSTANCE_MUTEX_NAME)
    if handle:
        _kernel32.CloseHandle(handle)
        return True
    return False


def _acquire_single_instance() -> bool:
    """ミューテックスを取得する。既存インスタンスがあれば False を返す。"""
    global _instance_mutex
    handle = _kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
    if not handle:
        # ミューテックス自体が作れない場合は多重起動チェックを諦めて起動を続ける
        logging.getLogger(__name__).warning(
            "多重起動チェック用ミューテックスの作成に失敗しました: %s", ctypes.get_last_error()
        )
        return True
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return False
    _instance_mutex = handle
    return True


def _activate_existing_instance() -> None:
    """既存インスタンスにウィンドウ表示を要求する。失敗しても静かに諦める。"""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket

    app = QCoreApplication.instance() or QCoreApplication(sys.argv)  # noqa: F841 (Qt利用に必須)
    sock = QLocalSocket()
    sock.connectToServer(IPC_SERVER_NAME)
    if sock.waitForConnected(1000):
        sock.write(b"show")
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
    else:
        logging.getLogger(__name__).warning(
            "既存インスタンスへの通知に失敗しました: %s", sock.errorString()
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
    # UAC プロンプトを出す前に既存インスタンスを確認する（あれば前面表示して終了）
    if _instance_already_running():
        _activate_existing_instance()
        sys.exit(0)

    if not _is_admin():
        if _relaunch_as_admin():
            sys.exit(0)
        # UAC が拒否された、または失敗した場合はそのまま起動を続ける
        logging.getLogger(__name__).warning(
            "管理者権限なしで起動しています。"
            "他の管理者権限アプリが前面にある場合、グローバルホットキーが動作しないことがあります。"
        )

    # 昇格後のプロセスで確定取得する。同時起動のレースはここで片方が負けて収束する
    if not _acquire_single_instance():
        _activate_existing_instance()
        sys.exit(0)

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
