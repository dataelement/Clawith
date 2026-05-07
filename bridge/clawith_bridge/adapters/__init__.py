"""Adapters for each local agent flavor.

Each adapter produces a stream of `SessionEvent` dicts for a given session_id.
"""
from .base import BaseAdapter, SessionEvent, SubprocessAdapter, DaemonAdapter

__all__ = ["BaseAdapter", "SessionEvent", "SubprocessAdapter", "DaemonAdapter"]
