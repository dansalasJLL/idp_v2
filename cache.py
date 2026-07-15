"""
cache.py — content-addressed cache for clause extraction results.

Why this exists
---------------
Every clause is one model call, and a model call costs money and time. Two very
common situations waste both:
  1. Re-running the same contract (a reviewer reloads, a job retries, a pilot
     re-processes a portfolio) — every clause is paid for again.
  2. Boilerplate that recurs across many contracts in a portfolio (standard
     insurance, confidentiality, notice clauses) — paid for once per contract
     instead of once, ever.

A content-addressed cache keys on a hash of (clause text + system prompt +
model + schema). If the exact same extraction input has been seen, the stored
result is returned for $0 and ~0ms. Change any input — a clause edit, a prompt
tweak, a model swap — and the key changes, so a stale result is never served.

Two backends:
  - MemoryCache: process-local, zero-setup, used by default and in tests.
  - DiskCache:   JSON files under a directory, survives restarts. Suitable for
                 a single worker / dev machine. For multi-worker BPO-scale
                 production, implement the same tiny interface against Redis or
                 a shared table — the pipeline doesn't care which backend it is.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import List, Optional, Protocol


def make_key(clause_text: str, system_prompt: str, model: str, schema: dict) -> str:
    """Stable SHA-256 over everything that affects the extraction result.
    Schema is serialized with sorted keys so dict ordering can't change the hash."""
    h = hashlib.sha256()
    h.update(clause_text.encode("utf-8"))
    h.update(b"\x00")
    h.update(system_prompt.encode("utf-8"))
    h.update(b"\x00")
    h.update(model.encode("utf-8"))
    h.update(b"\x00")
    h.update(json.dumps(schema, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


class ExtractionCache(Protocol):
    """The minimal interface a cache backend must implement."""
    def get(self, key: str) -> Optional[List[dict]]: ...
    def set(self, key: str, value: List[dict]) -> None: ...


class MemoryCache:
    """Process-local dict cache. Default backend."""
    def __init__(self):
        self._store: dict = {}

    def get(self, key: str) -> Optional[List[dict]]:
        return self._store.get(key)

    def set(self, key: str, value: List[dict]) -> None:
        self._store[key] = value

    def __len__(self) -> int:
        return len(self._store)


class DiskCache:
    """JSON-file cache under `root`. One file per key. Survives restarts."""
    def __init__(self, root: str = ".idp_cache"):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.root, f"{key}.json")

    def get(self, key: str) -> Optional[List[dict]]:
        path = self._path(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            # A corrupt cache file must not break a run — treat as a miss.
            return None

    def set(self, key: str, value: List[dict]) -> None:
        try:
            with open(self._path(key), "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False)
        except OSError:
            pass  # caching is best-effort; never fatal


class NullCache:
    """Disables caching entirely (always a miss). Useful for benchmarking."""
    def get(self, key: str) -> Optional[List[dict]]:
        return None

    def set(self, key: str, value: List[dict]) -> None:
        pass


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import tempfile
    import shutil

    print("=== cache.py self-test ===\n")

    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    k1 = make_key("clause text", "system prompt", "claude-sonnet-4-6", schema)
    k2 = make_key("clause text", "system prompt", "claude-sonnet-4-6", schema)
    k3 = make_key("clause text EDITED", "system prompt", "claude-sonnet-4-6", schema)
    k4 = make_key("clause text", "system prompt", "jll-falcon", schema)  # model change
    assert k1 == k2, "same inputs must yield same key"
    assert k1 != k3, "changed clause must change key"
    assert k1 != k4, "changed model must change key"
    # key must be order-independent for the schema dict
    schema_reordered = {"properties": {"a": {"type": "string"}}, "type": "object"}
    assert make_key("clause text", "system prompt", "claude-sonnet-4-6", schema_reordered) == k1
    print("make_key: OK (stable, sensitive to real changes, schema-order-independent)")

    for Backend, kw in [(MemoryCache, {}), (DiskCache, {"root": tempfile.mkdtemp()})]:
        c = Backend(**kw)
        assert c.get(k1) is None, "empty cache must miss"
        payload = [{"description": "x", "confidence": 0.9}]
        c.set(k1, payload)
        assert c.get(k1) == payload, "must return stored value"
        assert c.get(k3) is None, "different key must miss"
        print(f"{Backend.__name__}: OK")
        if "root" in kw:
            # DiskCache survives a fresh instance pointed at the same dir
            c2 = DiskCache(root=kw["root"])
            assert c2.get(k1) == payload, "disk cache must persist across instances"
            print("DiskCache persistence: OK")
            shutil.rmtree(kw["root"])

    n = NullCache()
    n.set(k1, [{"x": 1}])
    assert n.get(k1) is None, "NullCache must always miss"
    print("NullCache: OK")

    print("\nAll cache self-tests passed.")
