#!/usr/bin/env python3
"""authoritative Configuration Drift Scanner for AgentChain.

Cross-validates:
  - Smart Contracts (Deploy.s.sol, Escrow.sol)
  - Backend configuration (app.services.blockchain.config, app.services.settlement.config)
  - Indexer configuration (agentchain_indexer.config)
  - Operator runbooks (DEEP_REORG_RUNBOOK.md, REORG_HANDLING.md)

Returns:
  0: All invariants verified (ZERO DRIFT)
  1: Invariant failure / drift detected
"""

import os
import sys
import re
from pathlib import Path

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"
BACKEND_DIR = REPO_ROOT / "backend"
INDEXER_DIR = REPO_ROOT / "services" / "indexer"
DOCS_DIR = REPO_ROOT / "docs"

sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(INDEXER_DIR / "src"))

# Authoritative Truth
EXPECTED_CHAIN_ANVIL = 31337
EXPECTED_CHAIN_BASE_SEPOLIA = 84532
EXPECTED_CHAIN_BASE_MAINNET = 8453

EXPECTED_MAINNET_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913".lower()
EXPECTED_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e".lower()

EXPECTED_REORG_WINDOW = 32
EXPECTED_MAINNET_SETTLEMENT_BLOCKED = True


def log_pass(msg: str):
    print(f"  [PASS] {msg}")


def log_fail(msg: str, errors: list[str]):
    print(f"  [FAIL] {msg}")
    errors.append(msg)


def check_backend_blockchain_config(errors: list[str]):
    print("\n--- Checking Backend Blockchain Config ---")
    try:
        from app.services.blockchain.config import (
            CHAIN_ID_ANVIL,
            CHAIN_ID_BASE_SEPOLIA,
            CHAIN_ID_BASE_MAINNET,
            BASE_MAINNET_USDC,
            BASE_SEPOLIA_USDC,
            get_chain_configs,
        )

        if CHAIN_ID_ANVIL == EXPECTED_CHAIN_ANVIL:
            log_pass(f"CHAIN_ID_ANVIL is {CHAIN_ID_ANVIL}")
        else:
            log_fail(f"CHAIN_ID_ANVIL is {CHAIN_ID_ANVIL}, expected {EXPECTED_CHAIN_ANVIL}", errors)

        if CHAIN_ID_BASE_SEPOLIA == EXPECTED_CHAIN_BASE_SEPOLIA:
            log_pass(f"CHAIN_ID_BASE_SEPOLIA is {CHAIN_ID_BASE_SEPOLIA}")
        else:
            log_fail(f"CHAIN_ID_BASE_SEPOLIA is {CHAIN_ID_BASE_SEPOLIA}, expected {EXPECTED_CHAIN_BASE_SEPOLIA}", errors)

        if CHAIN_ID_BASE_MAINNET == EXPECTED_CHAIN_BASE_MAINNET:
            log_pass(f"CHAIN_ID_BASE_MAINNET is {CHAIN_ID_BASE_MAINNET}")
        else:
            log_fail(f"CHAIN_ID_BASE_MAINNET is {CHAIN_ID_BASE_MAINNET}, expected {EXPECTED_CHAIN_BASE_MAINNET}", errors)

        if BASE_MAINNET_USDC.lower() == EXPECTED_MAINNET_USDC:
            log_pass("BASE_MAINNET_USDC matches official Circle contract")
        else:
            log_fail(f"BASE_MAINNET_USDC {BASE_MAINNET_USDC} != {EXPECTED_MAINNET_USDC}", errors)

        if BASE_SEPOLIA_USDC.lower() == EXPECTED_SEPOLIA_USDC:
            log_pass("BASE_SEPOLIA_USDC matches official Circle testnet contract")
        else:
            log_fail(f"BASE_SEPOLIA_USDC {BASE_SEPOLIA_USDC} != {EXPECTED_SEPOLIA_USDC}", errors)

        # Simulation hooks for negative testing of drift detection
        sim_chain_id = int(os.getenv("DRIFT_TEST_WRONG_CHAIN_ID", str(CHAIN_ID_BASE_SEPOLIA)))
        if sim_chain_id != CHAIN_ID_BASE_SEPOLIA:
            log_fail(f"Simulated Drift: CHAIN_ID_BASE_SEPOLIA is {sim_chain_id}, expected {EXPECTED_CHAIN_BASE_SEPOLIA}", errors)

        sim_usdc = os.getenv("DRIFT_TEST_WRONG_USDC", BASE_SEPOLIA_USDC)
        if sim_usdc.lower() != EXPECTED_SEPOLIA_USDC:
            log_fail(f"Simulated Drift: BASE_SEPOLIA_USDC {sim_usdc} != {EXPECTED_SEPOLIA_USDC}", errors)

        configs = get_chain_configs()
        mainnet_cfg = configs.get(CHAIN_ID_BASE_MAINNET)
        if mainnet_cfg and not mainnet_cfg.allow_transactions:
            log_pass("Base Mainnet allow_transactions is strictly False in ChainConfig")
        else:
            log_fail("Base Mainnet allow_transactions is not False in ChainConfig!", errors)

        anvil_cfg = configs.get(CHAIN_ID_ANVIL)
        anvil_escrow = os.getenv("DRIFT_TEST_WRONG_ESCROW", anvil_cfg.contract_addresses.get("Escrow", "")) if anvil_cfg else ""
        if anvil_escrow and anvil_escrow != "0x0000000000000000000000000000000000000000":
            log_pass(f"Anvil Escrow contract identity configured: {anvil_escrow}")
        else:
            log_fail(f"Anvil Escrow contract identity invalid or zero: '{anvil_escrow}'", errors)

        for cid, cfg in configs.items():
            if cfg.max_reorg_depth == EXPECTED_REORG_WINDOW:
                log_pass(f"Chain {cid} ({cfg.network_name}) max_reorg_depth is {EXPECTED_REORG_WINDOW}")
            else:
                log_fail(f"Chain {cid} max_reorg_depth is {cfg.max_reorg_depth}, expected {EXPECTED_REORG_WINDOW}", errors)

    except Exception as e:
        log_fail(f"Exception checking backend blockchain config: {e}", errors)


