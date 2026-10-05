"""
Phase 6.7 — Precision-Safe Pricing and Economic Utilities.

Strict, deterministic, floating-point-free monetary handling for AgentChain.
Enforces USDC 6-decimal atomic units and integer-only arithmetic.
"""

from decimal import Decimal, InvalidOperation
from typing import Any
import uuid

# Official USDC specification on Base (Sepolia & Mainnet)
USDC_DECIMALS: int = 6
USDC_FACTOR: int = 10**USDC_DECIMALS  # 1_000_000 atomic units per 1.00 USDC
MAX_PRICE_ATOMIC: int = 1_000_000_000 * USDC_FACTOR  # 1 Billion USDC max boundary


def parse_usdc_display_to_atomic(val: str | int | Decimal) -> int:
    """
    Parse a human-readable USDC display value (e.g. '1.50', '20', 10) into exact atomic units.
    Uses Python Decimal with string conversion — NEVER binary floating-point.

    Raises:
        ValueError: If negative, malformed, exceeding max price, or having >6 decimal places.
    """
    if val is None:
        raise ValueError("Amount cannot be None")

    if isinstance(val, int):
        if val < 0:
            raise ValueError(f"Amount must be non-negative, got {val}")
        atomic = val * USDC_FACTOR
        if atomic > MAX_PRICE_ATOMIC:
            raise ValueError(f"Amount {val} exceeds maximum allowed boundary")
        return atomic

    str_val = str(val).strip()
    if not str_val:
        raise ValueError("Amount string cannot be empty")

    try:
        dec = Decimal(str_val)
    except InvalidOperation:
        raise ValueError(f"Invalid monetary string: '{str_val}'")

    if dec.is_nan() or dec.is_infinite():
        raise ValueError(f"Invalid monetary value: '{str_val}'")

    if dec < Decimal("0"):
        raise ValueError(f"Amount must be non-negative, got {str_val}")

    # Check decimal places
    # as_tuple().exponent gives negative count of decimal digits for non-integer Decimals
    exponent = dec.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -USDC_DECIMALS:
        raise ValueError(
            f"USDC amounts cannot have more than {USDC_DECIMALS} decimal places: '{str_val}'"
        )

    atomic = int(dec * Decimal(USDC_FACTOR))
    if atomic > MAX_PRICE_ATOMIC:
        raise ValueError(f"Amount {str_val} exceeds maximum allowed boundary")

    return atomic


def parse_atomic_units(val: int | str) -> int:
    """
    Validate and return an amount already provided in atomic units.
    """
    if val is None:
        raise ValueError("Atomic amount cannot be None")

    try:
        atomic = int(val)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid atomic units value: '{val}'")

    if atomic < 0:
        raise ValueError(f"Atomic units must be non-negative, got {atomic}")
    if atomic > MAX_PRICE_ATOMIC:
        raise ValueError(f"Atomic amount {atomic} exceeds maximum allowed boundary")

    return atomic


def format_atomic_to_usdc(atomic_units: int) -> str:
    """
    Format atomic units into standard USDC display format (e.g. 1500000 -> '1.500000').
    """
    if atomic_units < 0:
        raise ValueError(f"Atomic units cannot be negative: {atomic_units}")
    dec = Decimal(atomic_units) / Decimal(USDC_FACTOR)
    return f"{dec:.6f}"


def extract_version_price(version_obj: Any) -> tuple[int, str]:
    """
    Extract exact price in atomic units and currency from an AgentVersion instance or dict.
    Returns (price_atomic, currency).

    Rules:
    - If pricing_config has 'price_atomic' (int): use directly after validation.
    - If pricing_config has 'amount' (str/number): parse via parse_usdc_display_to_atomic.
    - If pricing_config is empty or lacks price fields: defaults to 0 (free tier / zero price).
    - Default currency is 'USDC'.
    """
    if version_obj is None:
        return 0, "USDC"

    pricing_config = getattr(version_obj, "pricing_config", None)
    if pricing_config is None and isinstance(version_obj, dict):
        pricing_config = version_obj.get("pricing_config")

    if not isinstance(pricing_config, dict) or not pricing_config:
        return 0, "USDC"

    currency = str(pricing_config.get("currency", "USDC")).upper()

    # 1. Direct atomic units
    if "price_atomic" in pricing_config and pricing_config["price_atomic"] is not None:
        return parse_atomic_units(pricing_config["price_atomic"]), currency

    # 2. Display amount string
    if "amount" in pricing_config and pricing_config["amount"] is not None:
        amount_str = str(pricing_config["amount"])
        return parse_usdc_display_to_atomic(amount_str), currency

    return 0, currency


def validate_budget_constraint(price_atomic: int, max_budget_atomic: int | None) -> bool:
    """
    Check if a price meets the budget constraint using strict integer comparison.
    If max_budget_atomic is None, the budget is unconstrained.
    """
    if max_budget_atomic is None:
        return True
    return price_atomic <= max_budget_atomic


def validate_tool_requirements(
    version_manifest: dict[str, Any] | None, required_tools: list[str]
) -> bool:
    """
    Verify that the candidate agent version provides all required tools.
    """
    if not required_tools:
        return True
    if not version_manifest or not isinstance(version_manifest, dict):
        return False

    raw_tools = version_manifest.get("tools", [])
    available_tools: set[str] = set()
    for t in raw_tools:
        if isinstance(t, dict) and t.get("enabled", True):
            name = t.get("tool_name")
            if name:
                available_tools.add(str(name).strip().lower())

    for req in required_tools:
        if req.strip().lower() not in available_tools:
            return False
    return True


def validate_runtime_requirements(
    version_manifest: dict[str, Any] | None, max_timeout_seconds: int | None
) -> bool:
    """
    Verify that the candidate agent version's timeout does not exceed max_timeout_seconds.
    """
    if max_timeout_seconds is None:
        return True
    if not version_manifest or not isinstance(version_manifest, dict):
        return True

    runtime = version_manifest.get("runtime", {})
    if isinstance(runtime, dict):
        timeout = runtime.get("timeout_seconds")
        if timeout is not None and int(timeout) > max_timeout_seconds:
            return False
    return True
