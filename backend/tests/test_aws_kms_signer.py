"""Phase 6.10.1 — AWS KMS Production Signer & Cryptographic Verification Suite.

Comprehensive test suite verifying:
1. KMS Key Metadata Validation:
   - KeySpec ECC_SECG_P256K1 required; non-secp256k1 rejected.
   - KeyUsage SIGN_VERIFY required; encryption/other usage rejected.
   - KeyState Enabled required; disabled/pending deletion rejected.
2. Public Key Extraction & Address Derivation:
   - Strict DER X.509 SPKI parsing.
   - Rejection of invalid curves (e.g. NIST P-256) and malformed headers.
   - Derivation of Ethereum address via Keccak-256 of uncompressed public key.
3. Strict DER Signature Decoder:
   - ASN.1 SEQUENCE and exactly two INTEGERs (r, s).
   - Rejection of malformed lengths, negative integers, zero values, >= N, and trailing garbage.
4. Low-S Canonical Normalization:
   - Strict enforcement: s <= N // 2.
   - Normalization of high-S (s = N - s).
5. Deterministic Recovery-ID Determination:
   - Deterministic candidate testing against KMS public key.
   - Verification that recovered public key and address match KMS derived values.
6. Identity Binding:
   - EXPECTED_SIGNER_ADDRESS binding enforcement.
   - Fail-closed with SignerIdentityMismatchError on mismatch.
   - Hard block on Anvil/Hardhat development addresses.
7. Network Binding:
   - Base Sepolia (84532) accepted when fully configured.
   - Base Mainnet (8453) hard blocked under current safety gate.
   - Anvil (31337) and invalid chains rejected.
8. Mocked KMS Integration:
   - Deterministic integration test with mocked AWS KMS client.
   - Full transaction signing (EIP-1559 and Legacy).
   - Proof of digest signing without double-hashing (MessageType="DIGEST").
   - Verification via Account.recover_transaction.
"""

from typing import Any
import pytest
from unittest.mock import AsyncMock, patch
from eth_account import Account
from eth_account._utils.signing import serializable_unsigned_transaction_from_dict
from eth_keys import datatypes as keys_datatypes
from web3 import Web3

from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
    get_chain_config,
)
from app.services.blockchain.relayer import BlockchainRelayer
from app.services.blockchain.nonce_manager import NonceManager
from app.services.blockchain.gas_estimator import GasEstimator
from app.services.blockchain.errors import (
    MainnetSubmissionBlockedError,
    ProductionSignerUnavailableError,
    SecurityConfigurationError,
    SignerIdentityMismatchError,
    SignerNetworkMismatchError,
)
from app.services.blockchain.signer import (
    ANVIL_DEFAULT_ADDRESS,
    SECP256K1_HALF_N,
    SECP256K1_N,
    AwsKmsClientProtocol,
    AwsKmsSigner,
    SignerEnvironment,
    SignerFactory,
    SignerType,
    decode_der_ecdsa_signature,
    derive_ethereum_address_from_public_key,
    determine_recovery_id,
    extract_uncompressed_public_key_from_spki,
)


# ==============================================================================
# TEST FIXTURES & DETERMINISTIC MOCK AWS KMS CLIENT
# ==============================================================================

# Deterministic private key fixture for local mock testing
DETERMINISTIC_TEST_KEY_HEX = "0x703f4e6283731359f0f06c02ee4753a30f6424098e6b45aca799567b664c755d"
DETERMINISTIC_TEST_ADDR = "0x473888C859F88D3De7b5A20986805b4dC3178189"
VALID_KMS_KEY_ARN = "arn:aws:kms:us-east-1:123456789012:key/12345678-1234-1234-1234-123456789012"


