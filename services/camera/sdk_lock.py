"""Process-global lock guarding every ZWO ASI SDK (ASICamera2.dll) call.

The ZWO SDK is a single C library loaded once per process and is NOT
thread-safe: two threads calling into it at the same time corrupt its internal
state and wedge the DLL until the process exits (the 2026-06-03 16:32 incident —
a capture-thread ``set_control_value`` overlapping a ``disconnect`` →
``stop_exposure``, after which even ``get_num_cameras()`` never returned).

A *per-CameraConnection* lock cannot prevent this, because recovery can hold two
``CameraConnection`` objects at once (a still-running capture thread plus a fresh
reconnect) — each would take its own lock and the two would not exclude each
other while both call the same DLL. This module is the single lock that every
connection shares, so all SDK access in the process serializes through it.

It is a plain (non-reentrant) ``Lock``: no code path acquires it twice on one
thread, and a non-reentrant lock surfaces an accidental double-acquire as an
obvious deadlock rather than silently allowing re-entry.
"""
import threading

# The one lock. Import and use directly; do not create per-instance locks.
SDK_LOCK = threading.Lock()
