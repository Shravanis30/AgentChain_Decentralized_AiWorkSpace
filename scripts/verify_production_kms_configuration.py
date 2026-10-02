#!/usr/bin/env python3
"""AgentChain Phase 6.10.3: Production KMS Configuration Validator.

Validates production configuration against strict release and security policies
WITHOUT making live AWS or RPC network calls.

Strict Checks:
1. All 14 required production environment variables must be defined.
2. Template placeholders (e.g. <AWS_KMS_KEY_ARN>) are detected as unpopulated.
3. SIGNER_PROVIDER must be 'aws_kms' (or 'kms'). Forbidden: 'local', 'testnet_account', 'raw_key'.
4. AWS_KMS_KEY_ARN must follow valid AWS KMS key ARN format; raw private keys are strictly rejected.
5. Base Mainnet (8453) is strictly HARD BLOCKED.
6. Local development network (31337) is strictly forbidden in production.
7. Anvil default accounts (0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266) and retired historical
   developer accounts (0x70997970C51812dc3A010C7d01b50e0d17dc79C8) are strictly blocked.
8. Relayer and Admin role addresses must be distinct (role separation).
9. All 6 canonical contract addresses must match verified Base Sepolia deployments.
10. Raw private keys in environment are strictly rejected.

Exit Codes:
  0 = Valid production configuration
  1 = Invalid configuration (security violation, bad format, or policy breach)
  2 = Prerequisites unavailable (unpopulated environment or unreplaced placeholders)
"""

import os
import re
import sys
from pathlib import Path
from typing import Any

# Ensure project root in python path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.core.production_config import (
    BLOCKED_PRODUCTION_ADDRESSES,
    CANONICAL_BASE_SEPOLIA_CONTRACTS,
)

REQUIRED_VARS = [
    "CHAIN_ID",
    "RPC_URL",
    "SIGNER_PROVIDER",
    "AWS_REGION",
    "AWS_KMS_KEY_ARN",
    "EXPECTED_SIGNER_ADDRESS",
    "AGENT_REGISTRY_ADDRESS",
    "REVENUE_DISTRIBUTOR_ADDRESS",
    "ESCROW_ADDRESS",
    "RESULT_NOTARY_ADDRESS",
    "REPUTATION_REGISTRY_ADDRESS",
    "USDC_ADDRESS",
    "RELAYER_ROLE_ADDRESS",
    "ADMIN_ROLE_ADDRESS",
]

ETH_ADDRESS_REGEX = re.compile(r"^0x[a-fA-F0-9]{40}$")
KMS_ARN_REGEX = re.compile(r"^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/[a-f0-9-]{36}$")
AWS_REGION_REGEX = re.compile(r"^[a-z]{2}-[a-z]+-[0-9]$")


def is_placeholder(val: str) -> bool:
    """Checks if value is an unpopulated template placeholder like <AWS_REGION>."""
    val = val.strip()
    return not val or (val.startswith("<") and val.endswith(">"))