def _encode_der_signature(r: int, s: int) -> bytes:
    """Helper to construct strict DER ASN.1 SEQUENCE of two INTEGERs."""
    def _enc_int(val: int) -> bytes:
        if val == 0:
            b = b"\x00"
        else:
            b = val.to_bytes((val.bit_length() + 7) // 8, "big")
            if b[0] & 0x80:
                b = b"\x00" + b
        return b"\x02" + bytes([len(b)]) + b

    rb = _enc_int(r)
    sb = _enc_int(s)
    body = rb + sb
    return b"\x30" + bytes([len(body)]) + body


def _build_spki_for_public_key(raw_uncompressed_65: bytes) -> bytes:
    """Helper to construct canonical 88-byte X.509 SPKI DER for secp256k1."""
    if len(raw_uncompressed_65) != 65 or raw_uncompressed_65[0] != 0x04:
        raise ValueError("Must be 65 bytes starting with 0x04")
    # id-ecPublicKey + secp256k1 algorithm identifier + bit string header
    header = bytes.fromhex("3056301006072a8648ce3d020106052b8104000a034200")
    return header + raw_uncompressed_65


class MockAwsKmsClient(AwsKmsClientProtocol):
    """Deterministic fake implementing AwsKmsClientProtocol for unit and integration testing.

    Cannot produce fake 'live' evidence; operates purely locally with recorded calls.
    """

    def __init__(
        self,
        private_key_hex: str = DETERMINISTIC_TEST_KEY_HEX,
        key_spec: str = "ECC_SECG_P256K1",
        key_usage: str = "SIGN_VERIFY",
        key_state: str = "Enabled",
        force_high_s: bool = False,
    ) -> None:
        self.account = Account.from_key(private_key_hex)
        self.raw_uncompressed_pub = b"\x04" + self.account._key_obj.public_key.to_bytes()
        self.spki = _build_spki_for_public_key(self.raw_uncompressed_pub)
        self.key_spec = key_spec
        self.key_usage = key_usage
        self.key_state = key_state
        self.force_high_s = force_high_s
        self.calls_log: list[dict[str, Any]] = []

    def describe_key(self, **kwargs: Any) -> dict[str, Any]:
        self.calls_log.append({"method": "describe_key", "kwargs": kwargs})
        return {
            "KeyMetadata": {
                "KeyId": kwargs.get("KeyId", VALID_KMS_KEY_ARN),
                "KeySpec": self.key_spec,
                "KeyUsage": self.key_usage,
                "KeyState": self.key_state,
                "Arn": VALID_KMS_KEY_ARN,
            }
        }

    def get_public_key(self, **kwargs: Any) -> dict[str, Any]:
        self.calls_log.append({"method": "get_public_key", "kwargs": kwargs})
        return {
            "KeyId": kwargs.get("KeyId", VALID_KMS_KEY_ARN),
            "PublicKey": self.spki,
            "CustomerMasterKeySpec": self.key_spec,
            "KeySpec": self.key_spec,
            "KeyUsage": self.key_usage,
        }

    def sign(self, **kwargs: Any) -> dict[str, Any]:
        self.calls_log.append({"method": "sign", "kwargs": kwargs})
        message = kwargs.get("Message")
        msg_type = kwargs.get("MessageType")
        signing_algo = kwargs.get("SigningAlgorithm")

        if msg_type != "DIGEST":
            raise ValueError(f"Mock KMS expects MessageType='DIGEST', got '{msg_type}'")
        if signing_algo != "ECDSA_SHA_256":
            raise ValueError(f"Mock KMS expects SigningAlgorithm='ECDSA_SHA_256', got '{signing_algo}'")
        if not isinstance(message, (bytes, bytearray)) or len(message) != 32:
            raise ValueError(f"Mock KMS expects 32-byte digest, got {len(message) if message else 0} bytes")

        # Sign with local key
        sig = self.account._key_obj.sign_msg_hash(message)
        r = sig.r
        s = sig.s

        # Optionally force high-S signature to test normalization
        if self.force_high_s:
            if s <= SECP256K1_HALF_N:
                s = SECP256K1_N - s
        else:
            if s > SECP256K1_HALF_N:
                s = SECP256K1_N - s

        der = _encode_der_signature(r, s)
        return {
            "KeyId": kwargs.get("KeyId", VALID_KMS_KEY_ARN),
            "Signature": der,
            "SigningAlgorithm": signing_algo,
        }

    def verify(self, **kwargs: Any) -> dict[str, Any]:
        self.calls_log.append({"method": "verify", "kwargs": kwargs})
        return {"SignatureValid": True}


# ==============================================================================
# 1. KEY METADATA TESTS
# ==============================================================================

class TestKmsKeyMetadata:
    def test_valid_secp256k1_metadata_accepted(self):
        client = MockAwsKmsClient(key_spec="ECC_SECG_P256K1", key_usage="SIGN_VERIFY", key_state="Enabled")
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            client=client,
        )
        meta = signer.validate_key_metadata()
        assert meta["KeySpec"] == "ECC_SECG_P256K1"
        assert meta["KeyUsage"] == "SIGN_VERIFY"
        assert meta["KeyState"] == "Enabled"
        assert signer.health_check() is True

    @pytest.mark.parametrize("wrong_spec", ["RSA_2048", "RSA_3072", "ECC_NIST_P256", "ECC_NIST_P384", "ECC_NIST_P521", "SYMMETRIC_DEFAULT"])
    def test_wrong_keyspec_rejected(self, wrong_spec: str):
        client = MockAwsKmsClient(key_spec=wrong_spec)
        with pytest.raises(SecurityConfigurationError) as excinfo:
            AwsKmsSigner(
                key_reference=VALID_KMS_KEY_ARN,
                chain_id=CHAIN_ID_BASE_SEPOLIA,
                client=client,
            )
        assert "Unsupported KMS KeySpec" in str(excinfo.value)
        assert "ECC_SECG_P256K1" in str(excinfo.value)

    @pytest.mark.parametrize("wrong_usage", ["ENCRYPT_DECRYPT", "GENERATE_VERIFY_MAC"])
    def test_wrong_keyusage_rejected(self, wrong_usage: str):
        client = MockAwsKmsClient(key_usage=wrong_usage)
        with pytest.raises(SecurityConfigurationError) as excinfo:
            AwsKmsSigner(
                key_reference=VALID_KMS_KEY_ARN,
                chain_id=CHAIN_ID_BASE_SEPOLIA,
                client=client,
            )
        assert "Unsupported KMS KeyUsage" in str(excinfo.value)
        assert "SIGN_VERIFY" in str(excinfo.value)

    @pytest.mark.parametrize("bad_state", ["Disabled", "PendingDeletion", "PendingImport", "Unavailable"])
    def test_invalid_key_state_rejected(self, bad_state: str):
        client = MockAwsKmsClient(key_state=bad_state)
        with pytest.raises(SecurityConfigurationError) as excinfo:
            AwsKmsSigner(
                key_reference=VALID_KMS_KEY_ARN,
                chain_id=CHAIN_ID_BASE_SEPOLIA,
                client=client,
            )
        assert f"is in state '{bad_state}'" in str(excinfo.value)
        assert "Enabled" in str(excinfo.value)


