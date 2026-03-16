"""
Notification flyout for the AppBar.

Shows recent in-app notifications from the NotificationStore
with category-based styling and a clear button.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QSizePolicy, QFrame
)
from PySide6.QtCore import Qt, QTimer
from qfluentwidgets import (
    Flyout, FlyoutViewBase, BodyLabel, CaptionLabel,
    PushButton, FluentIcon, ToolButton
)

from services.notification_store import get_notification_store
from ..theme.tokens import Colors, Typography, Spacing


# Category color mapping
_CATEGORY_COLORS = {
    'info': Colors.accent_default,
    'warning': '#e6a700',
    'error': '#e81123',
}


class NotificationItem(QFrame):
    """Single notification entry widget."""

    def __init__(self, notification, parent=None):
        super().__init__(parent)
        self._setup_ui(notification)

    def _setup_ui(self, notification):
        color = _CATEGORY_COLORS.get(notification.category, Colors.text_muted)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {Colors.bg_surface};
                border-left: 3px solid {color};
                padding: 2px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)

        # Message text
        msg = BodyLabel(notification.message)
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color: {Colors.text_primary}; font-size: {Typography.size_body}px;")
        layout.addWidget(msg)

        # Timestamp
        ts = notification.timestamp.strftime('%H:%M:%S')
        time_label = CaptionLabel(ts)
        time_label.setStyleSheet(f"color: {Colors.text_muted};")
        layout.addWidget(time_label)


class NotificationFlyoutView(FlyoutViewBase):
    """Flyout content showing notification list."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(420)
        self.setMaximumHeight(420)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Header row
        header = QHBoxLayout()
        title = BodyLabel("Notifications")
        title.setStyleSheet(f"""
            font-size: {Typography.size_subtitle}px;
            font-weight: 600;
            color: {Colors.text_primary};
        """)
        header.addWidget(title)
        header.addStretch()

        self.clear_btn = ToolButton(FluentIcon.DELETE)
        self.clear_btn.setFixedSize(28, 28)
        self.clear_btn.setToolTip("Clear all")
        self.clear_btn.clicked.connect(self._on_clear)
        header.addWidget(self.clear_btn)
        layout.addLayout(header)

        # Scrollable notification list
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
        """)

        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(4)
        self.list_layout.addStretch()

        self.scroll.setWidget(self.list_widget)
        layout.addWidget(self.scroll, 1)

        # Empty state
        self.empty_label = BodyLabel("No notifications")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color: {Colors.text_muted};")
        layout.addWidget(self.empty_label)

        self._refresh()

    def _refresh(self):
        """Reload notifications from the store."""
        store = get_notification_store()
        items = store.get_all()

        # Clear existing items (keep the stretch at end)
        while self.list_layout.count() > 1:
            child = self.list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if items:
            self.empty_label.hide()
            self.scroll.show()
            for n in items[:30]:  # Show max 30 in flyout
                self.list_layout.insertWidget(
                    self.list_layout.count() - 1,
                    NotificationItem(n)
                )
        else:
            self.empty_label.show()
            self.scroll.hide()

    def _on_clear(self):
        store = get_notification_store()
        store.clear()
        self._refresh()
