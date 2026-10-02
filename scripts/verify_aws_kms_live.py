#!/usr/bin/env python3
"""AgentChain Phase 6.10.2: Operator-Controlled Live AWS KMS Verification Script.

Purpose:
Executes read-only inspection and cryptographic signature verification against
an operator-supplied AWS KMS asymmetric secp256k1 key.

Strict Invariants:
1. NEVER provisions or creates AWS infrastructure, keys, IAM roles, or users.
2. NEVER stores or logs credentials, secret keys, or private key material.
3. NEVER broadcasts a blockchain transaction (0 on Base Sepolia, 0 on Base Mainnet).
4. Fails closed with exit code 2 if prerequisites are missing.
5. Fails closed with exit code 1 if verification fails.
6. Returns exit code 0 only upon cryptographic verification against a real KMS key.

Exit Codes:
  0 = Live verification passed
  1 = Verification failed
  2 = Prerequisites unavailable / not executed
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure project root is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "backend"))

from eth_account import Account
from web3 import Web3

from app.services.blockchain.config import (
    CHAIN_ID_BASE_SEPOLIA,
    get_chain_config,
)
from app.services.blockchain.errors import (
    SecurityConfigurationError,
    SignerIdentityMismatchError,
    SignerNetworkMismatchError,
)
from app.services.blockchain.relayer import BlockchainRelayer
from app.services.blockchain.signer import (
    SECP256K1_HALF_N,
    SECP256K1_N,
    AwsKmsSigner,
    SignerEnvironment,
    SignerFactory,
    SignerType,
    decode_der_ecdsa_signature,
    derive_ethereum_address_from_public_key,
    determine_recovery_id,
    extract_uncompressed_public_key_from_spki,
)


# Fixed deterministic 32-byte test digest per Phase 6.10.4 Section 7
DETERMINISTIC_TEST_MESSAGE = "AgentChain Phase 6.10.4 KMS attestation"
DETERMINISTIC_DIGEST = Web3.keccak(text=DETERMINISTIC_TEST_MESSAGE)


def check_prerequisites() -> tuple[bool, str, str, str, str]:
    """Inspects environment for required non-secret operator configuration."""
    arn = (
        os.environ.get("AWS_KMS_KEY_ARN")
        or os.environ.get("SIGNER_KEY_REFERENCE")
        or ""
    ).strip()
    region = (os.environ.get("AWS_REGION") or "us-east-1").strip()
    expected_address = (os.environ.get("EXPECTED_SIGNER_ADDRESS") or "").strip()

    if not arn:
        return False, arn, region, expected_address, "Missing AWS_KMS_KEY_ARN (or SIGNER_KEY_REFERENCE)"

    # Verify boto3 availability and credential resolution without printing secrets
    try:
        import boto3
        session = boto3.Session(region_name=region)
        credentials = session.get_credentials()
        if credentials is None or not getattr(credentials, "access_key", None):
            return False, arn, region, expected_address, "AWS credentials not resolved by boto3 ambient provider chain"
    except Exception as exc:
        return False, arn, region, expected_address, f"boto3 initialization error: {exc}"

    return True, arn, region, expected_address, "OK"


def verify_live_kms(key_arn: str, region: str, expected_address: str | None) -> dict[str, Any]:
    """Performs live read and signature verification against AWS KMS."""
    import boto3

    client = boto3.client("kms", region_name=region)

    results: dict[str, Any] = {
        "status": "FAIL",
        "key_arn_redacted": key_arn.split(":")[-1] if ":" in key_arn else key_arn,
        "region": region,
        "metadata_checks": {},
        "public_key_checks": {},
        "cryptographic_verification": {},
        "signer_factory_check": "PENDING",
    }

    print(f"\n1. Describing KMS Key: {key_arn}")
    desc_resp = client.describe_key(KeyId=key_arn)
    meta = desc_resp.get("KeyMetadata", {})

    key_spec = meta.get("KeySpec")
    key_usage = meta.get("KeyUsage")
    key_state = meta.get("KeyState")

    results["metadata_checks"] = {
        "KeySpec": key_spec,
        "KeyUsage": key_usage,
        "KeyState": key_state,
    }

    print(f"   KeySpec:  {key_spec}")
    print(f"   KeyUsage: {key_usage}")
    print(f"   KeyState: {key_state}")

    if key_spec != "ECC_SECG_P256K1":
        raise SecurityConfigurationError(f"Unsupported KMS KeySpec '{key_spec}'. Required: 'ECC_SECG_P256K1'")
    if key_usage != "SIGN_VERIFY":
        raise SecurityConfigurationError(f"Unsupported KMS KeyUsage '{key_usage}'. Required: 'SIGN_VERIFY'")
    if key_state != "Enabled":
        raise SecurityConfigurationError(f"KMS key is not enabled: '{key_state}'")

    print("\n2. Retrieving Public Key (GetPublicKey)...")
    pub_resp = client.get_public_key(KeyId=key_arn)
    spki_bytes = pub_resp.get("PublicKey")
    if not spki_bytes:
        raise ValueError("KMS returned empty PublicKey")

    uncompressed_pub = extract_uncompressed_public_key_from_spki(bytes(spki_bytes))
    derived_address = derive_ethereum_address_from_public_key(uncompressed_pub)

    results["public_key_checks"] = {
        "spki_bytes_len": len(spki_bytes),
        "uncompressed_pub_len": len(uncompressed_pub),
        "derived_ethereum_address": derived_address,
    }

    print(f"   SPKI Length: {len(spki_bytes)} bytes")
    print(f"   Uncompressed Point: 65 bytes (prefix 0x04)")
    print(f"   Derived Ethereum Address: {derived_address}")

    if expected_address:
        if derived_address.lower() != expected_address.lower():
            raise SignerIdentityMismatchError(
                f"KMS derived address {derived_address} does not match expected {expected_address}"
            )
        print(f"   [PASS] Derived address matches expected: {expected_address}")
        results["public_key_checks"]["expected_address_match"] = True
    else:
        print("   [INFO] No EXPECTED_SIGNER_ADDRESS provided for strict identity binding comparison.")

    print(f"\n3. Signing Deterministic Test Digest (Sign)...")
    print(f"   Digest (Keccak-256): 0x{DETERMINISTIC_DIGEST.hex()}")
    print("   MessageType: DIGEST (no double-hashing)")
    print("   SigningAlgorithm: ECDSA_SHA_256")

    sign_resp = client.sign(
        KeyId=key_arn,
        Message=DETERMINISTIC_DIGEST,
        MessageType="DIGEST",
        SigningAlgorithm="ECDSA_SHA_256",
    )
    raw_signature = sign_resp.get("Signature")
    if not raw_signature:
        raise ValueError("KMS sign returned empty Signature")

    print(f"   Raw DER Signature Length: {len(raw_signature)} bytes")

    # Strict DER decoding
    r, s = decode_der_ecdsa_signature(bytes(raw_signature))
    print(f"   Decoded r: 0x{r:064x}")
    print(f"   Decoded s: 0x{s:064x}")

    # Canonical low-S normalization
    original_s_was_high = s > SECP256K1_HALF_N
    if original_s_was_high:
        s = SECP256K1_N - s
        print(f"   [NORMALIZED] Signature was high-S, normalized s: 0x{s:064x}")
    else:
        print("   [PASS] Signature was already canonical low-S")

    # Deterministic recovery-ID determination
    rec_id = determine_recovery_id(DETERMINISTIC_DIGEST, r, s, uncompressed_pub[1:])
    print(f"   Determined Recovery ID (v): {rec_id}")

    # Cryptographic recovery check
    from eth_keys import datatypes as keys_datatypes
    cand_sig = keys_datatypes.Signature(vrs=(rec_id, r, s))
    recovered_pub = cand_sig.recover_public_key_from_msg_hash(DETERMINISTIC_DIGEST)
    recovered_addr = Web3.to_checksum_address(Web3.keccak(recovered_pub.to_bytes())[-20:])

    if recovered_addr.lower() != derived_address.lower():
        raise ValueError(f"Recovered address {recovered_addr} does not match KMS address {derived_address}")
    print(f"   [PASS] Recovered Address: {recovered_addr} matches KMS derived address")

    # AWS KMS Verify API call
    print("\n4. Verifying Signature with AWS KMS (Verify)...")
    verify_resp = client.verify(
        KeyId=key_arn,
        Message=DETERMINISTIC_DIGEST,
        MessageType="DIGEST",
        SigningAlgorithm="ECDSA_SHA_256",
        Signature=raw_signature,
    )
    sig_valid = verify_resp.get("SignatureValid", False)
    if not sig_valid:
        raise ValueError("AWS KMS Verify returned SignatureValid=False")
    print("   [PASS] AWS KMS Verify confirmed signature is valid.")

    results["cryptographic_verification"] = {
        "digest": "0x" + DETERMINISTIC_DIGEST.hex(),
        "der_signature_length": len(raw_signature),
        "original_s_was_high": original_s_was_high,
        "normalized_s_low": s <= SECP256K1_HALF_N,
        "recovery_id": rec_id,
        "recovered_address": recovered_addr,
        "kms_verify_api_result": sig_valid,
    }

    # Verify AwsKmsSigner wrapper
    print("\n5. Verifying AwsKmsSigner wrapper & SignerFactory...")
    signer = SignerFactory.create_signer(
        environment=SignerEnvironment.PRODUCTION,
        signer_type=SignerType.PRODUCTION_AWS_KMS,
        chain_id=CHAIN_ID_BASE_SEPOLIA,
        key_reference=key_arn,
        expected_address=derived_address,
        client=client,
    )
    assert signer.health_check() is True
    assert signer.get_address() == derived_address
    print("   [PASS] AwsKmsSigner initialized and passed health_check().")

    # Section 10: Relayer startup validation
    print("\n6. Verifying BlockchainRelayer startup with live AwsKmsSigner...")
    from unittest.mock import AsyncMock
    from app.services.blockchain.nonce_manager import NonceManager
    from app.services.blockchain.gas_estimator import GasEstimator
    import asyncio

    mock_rpc = AsyncMock()
    mock_rpc.verify_chain_id.return_value = CHAIN_ID_BASE_SEPOLIA
    cfg = get_chain_config(CHAIN_ID_BASE_SEPOLIA)
    relayer = BlockchainRelayer(
        config=cfg,
        rpc_client=mock_rpc,
        nonce_manager=NonceManager(mock_rpc),
        gas_estimator=GasEstimator(mock_rpc),
        signer=signer,
    )
    asyncio.run(relayer.verify_startup())
    print("   [PASS] BlockchainRelayer.verify_startup() passed pre-flight checks.")

    # Section 11: Negative Identity Test
    print("\n7. Executing Negative Identity Test (intentional address mismatch)...")
    wrong_address = "0x1111111111111111111111111111111111111111"
    try:
        bad_signer = SignerFactory.create_signer(
            environment=SignerEnvironment.PRODUCTION,
            signer_type=SignerType.PRODUCTION_AWS_KMS,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            key_reference=key_arn,
            expected_address=wrong_address,
            client=client,
        )
        bad_signer.health_check()
        raise AssertionError("Expected SignerIdentityMismatchError was not raised!")
    except SignerIdentityMismatchError:
        print("   [PASS] SignerIdentityMismatchError raised as expected on identity mismatch.")

    # Section 12: Negative Network Test
    print("\n8. Executing Negative Network Test (intentional invalid chain & Mainnet block)...")
    from app.services.blockchain.config import CHAIN_ID_BASE_MAINNET
    from app.services.blockchain.errors import MainnetSubmissionBlockedError
    try:
        SignerFactory.create_signer(
            environment=SignerEnvironment.PRODUCTION,
            signer_type=SignerType.PRODUCTION_AWS_KMS,
            chain_id=CHAIN_ID_BASE_MAINNET,
            key_reference=key_arn,
            expected_address=derived_address,
            client=client,
        )
        raise AssertionError("Expected MainnetSubmissionBlockedError was not raised!")
    except MainnetSubmissionBlockedError:
        print("   [PASS] MainnetSubmissionBlockedError raised as expected for Base Mainnet.")

    results["signer_factory_check"] = "PASS"
    results["relayer_startup_check"] = "PASS"
    results["negative_identity_test"] = "PASS"
    results["negative_network_test"] = "PASS"
    results["status"] = "PASS"
    return results


def main() -> int:
    print("======================================================================")
    print(" AgentChain Phase 6.10.4: Live AWS KMS Verification & Signer Attestation")
    print("======================================================================")

    ready, arn, region, expected_address, msg = check_prerequisites()

    if not ready:
        print(f"\n[FAIL-CLOSED] Live AWS KMS prerequisites unavailable: {msg}")
        print("\nOperator Requirements for Live AWS KMS Verification:")
        print("  1. AWS_KMS_KEY_ARN: ARN of an existing AWS KMS key with:")
        print("       KeySpec:  ECC_SECG_P256K1")
        print("       KeyUsage: SIGN_VERIFY")
        print("       KeyState: Enabled")
        print("  2. AWS_REGION: AWS region (e.g. us-east-1)")
        print("  3. EXPECTED_SIGNER_ADDRESS: The expected derived Ethereum checksum address")
        print("  4. Ambient AWS credentials (AWS profile, IAM role, or SSO session)")
        print("\nNo AWS resources will be created. Zero blockchain transactions will be broadcast.")
        print("\nResult: LIVE AWS KMS VERIFICATION: NOT EXECUTED")
        return 2

    try:
        results = verify_live_kms(arn, region, expected_address or None)
        print("\n======================================================================")
        print(" SUCCESS: Real AWS KMS Cryptographic Verification Passed!")
        print("======================================================================")
        print(f" Derived Signer Address: {results['public_key_checks']['derived_ethereum_address']}")
        print(f" Status: LIVE AWS KMS PROVIDER: PASS")
        return 0
    except Exception as exc:
        print(f"\n[ERROR] Live KMS verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
