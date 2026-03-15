"""
In-app notification store for PFR Sentinel.

Thread-safe store that keeps the last N events, accessible from the UI.
Categories: info, warning, error.
"""
import threading
from collections import deque
from datetime import datetime, timezone


# Maximum notifications retained
MAX_NOTIFICATIONS = 50


class Notification:
    """Single notification entry."""

    __slots__ = ('message', 'category', 'timestamp')

    def __init__(self, message, category='info'):
        self.message = message
        self.category = category
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self):
        return {
            'message': self.message,
            'category': self.category,
            'timestamp': self.timestamp.isoformat(),
        }


class NotificationStore:
    """Thread-safe store for in-app notifications.

    Keeps at most MAX_NOTIFICATIONS entries (oldest evicted first).
    """

    def __init__(self, max_size=MAX_NOTIFICATIONS):
        self._lock = threading.Lock()
        self._notifications = deque(maxlen=max_size)

    def add(self, message, category='info'):
        """Add a notification.

        Args:
            message: Notification text
            category: One of 'info', 'warning', 'error'
        """
        notification = Notification(message, category)
        with self._lock:
            self._notifications.append(notification)

    def get_all(self):
        """Return all notifications, newest first.

        Returns:
            List of Notification objects, newest first.
        """
        with self._lock:
            return list(reversed(self._notifications))

    def clear(self):
        """Remove all notifications."""
        with self._lock:
            self._notifications.clear()

    def count(self):
        """Return current notification count."""
        with self._lock:
            return len(self._notifications)


# Module-level singleton
_store = NotificationStore()


def get_notification_store():
    """Return the global NotificationStore singleton."""
    return _store