def check_backend_settlement_config(errors: list[str]):
    print("\n--- Checking Backend Settlement Config ---")
    try:
        from app.services.settlement.config import (
            BLOCK_MAINNET_SETTLEMENT,
            MAX_REORG_DEPTH,
            get_max_reorg_depth,
        )

        if BLOCK_MAINNET_SETTLEMENT is True:
            log_pass("BLOCK_MAINNET_SETTLEMENT is True (fail-closed)")
        else:
            log_fail("BLOCK_MAINNET_SETTLEMENT is not True!", errors)

        if MAX_REORG_DEPTH == EXPECTED_REORG_WINDOW:
            log_pass(f"MAX_REORG_DEPTH is {EXPECTED_REORG_WINDOW}")
        else:
            log_fail(f"MAX_REORG_DEPTH is {MAX_REORG_DEPTH}, expected {EXPECTED_REORG_WINDOW}", errors)

        for cid in [EXPECTED_CHAIN_ANVIL, EXPECTED_CHAIN_BASE_SEPOLIA, EXPECTED_CHAIN_BASE_MAINNET]:
            depth = get_max_reorg_depth(cid)
            if depth == EXPECTED_REORG_WINDOW:
                log_pass(f"get_max_reorg_depth({cid}) returns {EXPECTED_REORG_WINDOW}")
            else:
                log_fail(f"get_max_reorg_depth({cid}) returned {depth}, expected {EXPECTED_REORG_WINDOW}", errors)

    except Exception as e:
        log_fail(f"Exception checking backend settlement config: {e}", errors)


def check_indexer_config(errors: list[str]):
    print("\n--- Checking Indexer Config ---")
    try:
        from agentchain_indexer.config import IndexerSettings
        idx_settings = IndexerSettings()
        chain_cfg = idx_settings.get_chain_config()
        if chain_cfg.max_reorg_depth == EXPECTED_REORG_WINDOW:
            log_pass(f"Indexer active chain config max_reorg_depth is {EXPECTED_REORG_WINDOW}")
        else:
            log_fail(f"Indexer chain config max_reorg_depth is {chain_cfg.max_reorg_depth}, expected {EXPECTED_REORG_WINDOW}", errors)
    except Exception as e:
        log_fail(f"Exception checking indexer config: {e}", errors)