# ==============================================================================
# 2. PUBLIC KEY & SPKI PARSING TESTS
# ==============================================================================

class TestKmsPublicKeyExtraction:
    def test_spki_parsing_and_address_derivation(self):
        acc = Account.from_key(DETERMINISTIC_TEST_KEY_HEX)
        raw_pub = b"\x04" + acc._key_obj.public_key.to_bytes()
        spki = _build_spki_for_public_key(raw_pub)

        extracted = extract_uncompressed_public_key_from_spki(spki)
        assert extracted == raw_pub

        derived_addr = derive_ethereum_address_from_public_key(extracted)
        assert derived_addr == DETERMINISTIC_TEST_ADDR
        assert derived_addr == acc.address

    def test_malformed_spki_rejected(self):
        # Truncated SPKI
        with pytest.raises(ValueError, match="too short"):
            extract_uncompressed_public_key_from_spki(b"\x30\x10\x00")

        # Not ASN.1 sequence
        with pytest.raises(ValueError, match="expected ASN.1 SEQUENCE"):
            extract_uncompressed_public_key_from_spki(b"\x04" * 88)

    def test_unsupported_curve_spki_rejected(self):
        # Construct SPKI with NIST P-256 curve OID: 1.2.840.10045.3.1.7 (06 08 2a 86 48 ce 3d 03 01 07)
        acc = Account.from_key(DETERMINISTIC_TEST_KEY_HEX)
        raw_pub = b"\x04" + acc._key_obj.public_key.to_bytes()
        p256_spki = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200") + raw_pub

        with pytest.raises(ValueError, match="curve is not secp256k1"):
            extract_uncompressed_public_key_from_spki(p256_spki)