def validate_config_dict(cfg: dict[str, str]) -> tuple[int, list[str]]:
    """Validates configuration dictionary against production security invariants.

    Returns:
        (exit_code, list_of_messages)
    """
    errors: list[str] = []
    missing_or_placeholders: list[str] = []

    # 1. Check for required variables
    for var in REQUIRED_VARS:
        val = cfg.get(var, "")
        if not val or is_placeholder(val):
            missing_or_placeholders.append(var)

    if missing_or_placeholders:
        return 2, [f"Unpopulated or placeholder configuration variables: {', '.join(missing_or_placeholders)}"]

    # 2. Check for raw private keys in environment
    for forbidden_key in ["DEPLOYER_PRIVATE_KEY", "RAW_PRIVATE_KEY", "PRIVATE_KEY", "SIGNER_PRIVATE_KEY"]:
        if cfg.get(forbidden_key) and not is_placeholder(cfg[forbidden_key]):
            errors.append(f"CRITICAL SECURITY VIOLATION: Raw private key variable '{forbidden_key}' is present in production.")

    # 3. Check SIGNER_PROVIDER
    provider = cfg.get("SIGNER_PROVIDER", "").strip().lower()
    if provider not in ("aws_kms", "kms"):
        errors.append(f"Forbidden SIGNER_PROVIDER '{provider}'. Production requires 'aws_kms'.")

    # 4. Check CHAIN_ID
    raw_chain = cfg.get("CHAIN_ID", "").strip()
    try:
        chain_id = int(raw_chain)
        if chain_id == 8453:
            errors.append("CRITICAL SECURITY GATE: Base Mainnet (Chain ID 8453) is strictly HARD BLOCKED.")
        elif chain_id == 31337:
            errors.append("CRITICAL SECURITY VIOLATION: Local Anvil (Chain ID 31337) is forbidden in production.")
        elif chain_id != 84532:
            errors.append(f"Unsupported Chain ID {chain_id}. Currently approved live chain: 84532 (Base Sepolia).")
    except ValueError:
        errors.append(f"Invalid CHAIN_ID format: '{raw_chain}' (integer required).")

    # 5. Check AWS_REGION
    region = cfg.get("AWS_REGION", "").strip()
    if not AWS_REGION_REGEX.match(region):
        errors.append(f"Invalid AWS_REGION format: '{region}'. Expected e.g. 'us-east-1'.")

    # 6. Check AWS_KMS_KEY_ARN
    arn = cfg.get("AWS_KMS_KEY_ARN", "").strip()
    # Reject raw 32-byte hex key passed as ARN
    clean_arn = arn.removeprefix("0x")
    if len(clean_arn) == 64 and all(c in "0123456789abcdefABCDEF" for c in clean_arn):
        errors.append("CRITICAL SECURITY VIOLATION: A raw 32-byte private key was passed as AWS_KMS_KEY_ARN.")
    elif not KMS_ARN_REGEX.match(arn):
        errors.append(f"Invalid AWS_KMS_KEY_ARN format: '{arn}'. Must match 'arn:aws:kms:<region>:<account-id>:key/<key-id>'.")

    # 7. Check Address formats and blocked identities
    address_fields = [
        "EXPECTED_SIGNER_ADDRESS",
        "RELAYER_ROLE_ADDRESS",
        "ADMIN_ROLE_ADDRESS",
        "AGENT_REGISTRY_ADDRESS",
        "REVENUE_DISTRIBUTOR_ADDRESS",
        "ESCROW_ADDRESS",
        "RESULT_NOTARY_ADDRESS",
        "REPUTATION_REGISTRY_ADDRESS",
        "USDC_ADDRESS",
    ]
    for field in address_fields:
        addr = cfg.get(field, "").strip()
        if not ETH_ADDRESS_REGEX.match(addr):
            errors.append(f"Invalid Ethereum address format for {field}: '{addr}'.")
        elif addr.lower() in [b.lower() for b in BLOCKED_PRODUCTION_ADDRESSES]:
            reason = BLOCKED_PRODUCTION_ADDRESSES.get(addr, "Blocked address")
            errors.append(f"CRITICAL SECURITY VIOLATION: {field} uses blocked address {addr} ({reason}).")

    # 8. Check Role Separation
    relayer_addr = cfg.get("RELAYER_ROLE_ADDRESS", "").strip().lower()
    admin_addr = cfg.get("ADMIN_ROLE_ADDRESS", "").strip().lower()
    if relayer_addr and admin_addr and relayer_addr == admin_addr:
        errors.append("CRITICAL ROLE SEPARATION VIOLATION: Relayer role and Admin role cannot use the same address.")

    # 9. Check Canonical Contracts (Base Sepolia)
    contract_mappings = {
        "AGENT_REGISTRY_ADDRESS": CANONICAL_BASE_SEPOLIA_CONTRACTS["agent_registry"],
        "REVENUE_DISTRIBUTOR_ADDRESS": CANONICAL_BASE_SEPOLIA_CONTRACTS["revenue_distributor"],
        "ESCROW_ADDRESS": CANONICAL_BASE_SEPOLIA_CONTRACTS["escrow"],
        "RESULT_NOTARY_ADDRESS": CANONICAL_BASE_SEPOLIA_CONTRACTS["result_notary"],
        "REPUTATION_REGISTRY_ADDRESS": CANONICAL_BASE_SEPOLIA_CONTRACTS["reputation_registry"],
        "USDC_ADDRESS": CANONICAL_BASE_SEPOLIA_CONTRACTS["usdc"],
    }
    for field, expected_addr in contract_mappings.items():
        actual_addr = cfg.get(field, "").strip()
        if ETH_ADDRESS_REGEX.match(actual_addr) and actual_addr.lower() != expected_addr.lower():
            errors.append(f"Contract address mismatch for {field}: expected {expected_addr}, got {actual_addr}.")

    if errors:
        return 1, errors

    return 0, ["All production KMS configuration invariants verified successfully."]


def main() -> int:
    print("======================================================================")
    print(" AgentChain Phase 6.10.3: Production KMS Configuration Validator")
    print("======================================================================")

    # Collect configuration from environment
    env_config = {var: os.environ.get(var, "") for var in REQUIRED_VARS}
    # Also check forbidden keys
    for k in ["DEPLOYER_PRIVATE_KEY", "RAW_PRIVATE_KEY", "PRIVATE_KEY", "SIGNER_PRIVATE_KEY"]:
        if os.environ.get(k):
            env_config[k] = os.environ[k]

    code, messages = validate_config_dict(env_config)

    if code == 0:
        print("\n[PASS] Valid Production KMS Configuration:")
        for msg in messages:
            print(f"  - {msg}")
        return 0
    elif code == 2:
        print("\n[PREREQUISITES UNAVAILABLE] Configuration is not fully populated for production:")
        for msg in messages:
            print(f"  - {msg}")
        print("\nRequired Production Variables:")
        for v in REQUIRED_VARS:
            print(f"  - {v}")
        return 2
    else:
        print("\n[FAIL] Configuration violates production security policies:")
        for msg in messages:
            print(f"  - {msg}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