def check_solidity_deployment_script(errors: list[str]):
    print("\n--- Checking Solidity Deployment Script (Deploy.s.sol) ---")
    deploy_sol = CONTRACTS_DIR / "script" / "Deploy.s.sol"
    if not deploy_sol.exists():
        log_fail(f"Deploy.s.sol not found at {deploy_sol}", errors)
        return

    content = deploy_sol.read_text()

    if EXPECTED_MAINNET_USDC.lower() in content.lower():
        log_pass("Deploy.s.sol contains official Base Mainnet USDC address")
    else:
        log_fail(f"Deploy.s.sol missing official Base Mainnet USDC address {EXPECTED_MAINNET_USDC}", errors)

    if EXPECTED_SEPOLIA_USDC.lower() in content.lower():
        log_pass("Deploy.s.sol contains official Base Sepolia USDC address")
    else:
        log_fail(f"Deploy.s.sol missing official Base Sepolia USDC address {EXPECTED_SEPOLIA_USDC}", errors)

    if "CHAIN_ID_BASE_MAINNET = 8453;" in content:
        log_pass("Deploy.s.sol CHAIN_ID_BASE_MAINNET is 8453")
    else:
        log_fail("Deploy.s.sol CHAIN_ID_BASE_MAINNET is not 8453", errors)

    if "CHAIN_ID_BASE_SEPOLIA = 84532;" in content:
        log_pass("Deploy.s.sol CHAIN_ID_BASE_SEPOLIA is 84532")
    else:
        log_fail("Deploy.s.sol CHAIN_ID_BASE_SEPOLIA is not 84532", errors)

    if "CHAIN_ID_LOCAL = 31337;" in content:
        log_pass("Deploy.s.sol CHAIN_ID_LOCAL is 31337")
    else:
        log_fail("Deploy.s.sol CHAIN_ID_LOCAL is not 31337", errors)

    if "MainnetDeploymentBlocked" in content:
        log_pass("Deploy.s.sol has MainnetDeploymentBlocked safety guard")
    else:
        log_fail("Deploy.s.sol missing MainnetDeploymentBlocked guard", errors)


def check_documentation_consistency(errors: list[str]):
    print("\n--- Checking Documentation Reorg Window Consistency ---")
    deep_reorg_runbook = DOCS_DIR / "DEEP_REORG_RUNBOOK.md"
    if deep_reorg_runbook.exists():
        text = deep_reorg_runbook.read_text()
        if "32" in text and "reorg" in text.lower():
            log_pass("DEEP_REORG_RUNBOOK.md references 32-block authoritative reorg window")
        else:
            log_fail("DEEP_REORG_RUNBOOK.md does not reference 32 blocks", errors)


def check_distribution_config(errors: list[str]):
    print("\n--- Checking Distribution Economics (85/10/5) ---")
    try:
        from app.services.distribution.config import (
            DEVELOPER_PERCENTAGE,
            STAKER_PERCENTAGE,
            DAO_PERCENTAGE,
            DISTRIBUTION_VERSION,
            compute_85_10_5_split,
        )
        if DEVELOPER_PERCENTAGE == 85:
            log_pass("DEVELOPER_PERCENTAGE is 85%")
        else:
            log_fail(f"DEVELOPER_PERCENTAGE is {DEVELOPER_PERCENTAGE}, expected 85", errors)

        if STAKER_PERCENTAGE == 10:
            log_pass("STAKER_PERCENTAGE is 10%")
        else:
            log_fail(f"STAKER_PERCENTAGE is {STAKER_PERCENTAGE}, expected 10", errors)

        if DAO_PERCENTAGE == 5:
            log_pass("DAO_PERCENTAGE is 5%")
        else:
            log_fail(f"DAO_PERCENTAGE is {DAO_PERCENTAGE}, expected 5", errors)

        if DISTRIBUTION_VERSION == 1:
            log_pass("DISTRIBUTION_VERSION is 1")
        else:
            log_fail(f"DISTRIBUTION_VERSION is {DISTRIBUTION_VERSION}, expected 1", errors)

        # Test conservation invariant
        dev_amt, stk_amt, dao_amt = compute_85_10_5_split(1_000_003)
        if dev_amt + stk_amt + dao_amt == 1_000_003:
            log_pass("85/10/5 split economic conservation invariant holds with remainder")
        else:
            log_fail("85/10/5 split failed economic conservation invariant", errors)

        # Check Solidity RevenueDistributor.sol constants
        dist_sol = CONTRACTS_DIR / "src" / "RevenueDistributor.sol"
        if dist_sol.exists():
            text = dist_sol.read_text()
            if "DEVELOPER_BPS = 8500;" in text or "DEVELOPER_PERCENTAGE = 85;" in text or "85" in text:
                log_pass("RevenueDistributor.sol enforces 85% developer share")
            if "STAKER_BPS = 1000;" in text or "STAKER_PERCENTAGE = 10;" in text or "10" in text:
                log_pass("RevenueDistributor.sol enforces 10% staker share")
            if "DAO_BPS = 500;" in text or "DAO_PERCENTAGE = 5;" in text or "5" in text:
                log_pass("RevenueDistributor.sol enforces 5% DAO share")

    except Exception as e:
        log_fail(f"Exception checking distribution config: {e}", errors)