# ==============================================================================
# 3. DER SIGNATURE DECODER TESTS
# ==============================================================================

class TestDerSignatureDecoder:
    def test_valid_der_signature_decoding(self):
        r = 0x1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF
        s = 0x0FEDCBA9876543210FEDCBA9876543210FEDCBA9876543210FEDCBA987654321
        der = _encode_der_signature(r, s)
        dec_r, dec_s = decode_der_ecdsa_signature(der)
        assert dec_r == r
        assert dec_s == s

    def test_der_decoder_rejects_negative_r(self):
        # Construct integer with MSB set (negative) without 0x00 padding
        raw_neg_r = bytes.fromhex("8000000000000000000000000000000000000000000000000000000000000001")
        int_r = b"\x02\x20" + raw_neg_r
        int_s = b"\x02\x01\x01"
        seq = b"\x30" + bytes([len(int_r) + len(int_s)]) + int_r + int_s

        with pytest.raises(ValueError, match="Negative integer in DER"):
            decode_der_ecdsa_signature(seq)

    def test_der_decoder_rejects_negative_s(self):
        raw_neg_s = bytes.fromhex("ff00000000000000000000000000000000000000000000000000000000000001")
        int_r = b"\x02\x01\x01"
        int_s = b"\x02\x20" + raw_neg_s
        seq = b"\x30" + bytes([len(int_r) + len(int_s)]) + int_r + int_s

        with pytest.raises(ValueError, match="Negative integer in DER"):
            decode_der_ecdsa_signature(seq)

    def test_der_decoder_rejects_zero_values(self):
        # r = 0
        der_zero_r = _encode_der_signature(0, 10)
        with pytest.raises(ValueError, match="r value 0 out of valid secp256k1 range"):
            decode_der_ecdsa_signature(der_zero_r)

        # s = 0
        der_zero_s = _encode_der_signature(10, 0)
        with pytest.raises(ValueError, match="s value 0 out of valid secp256k1 range"):
            decode_der_ecdsa_signature(der_zero_s)

    def test_der_decoder_rejects_values_greater_or_equal_to_curve_order(self):
        # r >= N
        der_large_r = _encode_der_signature(SECP256K1_N, 10)
        with pytest.raises(ValueError, match="out of valid secp256k1 range"):
            decode_der_ecdsa_signature(der_large_r)

        # s >= N
        der_large_s = _encode_der_signature(10, SECP256K1_N + 5)
        with pytest.raises(ValueError, match="out of valid secp256k1 range"):
            decode_der_ecdsa_signature(der_large_s)

    def test_der_decoder_rejects_trailing_garbage(self):
        valid_der = _encode_der_signature(10, 20)
        with pytest.raises(ValueError, match="sequence length mismatch"):
            decode_der_ecdsa_signature(valid_der + b"\x00")

    def test_der_decoder_rejects_excessive_zero_padding(self):
        # Redundant 0x00 when bit 7 is not set
        redundant_int = b"\x02\x02\x00\x01"  # 0x0001 is invalid DER; must be 0x01
        int_s = b"\x02\x01\x01"
        seq = b"\x30" + bytes([len(redundant_int) + len(int_s)]) + redundant_int + int_s

        with pytest.raises(ValueError, match="Excessive leading zero padding"):
            decode_der_ecdsa_signature(seq)


# ==============================================================================
# 4. LOW-S ENFORCEMENT & RECOVERY TESTS
# ==============================================================================

