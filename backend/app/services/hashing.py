import hashlib
import json
from typing import Any


def canonicalize_json(data: Any) -> str:
    """
    Produce RFC 8785 JSON Canonicalization Scheme (JCS) representation:
    - Keys sorted lexicographically
    - No unnecessary whitespace
    - UTF-8 representation
    """
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_canonical_hash(data: Any) -> str:
    """
    Compute SHA-256 hash of canonicalized JSON.
    Used for tamper-proof persistence and future blockchain anchoring.
    """
    canonical_str = canonicalize_json(data)
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()
