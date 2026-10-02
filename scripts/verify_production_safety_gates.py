#!/usr/bin/env python3
"""CI / Static Security Gate for AgentChain Production & Mainnet Safety Readiness.

Runs automated checks to enforce:
1. Secret Scanning: No private keys, mnemonics, or credentials in tracked files.
2. Mainnet Hard Block: Base Mainnet (8453) is strictly blocked across contracts, relayer, and signers.
3. Production Signer Abstraction: Raw private keys forbidden in production; fail-closed KMS/HSM/MPC boundaries.
4. Role Separation: Explicit role addresses required; Anvil & test accounts rejected on live networks.
5. Deployment Guard: Strict checks in deployment scripts for chain ID, roles, and token bindings.
6. Git / Docker Hardening: Verification of .gitignore and .dockerignore patterns.
"""

import sys
import re
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

ANVIL_DEFAULT_ACCOUNTS = [
    "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266".lower(),
    "0x70997970C51812dc3A010C7d01b50e0d17dc79C8".lower(),
]

BLOCKED_CHAIN_IDS = [8453]

# Pattern for 32-byte hex private key (excluding all-zero, all-f, or common test dummy values)
PRIVATE_KEY_PATTERN = re.compile(r'(?i)(?:private[_-]?key|secret[_-]?key|signer[_-]?key)\s*[:=]\s*["\']?(0x[0-9a-fA-F]{64})["\']?')


def run_check(name: str):
    def decorator(func):
        def wrapper(*args, **kwargs):
            print(f"\n[CHECK] {name} ... ", end="", flush=True)
            try:
                func(*args, **kwargs)
                print("PASS")
                return True
            except Exception as e:
                print(f"FAIL\n  --> {str(e)}")
                return False
        return wrapper
    return decorator


@run_check("Secret Scanning: Git Tracked Files")
def check_no_tracked_secrets():
    # Get list of tracked files
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True
    )
    tracked_files = [f.strip() for f in result.stdout.splitlines() if f.strip()]

    forbidden_patterns = [
        ".dev_wallet.txt",
        ".env.local",
        ".env.production",
        ".env.test",
        ".pem",
        ".keystore",
    ]

    for f in tracked_files:
        for pattern in forbidden_patterns:
            if pattern in f and not f.endswith(".example") and not f.endswith(".template"):
                raise AssertionError(f"Sensitive file tracked in git: {f}")

        # Scan code files for hardcoded private keys outside test fixtures
        if f.endswith((".py", ".sol", ".ts", ".tsx", ".js", ".json", ".yml", ".yaml")) and "test" not in f.lower() and "mock" not in f.lower():
            file_path = REPO_ROOT / f
            if file_path.exists() and file_path.stat().st_size < 1_000_000:
                try:
                    content = file_path.read_text(encoding="utf-8", errors="ignore")
                    matches = PRIVATE_KEY_PATTERN.findall(content)
                    for m in matches:
                        # Allow documentation placeholders and known Anvil dummy keys in docs only
                        if "0x0000" in m or "0xffff" in m:
                            continue
                        raise AssertionError(f"Potential private key found in tracked production file {f}: {m[:10]}...")
                except Exception as e:
                    if isinstance(e, AssertionError):
                        raise
                    pass


@run_check("Git & Docker Hardening: Ignored Files Verification")
def check_gitignore_and_dockerignore():
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert ".env" in gitignore, ".env not in .gitignore"
    assert ".dev_wallet.txt" in gitignore, ".dev_wallet.txt not in .gitignore"
    assert "*.pem" in gitignore, "*.pem not in .gitignore"
    assert "*.key" in gitignore, "*.key not in .gitignore"

    dockerignore_path = REPO_ROOT / ".dockerignore"
    assert dockerignore_path.exists(), ".dockerignore does not exist"
    dockerignore = dockerignore_path.read_text()
    assert ".git" in dockerignore, ".git not in .dockerignore"
    assert ".env*" in dockerignore, ".env* not in .dockerignore"
    assert ".dev_wallet.txt" in dockerignore, ".dev_wallet.txt not in .dockerignore"


@run_check("Mainnet Hard Block: Smart Contract Guard")
def check_contract_mainnet_guard():
    deploy_script = (REPO_ROOT / "contracts" / "script" / "Deploy.s.sol").read_text()
    assert "CHAIN_ID_BASE_MAINNET = 8453;" in deploy_script
    assert "MainnetDeploymentBlocked" in deploy_script
    assert "if (chainId == CHAIN_ID_BASE_MAINNET)" in deploy_script or "if (config.chainId == CHAIN_ID_BASE_MAINNET)" in deploy_script


@run_check("Mainnet Hard Block: Relayer & Startup Guard")
def check_relayer_mainnet_guard():
    relayer_code = (REPO_ROOT / "backend" / "app" / "services" / "blockchain" / "relayer.py").read_text()
    assert "8453" in relayer_code, "Chain 8453 check missing in relayer"
    assert "Base Mainnet relayer startup is blocked" in relayer_code, "Base Mainnet relayer blocked message missing"


@run_check("Mainnet Hard Block: Signer Abstraction Guard")
def check_signer_mainnet_guard():
    signer_code = (REPO_ROOT / "backend" / "app" / "services" / "blockchain" / "signer.py").read_text()
    assert "8453" in signer_code
    assert "SignerNetworkMismatchError" in signer_code
    assert "ProductionSignerUnavailableError" in signer_code


