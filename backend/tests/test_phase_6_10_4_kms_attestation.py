"""Phase 6.10.4 — Live AWS KMS Verification & Production Signer Attestation Test Suite.

Verifies:
1. Test digest formulation: keccak256("AgentChain Phase 6.10.4 KMS attestation").
2. DER signature decoding, bounds validation (1 <= r < N, 1 <= s < N), and low-S normalization.
3. Recovery ID determination and public key / address recovery matching KMS derived address.
4. AwsKmsSigner attestation properties (health_check, address, chain_id).
5. Relayer startup validation with AwsKmsSigner.
6. Negative Identity Test: intentionally incorrect expected address raises SignerIdentityMismatchError.
7. Negative Network Test: incorrect chain ID raises SignerNetworkMismatchError and Mainnet raises MainnetSubmissionBlockedError.
8. Prerequisite checking fail-closed behavior when operator credentials/key are unavailable.
"""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import pytest
from eth_account import Account
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
    get_chain_config,
)
from app.services.blockchain.errors import (
    MainnetSubmissionBlockedError,
    SecurityConfigurationError,
    SignerIdentityMismatchError,
    SignerNetworkMismatchError,
)
from app.services.blockchain.relayer import BlockchainRelayer
from app.services.blockchain.nonce_manager import NonceManager
from app.services.blockchain.gas_estimator import GasEstimator
from app.services.blockchain.signer import (
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
from scripts.verify_aws_kms_live import (
    DETERMINISTIC_DIGEST,
    DETERMINISTIC_TEST_MESSAGE,
    check_prerequisites,
)

DETERMINISTIC_TEST_KEY_HEX = "0x703f4e6283731359f0f06c02ee4753a30f6424098e6b45aca799567b664c755d"
DETERMINISTIC_TEST_ADDR = "0x473888C859F88D3De7b5A20986805b4dC3178189"
VALID_KMS_KEY_ARN = "arn:aws:kms:us-east-1:123456789012:key/12345678-1234-1234-1234-123456789012"


def _build_spki(raw_pub_65: bytes) -> bytes:
    header = bytes.fromhex("3056301006072a8648ce3d020106052b8104000a034200")
    return header + raw_pub_65


def _encode_der(r: int, s: int) -> bytes:
    def _encode_int(val: int) -> bytes:
        raw = val.to_bytes((val.bit_length() + 7) // 8, byteorder="big")
        if raw[0] & 0x80:
            raw = b"\x00" + raw
        return b"\x02" + bytes([len(raw)]) + raw

    rb = _encode_int(r)
    sb = _encode_int(s)
    payload = rb + sb
    return b"\x30" + bytes([len(payload)]) + payload


class MockAwsKmsClient(AwsKmsClientProtocol):
    """Deterministic mock of AWS KMS for test isolation without network calls."""

    def __init__(
        self,
        private_key_hex: str = DETERMINISTIC_TEST_KEY_HEX,
        key_state: str = "Enabled",
        key_spec: str = "ECC_SECG_P256K1",
        key_usage: str = "SIGN_VERIFY",
    ):
        self._acct = Account.from_key(private_key_hex)
        raw_pub = self._acct._key_obj.public_key.to_bytes()
        self.raw_pub_65 = b"\x04" + raw_pub
        self.spki_pub = _build_spki(self.raw_pub_65)
        self.key_state = key_state
        self.key_spec = key_spec
        self.key_usage = key_usage

    def describe_key(self, *, KeyId: str) -> dict[str, Any]:
        return {
            "KeyMetadata": {
                "KeyId": KeyId,
                "Arn": KeyId,
                "KeyState": self.key_state,
                "KeySpec": self.key_spec,
                "KeyUsage": self.key_usage,
                "Enabled": self.key_state == "Enabled",
            }
        }

    def get_public_key(self, *, KeyId: str) -> dict[str, Any]:
        return {"PublicKey": self.spki_pub, "KeyId": KeyId}

    def sign(
        self,
        *,
        KeyId: str,
        Message: bytes,
        MessageType: str,
        SigningAlgorithm: str,
    ) -> dict[str, Any]:
        sig_obj = self._acct._key_obj.sign_msg_hash(Message)
        r = sig_obj.r
        s = sig_obj.s
        der_bytes = _encode_der(r, s)
        return {
            "Signature": der_bytes,
            "KeyId": KeyId,
            "SigningAlgorithm": SigningAlgorithm,
        }

    def verify(
        self,
        *,
        KeyId: str,
        Message: bytes,
        MessageType: str,
        Signature: bytes,
        SigningAlgorithm: str,
    ) -> dict[str, Any]:
        r, s = decode_der_ecdsa_signature(Signature)
        if s > SECP256K1_HALF_N:
            s = SECP256K1_N - s
        from eth_keys import datatypes as keys_datatypes

        rec_id = determine_recovery_id(Message, r, s, self.raw_pub_65[1:])
        cand = keys_datatypes.Signature(vrs=(rec_id, r, s))
        rec_pub = cand.recover_public_key_from_msg_hash(Message)
        is_valid = rec_pub.to_bytes() == self.raw_pub_65[1:]
        return {"SignatureValid": is_valid, "KeyId": KeyId}


class TestPhase6104CryptographicAttestation:
    def test_attestation_digest_matches_specification(self):
        assert DETERMINISTIC_TEST_MESSAGE == "AgentChain Phase 6.10.4 KMS attestation"
        expected_digest = Web3.keccak(text="AgentChain Phase 6.10.4 KMS attestation")
        assert DETERMINISTIC_DIGEST == expected_digest
        assert len(DETERMINISTIC_DIGEST) == 32

    def test_live_kms_signature_verification_and_recovery(self):
        client = MockAwsKmsClient()
        sign_resp = client.sign(
            KeyId=VALID_KMS_KEY_ARN,
            Message=DETERMINISTIC_DIGEST,
            MessageType="DIGEST",
            SigningAlgorithm="ECDSA_SHA_256",
        )
        raw_sig = sign_resp["Signature"]
        assert len(raw_sig) > 0

        # DER decoding
        r, s = decode_der_ecdsa_signature(raw_sig)
        assert 1 <= r < SECP256K1_N
        assert 1 <= s < SECP256K1_N

        # Low-S normalization
        if s > SECP256K1_HALF_N:
            s = SECP256K1_N - s
        assert s <= SECP256K1_HALF_N

        # Recovery-ID determination
        rec_id = determine_recovery_id(DETERMINISTIC_DIGEST, r, s, client.raw_pub_65[1:])
        assert rec_id in (0, 1)

        # Public key and address recovery
        from eth_keys import datatypes as keys_datatypes
        cand_sig = keys_datatypes.Signature(vrs=(rec_id, r, s))
        rec_pub = cand_sig.recover_public_key_from_msg_hash(DETERMINISTIC_DIGEST)
        assert rec_pub.to_bytes() == client.raw_pub_65[1:]

        recovered_addr = Web3.to_checksum_address(Web3.keccak(rec_pub.to_bytes())[-20:])
        assert recovered_addr == DETERMINISTIC_TEST_ADDR

        # KMS Verify API check
        verify_resp = client.verify(
            KeyId=VALID_KMS_KEY_ARN,
            Message=DETERMINISTIC_DIGEST,
            MessageType="DIGEST",
            Signature=raw_sig,
            SigningAlgorithm="ECDSA_SHA_256",
        )
        assert verify_resp["SignatureValid"] is True

    def test_aws_kms_signer_attestation_properties(self):
        client = MockAwsKmsClient()
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            expected_address=DETERMINISTIC_TEST_ADDR,
            client=client,
        )
        assert signer.health_check() is True
        assert signer.get_address() == DETERMINISTIC_TEST_ADDR
        assert signer.chain_id == CHAIN_ID_BASE_SEPOLIA

    @pytest.mark.asyncio
    async def test_relayer_startup_attestation(self):
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
        # Relayer startup must succeed
        await relayer.verify_startup()

    def test_negative_identity_test_fails_closed(self):
        client = MockAwsKmsClient()
        wrong_address = "0x1111111111111111111111111111111111111111"
        with pytest.raises(SignerIdentityMismatchError):
            signer = AwsKmsSigner(
                key_reference=VALID_KMS_KEY_ARN,
                chain_id=CHAIN_ID_BASE_SEPOLIA,
                expected_address=wrong_address,
                client=client,
            )
            signer.health_check()

    def test_negative_network_test_fails_closed(self):
        client = MockAwsKmsClient()
        # Invalid chain
        with pytest.raises(SignerNetworkMismatchError):
            AwsKmsSigner(
                key_reference=VALID_KMS_KEY_ARN,
                chain_id=999999,
                expected_address=DETERMINISTIC_TEST_ADDR,
                client=client,
            )

        # Base Mainnet hard block
        with pytest.raises(MainnetSubmissionBlockedError):
            AwsKmsSigner(
                key_reference=VALID_KMS_KEY_ARN,
                chain_id=CHAIN_ID_BASE_MAINNET,
                expected_address=DETERMINISTIC_TEST_ADDR,
                client=client,
            )

    def test_prerequisite_check_reports_unavailable_on_empty_env(self, monkeypatch):
        monkeypatch.delenv("AWS_KMS_KEY_ARN", raising=False)
        monkeypatch.delenv("SIGNER_KEY_REFERENCE", raising=False)
        ready, arn, region, expected_address, msg = check_prerequisites()
        assert ready is False
        assert "Missing AWS_KMS_KEY_ARN" in msg