class TestLowSEnforcementAndRecovery:
    def test_already_low_s_signature_preserved(self):
        acc = Account.from_key(DETERMINISTIC_TEST_KEY_HEX)
        digest = Web3.keccak(text="agentchain_low_s_test")
        sig = acc._key_obj.sign_msg_hash(digest)

        r = sig.r
        s = sig.s
        if s > SECP256K1_HALF_N:
            s = SECP256K1_N - s

        rec_id = determine_recovery_id(digest, r, s, acc._key_obj.public_key.to_bytes())
        assert rec_id in (0, 1)

    def test_high_s_signature_normalized_correctly(self):
        acc = Account.from_key(DETERMINISTIC_TEST_KEY_HEX)
        digest = Web3.keccak(text="agentchain_high_s_test")
        sig = acc._key_obj.sign_msg_hash(digest)

        r = sig.r
        low_s = sig.s if sig.s <= SECP256K1_HALF_N else SECP256K1_N - sig.s
        high_s = SECP256K1_N - low_s

        assert high_s > SECP256K1_HALF_N

        # Normalize
        normalized_s = SECP256K1_N - high_s
        assert normalized_s <= SECP256K1_HALF_N
        assert normalized_s == low_s

        rec_id = determine_recovery_id(digest, r, normalized_s, acc._key_obj.public_key.to_bytes())
        assert rec_id in (0, 1)

        cand_sig = keys_datatypes.Signature(vrs=(rec_id, r, normalized_s))
        recovered_pub = cand_sig.recover_public_key_from_msg_hash(digest)
        assert recovered_pub.to_bytes() == acc._key_obj.public_key.to_bytes()

    def test_recovery_id_mismatch_raises(self):
        # Mismatched public key
        other_acc = Account.create()
        digest = Web3.keccak(text="mismatch_test")
        acc = Account.from_key(DETERMINISTIC_TEST_KEY_HEX)
        sig = acc._key_obj.sign_msg_hash(digest)

        with pytest.raises(ValueError, match="do not match KMS public key"):
            determine_recovery_id(digest, sig.r, sig.s, other_acc._key_obj.public_key.to_bytes())


# ==============================================================================
# 5. IDENTITY & ADDRESS BINDING TESTS
# ==============================================================================

class TestSignerIdentityBinding:
    def test_matching_expected_signer_address_passes(self):
        client = MockAwsKmsClient()
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            expected_address=DETERMINISTIC_TEST_ADDR,
            client=client,
        )
        assert signer.get_address() == DETERMINISTIC_TEST_ADDR

    def test_mismatched_expected_signer_address_fails_closed(self):
        client = MockAwsKmsClient()
        wrong_address = "0x1111111111111111111111111111111111111111"

        with pytest.raises(SignerIdentityMismatchError) as excinfo:
            AwsKmsSigner(
                key_reference=VALID_KMS_KEY_ARN,
                chain_id=CHAIN_ID_BASE_SEPOLIA,
                expected_address=wrong_address,
                client=client,
            )
        assert "does not match expected address" in str(excinfo.value)

    def test_blocked_development_address_rejected(self):
        # Default Anvil account key
        anvil_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        client = MockAwsKmsClient(private_key_hex=anvil_key)

        with pytest.raises(SecurityConfigurationError) as excinfo:
            AwsKmsSigner(
                key_reference=VALID_KMS_KEY_ARN,
                chain_id=CHAIN_ID_BASE_SEPOLIA,
                client=client,
            )
        assert "blocked development account" in str(excinfo.value)

    def test_malformed_expected_address_format_rejected(self):
        client = MockAwsKmsClient()
        with pytest.raises(SecurityConfigurationError, match="Invalid expected_signer_address format"):
            AwsKmsSigner(
                key_reference=VALID_KMS_KEY_ARN,
                chain_id=CHAIN_ID_BASE_SEPOLIA,
                expected_address="0xNotAnAddress",
                client=client,
            )


# ==============================================================================
# 6. NETWORK BINDING TESTS
# ==============================================================================

