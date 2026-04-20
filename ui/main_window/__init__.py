"""
ui.main_window — MainWindow package.

Re-exports MainWindow so existing callers using
``from ui.main_window import MainWindow`` keep working unchanged.
"""
from .window import MainWindow  # noqa: F401

__all__ = ['MainWindow']
