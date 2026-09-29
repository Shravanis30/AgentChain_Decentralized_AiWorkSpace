"""Cryptographic identity and encoding utilities for Result Notarization."""

import uuid
from typing import Any


def execution_id_to_bytes32(execution_id: str | uuid.UUID) -> str:
    """Encodes a 16-byte UUID into a 32-byte hex string (padded with zeros)."""
    if isinstance(execution_id, str):
        execution_id = uuid.UUID(execution_id)
    return "0x" + execution_id.bytes.hex().ljust(64, "0")


def bytes32_to_execution_id(b32: str) -> uuid.UUID:
    """Decodes a 32-byte hex string into a 16-byte UUID from its prefix."""
    clean = b32.removeprefix("0x")[:32]
    return uuid.UUID(bytes=bytes.fromhex(clean))


def result_hash_to_bytes32(result_hash: str) -> str:
    """Normalizes a 32-byte (64 hex char) SHA-256 result hash to 0x-prefixed bytes32."""
    clean = result_hash.removeprefix("0x").lower()
    if len(clean) != 64:
        raise ValueError(f"Invalid SHA-256 result hash length: expected 64 hex chars, got {len(clean)}")
    return "0x" + clean


def bytes32_to_result_hash(b32: str) -> str:
    """Normalizes bytes32 hex string to 64-char lowercase SHA-256 result hash."""
    clean = b32.removeprefix("0x").lower()
    return clean


def artifact_commitment_to_bytes32(commitment: str | None) -> str:
    """Normalizes optional artifact commitment or returns 32-byte zero hash."""
    if not commitment:
        return "0x" + "00" * 32
    clean = commitment.removeprefix("0x").lower()
    if len(clean) < 64:
        clean = clean.ljust(64, "0")
    elif len(clean) > 64:
        clean = clean[:64]
    return "0x" + clean


def build_notarization_idempotency_key(
    chain_id: int, contract_address: str, execution_id: uuid.UUID | str, result_hash: str
) -> str:
    """Generates a deterministic idempotency key for notarization operations (bounded <= 100 chars)."""
    exec_str = str(execution_id)
    res_clean = result_hash.removeprefix("0x").lower()[:16]
    return f"notarize:{chain_id}:{exec_str}:{res_clean}"
