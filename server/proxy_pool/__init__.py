"""Synchronous proxy pool for engine integration."""

from .models import ProxyResult
from .runtime_sync import SyncProxyPool

# Public name kept stable for the engine.  It now has no async or SSH-forward
# side effects; installation is performed separately by install.py.
ProxyPool = SyncProxyPool

__all__ = ["ProxyPool", "SyncProxyPool", "ProxyResult"]
