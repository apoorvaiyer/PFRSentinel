"""
Test in-app notification store
"""
import pytest
import os
import sys
import threading

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.notification_store import NotificationStore, Notification


class TestNotificationAdd:
    """Test adding notifications"""

    def test_add_stores_with_timestamp(self):
        """Test adding notification stores it with timestamp"""
        store = NotificationStore()
        store.add("Test message", "info")
        items = store.get_all()
        assert len(items) == 1
        assert items[0].message == "Test message"
        assert items[0].timestamp is not None

    def test_add_default_category_is_info(self):
        """Test default category is info"""
        store = NotificationStore()
        store.add("Hello")
        assert store.get_all()[0].category == "info"

    def test_categories_preserved(self):
        """Test notification categories (info, warning, error) are preserved"""
        store = NotificationStore()
        store.add("Info msg", "info")
        store.add("Warn msg", "warning")
        store.add("Error msg", "error")

        items = store.get_all()
        categories = [n.category for n in items]
        assert "info" in categories
        assert "warning" in categories
        assert "error" in categories


class TestNotificationCapacity:
    """Test capacity and eviction"""

    def test_caps_at_max_entries(self):
        """Test store caps at max entries (oldest evicted)"""
        store = NotificationStore(max_size=5)
        for i in range(10):
            store.add(f"Message {i}")

        assert store.count() == 5
        items = store.get_all()
        # Oldest should be evicted, newest kept
        assert items[0].message == "Message 9"
        assert items[-1].message == "Message 5"

    def test_default_max_is_50(self):
        """Test default max size is 50"""
        store = NotificationStore()
        for i in range(60):
            store.add(f"Msg {i}")
        assert store.count() == 50


class TestNotificationRetrieval:
    """Test getting notifications"""

    def test_get_all_newest_first(self):
        """Test notifications are returned newest first"""
        store = NotificationStore()
        store.add("First")
        store.add("Second")
        store.add("Third")

        items = store.get_all()
        assert items[0].message == "Third"
        assert items[1].message == "Second"
        assert items[2].message == "First"

    def test_get_all_empty_store(self):
        """Test get_all on empty store returns empty list"""
        store = NotificationStore()
        assert store.get_all() == []

    def test_content_is_retrievable(self):
        """Test notification content is retrievable"""
        store = NotificationStore()
        store.add("Camera disconnected", "error")
        item = store.get_all()[0]
        assert item.message == "Camera disconnected"
        assert item.category == "error"


class TestNotificationClear:
    """Test clearing notifications"""

    def test_clear_empties_store(self):
        """Test clearing notifications empties the store"""
        store = NotificationStore()
        store.add("A")
        store.add("B")
        store.clear()
        assert store.count() == 0
        assert store.get_all() == []


class TestNotificationThreadSafety:
    """Test thread-safe access"""

    def test_concurrent_add_from_multiple_producers(self):
        """Test thread-safe access from multiple producers"""
        store = NotificationStore(max_size=200)
        errors = []

        def producer(prefix, count):
            try:
                for i in range(count):
                    store.add(f"{prefix}-{i}", "info")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=producer, args=(f"t{t}", 50))
            for t in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert store.count() == 200


class TestNotificationSerialization:
    """Test notification dict conversion"""

    def test_to_dict(self):
        """Test Notification.to_dict() returns expected keys"""
        n = Notification("Test", "warning")
        d = n.to_dict()
        assert d['message'] == "Test"
        assert d['category'] == "warning"
        assert 'timestamp' in d