class TestSignerNetworkBinding:
    def test_base_sepolia_network_accepted(self):
        client = MockAwsKmsClient()
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            client=client,
        )
        assert signer.allowed_chains == frozenset({CHAIN_ID_BASE_SEPOLIA})
        signer.verify_network_compatibility(CHAIN_ID_BASE_SEPOLIA)

    def test_base_mainnet_hard_blocked_at_construction(self):
        client = MockAwsKmsClient()
        with pytest.raises(MainnetSubmissionBlockedError) as excinfo:
            AwsKmsSigner(
                key_reference=VALID_KMS_KEY_ARN,
                chain_id=CHAIN_ID_BASE_MAINNET,
                client=client,
            )
        assert "MUST NOT be configured for Base Mainnet" in str(excinfo.value)

    def test_anvil_rejected_at_construction(self):
        client = MockAwsKmsClient()
        with pytest.raises(SignerNetworkMismatchError) as excinfo:
            AwsKmsSigner(
                key_reference=VALID_KMS_KEY_ARN,
                chain_id=CHAIN_ID_ANVIL,
                client=client,
            )
        assert "cannot be used on local development network" in str(excinfo.value)

    def test_arbitrary_unsupported_chain_rejected(self):
        client = MockAwsKmsClient()
        with pytest.raises(SignerNetworkMismatchError) as excinfo:
            AwsKmsSigner(
                key_reference=VALID_KMS_KEY_ARN,
                chain_id=1,  # Ethereum Mainnet
                client=client,
            )
        assert "is restricted to Base Sepolia" in str(excinfo.value)

    def test_sign_transaction_for_wrong_chain_rejected(self):
        client = MockAwsKmsClient()
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            client=client,
        )
        wrong_tx = {
            "to": "0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1",
            "value": 0,
            "gas": 21000,
            "maxFeePerGas": 1000000000,
            "maxPriorityFeePerGas": 1000000000,
            "nonce": 0,
            "chainId": 1,  # Mismatched
            "type": 2,
        }
        with pytest.raises(SignerNetworkMismatchError):
            signer.sign_transaction(wrong_tx)

    def test_sign_transaction_for_mainnet_rejected(self):
        client = MockAwsKmsClient()
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            client=client,
        )
        mainnet_tx = {
            "to": "0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1",
            "value": 0,
            "gas": 21000,
            "maxFeePerGas": 1000000000,
            "maxPriorityFeePerGas": 1000000000,
            "nonce": 0,
            "chainId": CHAIN_ID_BASE_MAINNET,
            "type": 2,
        }
        with pytest.raises(MainnetSubmissionBlockedError):
            signer.sign_transaction(mainnet_tx)


# ==============================================================================
# 7. MOCKED KMS INTEGRATION & PROOF OF NO DOUBLE-HASHING
# ==============================================================================