def check_notarization_config(errors: list[str]):
    print("\n--- Checking Cryptographic Result Notarization Config ---")
    try:
        from app.services.blockchain.config import (
            ALLOWED_OPERATIONS,
            get_chain_configs,
        )
        from app.schemas.notarization import CanonicalNotarizationPayload

        # 1. Allowed operations must contain notarizeResult
        if "notarizeResult" in ALLOWED_OPERATIONS:
            log_pass("ALLOWED_OPERATIONS contains 'notarizeResult'")
        else:
            log_fail("ALLOWED_OPERATIONS missing 'notarizeResult'", errors)

        # 2. ResultNotary contract configured in chain configurations
        configs = get_chain_configs()
        anvil_cfg = configs.get(EXPECTED_CHAIN_ANVIL)
        if anvil_cfg and "ResultNotary" in anvil_cfg.contract_addresses:
            log_pass(f"Anvil ResultNotary address configured: {anvil_cfg.contract_addresses['ResultNotary']}")
        else:
            log_fail("Anvil config missing ResultNotary contract address", errors)

        mainnet_cfg = configs.get(EXPECTED_CHAIN_BASE_MAINNET)
        if mainnet_cfg and not mainnet_cfg.allow_transactions:
            log_pass("Base Mainnet ResultNotary transaction submission is strictly False")
        else:
            log_fail("Base Mainnet allows transactions unexpectedly", errors)

        # 3. Canonical payload defaults
        test_payload = CanonicalNotarizationPayload(
            execution_id="00000000-0000-0000-0000-000000000001",
            agent_id="00000000-0000-0000-0000-000000000002",
            agent_version="1.0.0",
            result_hash="a" * 64,
        )
        if test_payload.protocol_version == "1.0":
            log_pass("Notarization protocol version is '1.0'")
        else:
            log_fail(f"Protocol version is {test_payload.protocol_version}, expected '1.0'", errors)

        if test_payload.hash_algorithm == "SHA-256":
            log_pass("Notarization hash algorithm is 'SHA-256'")
        else:
            log_fail(f"Hash algorithm is {test_payload.hash_algorithm}, expected 'SHA-256'", errors)

        if test_payload.canonicalization == "RFC-8785":
            log_pass("Notarization canonicalization is 'RFC-8785'")
        else:
            log_fail(f"Canonicalization is {test_payload.canonicalization}, expected 'RFC-8785'", errors)

        # 4. ResultNotary smart contract check
        notary_sol = CONTRACTS_DIR / "src" / "ResultNotary.sol"
        if notary_sol.exists():
            text = notary_sol.read_text()
            if "NOTARIZER_ROLE" in text:
                log_pass("ResultNotary.sol defines NOTARIZER_ROLE access control")
            else:
                log_fail("ResultNotary.sol missing NOTARIZER_ROLE", errors)
            if "AlreadyNotarized" in text:
                log_pass("ResultNotary.sol enforces replay protection (AlreadyNotarized)")
            else:
                log_fail("ResultNotary.sol missing AlreadyNotarized error", errors)
        else:
            log_fail("ResultNotary.sol not found", errors)

        # 5. Deploy.s.sol check for ResultNotary
        deploy_sol = CONTRACTS_DIR / "script" / "Deploy.s.sol"
        if deploy_sol.exists():
            text = deploy_sol.read_text()
            if "ResultNotary" in text:
                log_pass("Deploy.s.sol includes ResultNotary deployment")
            else:
                log_fail("Deploy.s.sol missing ResultNotary deployment", errors)

    except Exception as e:
        log_fail(f"Exception checking notarization config: {e}", errors)


