from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable, Hashable

_lock = threading.RLock()
_cache: dict[Hashable, tuple[float, Any]] = {}


def get_or_build(key: Hashable, ttl_seconds: float, builder: Callable[[], Any]) -> Any:
    """Return a deep-copied TTL cached value.

    Dashboard/API code frequently adds metadata to returned dictionaries.  The
    deep copies prevent one caller from mutating the shared cached object.
    """
    now = time.monotonic()
    ttl = max(0.0, float(ttl_seconds))
    with _lock:
        entry = _cache.get(key)
        if entry is not None and entry[0] > now:
            return copy.deepcopy(entry[1])

    value = builder()
    with _lock:
        _cache[key] = (now + ttl, copy.deepcopy(value))
    return value


def put(key: Hashable, ttl_seconds: float, value: Any) -> None:
    with _lock:
        _cache[key] = (time.monotonic() + max(0.0, float(ttl_seconds)), copy.deepcopy(value))


def clear(prefix: str | None = None) -> None:
    with _lock:
        if prefix is None:
            _cache.clear()
            return
        for key in list(_cache):
            if isinstance(key, tuple) and key and str(key[0]).startswith(prefix):
                _cache.pop(key, None)
            elif str(key).startswith(prefix):
                _cache.pop(key, None)