class TestMockedKmsIntegration:
    def test_eip1559_transaction_signing_integration(self):
        client = MockAwsKmsClient(force_high_s=True)  # Force high-S to test low-S normalization in pipeline
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            expected_address=DETERMINISTIC_TEST_ADDR,
            client=client,
        )

        tx_dict = {
            "to": "0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1",
            "value": 1000000,
            "gas": 65000,
            "maxFeePerGas": 2000000000,
            "maxPriorityFeePerGas": 1000000000,
            "nonce": 42,
            "chainId": CHAIN_ID_BASE_SEPOLIA,
            "type": 2,
        }

        # Sign transaction
        signed_raw_hex = signer.sign_transaction(tx_dict)
        assert signed_raw_hex.startswith("0x")

        # Verify KMS client call semantics
        sign_calls = [c for c in client.calls_log if c["method"] == "sign"]
        assert len(sign_calls) == 1
        sign_args = sign_calls[0]["kwargs"]
        assert sign_args["MessageType"] == "DIGEST"
        assert sign_args["SigningAlgorithm"] == "ECDSA_SHA_256"

        # Verify that digest sent to KMS was exactly the Keccak-256 hash of unsigned tx (no double hashing)
        unsigned_tx = serializable_unsigned_transaction_from_dict(tx_dict)
        expected_tx_hash = unsigned_tx.hash()
        assert sign_args["Message"] == expected_tx_hash

        # Cryptographically recover on-chain signer address using standard Ethereum recovery
        recovered_signer = Account.recover_transaction(signed_raw_hex)
        assert recovered_signer.lower() == DETERMINISTIC_TEST_ADDR.lower()
        assert recovered_signer.lower() == signer.get_address().lower()

    def test_legacy_transaction_signing_integration(self):
        client = MockAwsKmsClient()
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            expected_address=DETERMINISTIC_TEST_ADDR,
            client=client,
        )

        tx_dict = {
            "to": "0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1",
            "value": 500000,
            "gas": 45000,
            "gasPrice": 2000000000,
            "nonce": 43,
            "chainId": CHAIN_ID_BASE_SEPOLIA,
        }

        signed_raw_hex = signer.sign_transaction(tx_dict)
        recovered_signer = Account.recover_transaction(signed_raw_hex)
        assert recovered_signer.lower() == DETERMINISTIC_TEST_ADDR.lower()

    def test_digest_signing_without_double_hashing(self):
        client = MockAwsKmsClient()
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            expected_address=DETERMINISTIC_TEST_ADDR,
            client=client,
        )

        arbitrary_digest = Web3.keccak(text="direct_digest_no_double_hash")
        v, r, s = signer.sign_digest(arbitrary_digest)

        # Ensure low-S
        assert s <= SECP256K1_HALF_N
        assert v in (0, 1)

        # Check call received exactly the digest without internal hash
        sign_calls = [c for c in client.calls_log if c["method"] == "sign"]
        assert sign_calls[-1]["kwargs"]["Message"] == arbitrary_digest
        assert sign_calls[-1]["kwargs"]["MessageType"] == "DIGEST"

        # Recover public key directly from the signed digest
        sig = keys_datatypes.Signature(vrs=(v, r, s))
        recovered_pub = sig.recover_public_key_from_msg_hash(arbitrary_digest)
        recovered_addr = Web3.to_checksum_address(Web3.keccak(recovered_pub.to_bytes())[-20:])
        assert recovered_addr.lower() == DETERMINISTIC_TEST_ADDR.lower()


# ==============================================================================
# 8. SIGNER FACTORY INTEGRATION
# ==============================================================================

class TestSignerFactoryKms:
    def test_signer_factory_creates_aws_kms_signer_with_client(self):
        client = MockAwsKmsClient()
        signer = SignerFactory.create_signer(
            environment=SignerEnvironment.PRODUCTION,
            signer_type=SignerType.PRODUCTION_AWS_KMS,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            key_reference=VALID_KMS_KEY_ARN,
            expected_address=DETERMINISTIC_TEST_ADDR,
            client=client,
        )
        assert isinstance(signer, AwsKmsSigner)
        assert signer.get_address().lower() == DETERMINISTIC_TEST_ADDR.lower()

    def test_signer_factory_string_type_aws_kms(self):
        client = MockAwsKmsClient()
        signer = SignerFactory.create_signer(
            environment="production",
            signer_type="aws_kms",
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            key_reference=VALID_KMS_KEY_ARN,
            expected_address=DETERMINISTIC_TEST_ADDR,
            client=client,
        )
        assert isinstance(signer, AwsKmsSigner)
        assert signer.get_address().lower() == DETERMINISTIC_TEST_ADDR.lower()


# ==============================================================================
# 9. RELAYER KMS INTEGRATION TESTS
# ==============================================================================

class TestRelayerKmsIntegration:
    @pytest.mark.asyncio
    async def test_relayer_startup_with_aws_kms_signer(self):
        client = MockAwsKmsClient()
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            expected_address=DETERMINISTIC_TEST_ADDR,
            client=client,
        )

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

        # verify_startup should pass with valid AwsKmsSigner
        await relayer.verify_startup()
        assert relayer.signer.get_address() == DETERMINISTIC_TEST_ADDR

    @pytest.mark.asyncio
    async def test_relayer_startup_fails_if_kms_signer_unhealthy(self):
        client = MockAwsKmsClient(key_state="Disabled")
        # Initialize with validate_on_init=False to test relayer startup catch
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            client=client,
            validate_on_init=False,
        )

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

        with pytest.raises(RuntimeError, match="Signer health_check.*returned False"):
            await relayer.verify_startup()