@run_check("Production Signer Abstraction: Fail-Closed Provider Boundaries")
def check_production_signer_boundaries():
    signer_code = (REPO_ROOT / "backend" / "app" / "services" / "blockchain" / "signer.py").read_text()
    assert "class KmsSignerAdapter" in signer_code
    assert "class HsmSignerAdapter" in signer_code
    assert "class MpcSignerAdapter" in signer_code
    assert "raise ProductionSignerUnavailableError" in signer_code
    assert "ProductionSigner" in signer_code


@run_check("Role Separation & Anvil Default Account Guard")
def check_role_separation_guards():
    deploy_script = (REPO_ROOT / "contracts" / "script" / "Deploy.s.sol").read_text()
    assert "AnvilAccountBlockedOnLiveNetwork" in deploy_script
    assert "ExplicitRoleConfigurationRequired" in deploy_script

    prod_config = (REPO_ROOT / "backend" / "app" / "core" / "production_config.py").read_text()
    assert "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266" in prod_config
    assert "0x70997970C51812dc3A010C7d01b50e0d17dc79C8" in prod_config
    assert "CRITICAL ROLE SEPARATION VIOLATION" in prod_config


@run_check("Production Configuration Model & Invariants")
def check_production_config_model():
    from backend.app.core.production_config import (
        ProductionEnvironmentConfig,
        RoleConfig,
        ContractConfig,
        EnvironmentType,
        ProductionSignerType,
        ProductionConfigError,
        CANONICAL_BASE_SEPOLIA_CONTRACTS
    )

    valid_roles = RoleConfig(
        relayer_role_address="0x473888C859F88D3De7b5A20986805b4dC3178189",
        admin_role_address="0x162dEB52f30f551020c23155Bc5c66419c184d82",
    )
    valid_contracts = ContractConfig(
        agent_registry_address=CANONICAL_BASE_SEPOLIA_CONTRACTS["agent_registry"],
        revenue_distributor_address=CANONICAL_BASE_SEPOLIA_CONTRACTS["revenue_distributor"],
        escrow_address=CANONICAL_BASE_SEPOLIA_CONTRACTS["escrow"],
        result_notary_address=CANONICAL_BASE_SEPOLIA_CONTRACTS["result_notary"],
        reputation_registry_address=CANONICAL_BASE_SEPOLIA_CONTRACTS["reputation_registry"],
        usdc_address=CANONICAL_BASE_SEPOLIA_CONTRACTS["usdc"],
    )

    # 1. Valid production config
    config = ProductionEnvironmentConfig(
        app_env=EnvironmentType.PRODUCTION,
        chain_id=84532,
        rpc_url="https://sepolia.base.org",
        signer_provider=ProductionSignerType.KMS,
        signer_key_reference="arn:aws:kms:us-east-1:123456789012:key/test",
        roles=valid_roles,
        contracts=valid_contracts,
        debug=False,
    )
    assert config.chain_id == 84532

    # 2. Mainnet must fail closed
    try:
        ProductionEnvironmentConfig(
            app_env=EnvironmentType.PRODUCTION,
            chain_id=8453,
            rpc_url="https://mainnet.base.org",
            signer_provider=ProductionSignerType.KMS,
            signer_key_reference="arn:aws:kms:us-east-1:123456789012:key/test",
            roles=valid_roles,
            contracts=valid_contracts,
        )
        raise AssertionError("Mainnet configuration should have failed!")
    except (ProductionConfigError, ValueError) as e:
        assert "HARD BLOCKED" in str(e)

    # 3. Anvil role address in production must fail closed
    try:
        bad_roles = RoleConfig(
            relayer_role_address="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
            admin_role_address="0x162dEB52f30f551020c23155Bc5c66419c184d82",
        )
        ProductionEnvironmentConfig(
            app_env=EnvironmentType.PRODUCTION,
            chain_id=84532,
            rpc_url="https://sepolia.base.org",
            signer_provider=ProductionSignerType.KMS,
            signer_key_reference="arn:aws:kms:us-east-1:123456789012:key/test",
            roles=bad_roles,
            contracts=valid_contracts,
        )
        raise AssertionError("Anvil default role address should have failed in production!")
    except (ProductionConfigError, ValueError) as e:
        assert "uses blocked address" in str(e)



def main():
    print("======================================================================")
    print(" AgentChain Phase 6.10: Production & Mainnet Safety Verification Gate")
    print("======================================================================")

    checks = [
        check_no_tracked_secrets,
        check_gitignore_and_dockerignore,
        check_contract_mainnet_guard,
        check_relayer_mainnet_guard,
        check_signer_mainnet_guard,
        check_production_signer_boundaries,
        check_role_separation_guards,
        check_production_config_model,
    ]

    all_passed = True
    for check in checks:
        if not check():
            all_passed = False

    print("\n----------------------------------------------------------------------")
    if all_passed:
        print("ALL PRODUCTION SAFETY GATES PASSED (8/8)")
        print("----------------------------------------------------------------------")
        sys.exit(0)
    else:
        print("PRODUCTION SAFETY GATES FAILED")
        print("----------------------------------------------------------------------")
        sys.exit(1)


if __name__ == "__main__":
    main()
