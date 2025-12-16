from __future__ import annotations
import time
from typing import Any, Dict, Optional

class SimpleTTLCache:
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl = ttl_seconds
        self._store: Dict[str, Any] = {}
        self._ts: Dict[str, float] = {}

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value
        self._ts[key] = time.time()
        self._gc()

    def get(self, key: str) -> Optional[Any]:
        self._gc()
        if key not in self._store:
            return None
        if time.time() - self._ts.get(key, 0) > self.ttl:
            self._store.pop(key, None)
            self._ts.pop(key, None)
            return None
        return self._store.get(key)

    def _gc(self) -> None:
        now = time.time()
        expired = [k for k, ts in self._ts.items() if now - ts > self.ttl]
        for k in expired:
            self._store.pop(k, None)
            self._ts.pop(k, None)
