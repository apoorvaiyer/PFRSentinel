"""Single-instance guard.

PFR Sentinel runs unattended 24/7 and often lives in the system tray, so a
user can easily forget it's already running and launch it again — spawning a
second process that fights over the camera, output ports, and config file.

This guard uses a Qt local socket (named pipe on Windows) as the lock. The
first process listens on a well-known name; a second launch fails to listen,
connects to the running instance to ask it to surface its window, then exits.

Requires a QCoreApplication/QApplication instance to already exist.
"""
from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from services.logger import app_logger

# Stable, app-specific name. Scoped per user by the OS, which is what we want —
# two different Windows users may each run their own instance.
_SERVER_NAME = "PFRSentinel-single-instance-v1"


class SingleInstanceGuard(QObject):
    """Detect and signal an already-running PFR Sentinel instance.

    Emits ``activate_requested`` on the owning (first) instance whenever a
    second launch attempt connects, so the UI can restore/raise its window.
    """

    activate_requested = Signal()

    def __init__(self, name: str = _SERVER_NAME):
        super().__init__()
        self._name = name
        self._server: QLocalServer | None = None

    def already_running(self) -> bool:
        """Return True if another instance owns the lock (and was signalled).

        On the first instance this claims the lock and returns False. On a
        second instance it pokes the owner to show its window and returns True;
        the caller should then exit.
        """
        probe = QLocalSocket()
        probe.connectToServer(self._name)
        if probe.waitForConnected(300):
            # Owner is alive — ask it to surface, then bow out.
            probe.write(b"activate")
            probe.flush()
            probe.waitForBytesWritten(300)
            probe.disconnectFromServer()
            app_logger.info("Another PFR Sentinel instance is already running; signalled it to show.")
            return True

        # No owner answered. A crashed prior instance can leave a stale socket
        # file that blocks listen() on Unix; removeServer clears it (no-op when
        # nothing is stale).
        QLocalServer.removeServer(self._name)

        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        if not self._server.listen(self._name):
            # Couldn't claim the lock and couldn't connect — don't block startup,
            # just run without the guard rather than refusing to launch.
            app_logger.warning(
                f"Single-instance guard failed to listen: {self._server.errorString()} — "
                "continuing without it."
            )
            self._server = None
        return False

    def _on_new_connection(self):
        conn = self._server.nextPendingConnection() if self._server else None
        if conn is None:
            return
        # We don't need the payload; any connection means "surface the window".
        conn.disconnectFromServer()
        self.activate_requested.emit()