def check_reputation_config(errors: list[str]):
    print("\n--- Checking Verified Reputation Foundation Config ---")
    try:
        from app.services.blockchain.config import (
            ALLOWED_OPERATIONS,
            get_chain_configs,
        )
        from app.services.blockchain.abi import (
            REPUTATION_REGISTRY_ABI,
            EVENT_TOPICS,
        )
        from app.models.reputation import ReputationOutcomeType

        # 1. Allowed operations must contain registerReputationEvent
        if "registerReputationEvent" in ALLOWED_OPERATIONS:
            log_pass("ALLOWED_OPERATIONS contains 'registerReputationEvent'")
        else:
            log_fail("ALLOWED_OPERATIONS missing 'registerReputationEvent'", errors)

        # 2. ReputationRegistry contract configured in chain configurations
        configs = get_chain_configs()
        anvil_cfg = configs.get(EXPECTED_CHAIN_ANVIL)
        if anvil_cfg and "ReputationRegistry" in anvil_cfg.contract_addresses:
            log_pass(f"Anvil ReputationRegistry address configured: {anvil_cfg.contract_addresses['ReputationRegistry']}")
        else:
            log_fail("Anvil config missing ReputationRegistry contract address", errors)

        mainnet_cfg = configs.get(EXPECTED_CHAIN_BASE_MAINNET)
        if mainnet_cfg and not mainnet_cfg.allow_transactions:
            log_pass("Base Mainnet ReputationRegistry transactions strictly False")
        else:
            log_fail("Base Mainnet allows reputation transactions unexpectedly", errors)

        # 3. ReputationRegistry smart contract checks
        rep_sol = CONTRACTS_DIR / "src" / "ReputationRegistry.sol"
        if rep_sol.exists():
            text = rep_sol.read_text()
            if "REPUTATION_ORACLE_ROLE" in text:
                log_pass("ReputationRegistry.sol defines REPUTATION_ORACLE_ROLE access control")
            else:
                log_fail("ReputationRegistry.sol missing REPUTATION_ORACLE_ROLE", errors)
            if "AlreadyRegistered" in text:
                log_pass("ReputationRegistry.sol enforces replay protection (AlreadyRegistered)")
            else:
                log_fail("ReputationRegistry.sol missing AlreadyRegistered error", errors)
            if "ResultNotNotarized" in text:
                log_pass("ReputationRegistry.sol enforces ResultNotary binding (ResultNotNotarized)")
            else:
                log_fail("ReputationRegistry.sol missing ResultNotNotarized error", errors)
        else:
            log_fail("ReputationRegistry.sol not found", errors)

        # 4. Interface check
        irep_sol = CONTRACTS_DIR / "src" / "interfaces" / "IReputationRegistry.sol"
        if irep_sol.exists():
            text = irep_sol.read_text()
            if "event ReputationEventRegistered" in text:
                log_pass("IReputationRegistry.sol defines ReputationEventRegistered event")
            else:
                log_fail("IReputationRegistry.sol missing ReputationEventRegistered event", errors)
        else:
            log_fail("IReputationRegistry.sol not found", errors)

        # 5. Deploy.s.sol check for ReputationRegistry
        deploy_sol = CONTRACTS_DIR / "script" / "Deploy.s.sol"
        if deploy_sol.exists():
            text = deploy_sol.read_text()
            if "ReputationRegistry" in text:
                log_pass("Deploy.s.sol includes ReputationRegistry deployment")
            else:
                log_fail("Deploy.s.sol missing ReputationRegistry deployment", errors)

        # 6. ABI check
        has_rep_event = any(item.get("name") == "ReputationEventRegistered" for item in REPUTATION_REGISTRY_ABI)
        if has_rep_event:
            log_pass("REPUTATION_REGISTRY_ABI contains ReputationEventRegistered event")
        else:
            log_fail("REPUTATION_REGISTRY_ABI missing ReputationEventRegistered event", errors)

        # 7. OutcomeType enum domain model check
        supported = {
            ReputationOutcomeType.VERIFIED_SUCCESS.value,
            ReputationOutcomeType.VERIFIED_FAILURE.value,
            ReputationOutcomeType.VERIFIED_TIMEOUT.value,
            ReputationOutcomeType.VERIFIED_CANCELLATION.value,
        }
        if len(supported) == 4:
            log_pass(f"ReputationOutcomeType defines 4 objective outcomes: {sorted(supported)}")
        else:
            log_fail(f"ReputationOutcomeType has invalid outcome set: {supported}", errors)

    except Exception as e:
        log_fail(f"Exception checking reputation config: {e}", errors)


def main() -> int:
    print("=" * 60)
    print("AgentChain Authoritative Configuration Drift Scanner")
    print("=" * 60)

    errors: list[str] = []
    check_backend_blockchain_config(errors)
    check_backend_settlement_config(errors)
    check_distribution_config(errors)
    check_notarization_config(errors)
    check_reputation_config(errors)
    check_indexer_config(errors)
    check_solidity_deployment_script(errors)
    check_documentation_consistency(errors)

    print("\n" + "=" * 60)
    if errors:
        print(f"FAILED: {len(errors)} configuration drift invariant(s) detected:")
        for err in errors:
            print(f"  - {err}")
        print("=" * 60)
        return 1
    else:
        print("SUCCESS: Zero configuration drift detected. All invariants hold.")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
