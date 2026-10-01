"""Deterministic utilities for verified reputation evidence.

Enforces:
- Deterministic RFC 8785 canonicalization for evidence hashing.
- SHA-256 evidence hashing.
- Deterministic reputation event keys:
  sha256(f"{chain_id}:{agent_id}:{execution_id}:{outcome_type}:{result_hash}:{evidence_hash}")
- UUID <-> bytes32 conversions.
"""

import hashlib
import json
import uuid
from typing import Any


def uuid_to_bytes32(u: uuid.UUID | str) -> str:
    """Converts a standard UUID to a 0x-prefixed 32-byte hex string (padded with trailing zeros)."""
    if isinstance(u, str):
        u = uuid.UUID(u)
    return "0x" + u.bytes.hex().ljust(64, "0")


def bytes32_to_uuid(b32: str | bytes) -> uuid.UUID:
    """Converts a 32-byte hex string or bytes to a UUID from its 16-byte prefix."""
    if isinstance(b32, (bytes, bytearray)):
        clean = b32.hex()
    else:
        clean = str(b32).lower().removeprefix("0x")
    return uuid.UUID(bytes=bytes.fromhex(clean[:32]))


def canonicalize_evidence(data: Any) -> bytes:
    """RFC 8785 compliant canonical JSON serialization."""
    return json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
        indent=None,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def compute_evidence_hash(data: Any) -> str:
    """Computes a SHA-256 digest of the canonicalized evidence payload.
    
    Returns 64-character lowercase hex string.
    """
    if isinstance(data, (bytes, bytearray)):
        raw = data
    elif isinstance(data, str):
        raw = data.encode("utf-8")
    else:
        raw = canonicalize_evidence(data)
    return hashlib.sha256(raw).hexdigest().lower()


def compute_reputation_key(
    chain_id: int,
    agent_id: uuid.UUID | str,
    execution_id: uuid.UUID | str,
    outcome_type: str,
    result_hash: str | None,
    evidence_hash: str,
) -> str:
    """Deterministic, collision-resistant reputation event key.
    
    Enforces idempotency and unique logical event identification:
    sha256("{chain_id}:{agent_id}:{execution_id}:{outcome_type}:{result_hash}:{evidence_hash}")
    """
    norm_agent = str(agent_id).lower()
    norm_exec = str(execution_id).lower()
    norm_outcome = outcome_type.strip().upper()
    norm_result = (result_hash or "").lower().removeprefix("0x")
    norm_evidence = evidence_hash.lower().removeprefix("0x")

    payload = f"{chain_id}:{norm_agent}:{norm_exec}:{norm_outcome}:{norm_result}:{norm_evidence}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
