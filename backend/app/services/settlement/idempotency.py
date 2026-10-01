"""Deterministic settlement idempotency key generation."""

import re


def generate_settlement_idempotency_key(
    chain_id: int,
    escrow_id: str,
    action: str,
    authorization_version: int = 1,
) -> str:
    """Computes a deterministic idempotency key for a logical settlement request.
    
    Guarantees:
    - Same logical settlement request always yields the identical idempotency key.
    - Normalized case prevents string formatting discrepancies.
    """
    clean_escrow = escrow_id.strip().lower()
    clean_action = action.strip().upper()
    return f"settlement:{chain_id}:{clean_escrow}:{clean_action}:{authorization_version}"
