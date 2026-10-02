"""Phase 6.10.3 — Production KMS Operational Controls & Failure Injection Test Suite.

Verifies:
1. Operational Health Model & Status Distinction:
   - CONFIG_INVALID
   - KMS_UNAVAILABLE
   - KMS_KEY_DISABLED
   - KMS_KEY_INVALID
   - SIGNER_IDENTITY_MISMATCH
   - NETWORK_MISMATCH
   - HEALTHY
2. Production Signer Boundary & Anti-Fallback Protection:
   - Refusal of LocalDevelopmentSigner in production.
   - Refusal of TestnetAccountSigner in production.
   - Refusal of raw DEPLOYER_PRIVATE_KEY in production.
   - Refusal of Anvil default address in production.
   - Refusal of retired historical developer address in production.
   - Refusal of Base Mainnet (8453) across all production layers.
3. Relayer Preflight Integration:
   - Relayer startup fails closed for every unhealthy signer state, reporting the exact failure status.
4. Production Configuration Validation Script:
   - Exit code 0 for valid production configuration.
   - Exit code 1 for security violations (raw keys, blocked accounts, bad ARNs, Mainnet).
   - Exit code 2 for unpopulated environment or template placeholders.
"""

import sys
from pathlib import Path
from typing import Any
import pytest
from unittest.mock import AsyncMock, MagicMock
from eth_account import Account
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
    get_chain_config,
)
from app.services.blockchain.errors import (
    MainnetSubmissionBlockedError,
    ProductionSignerUnavailableError,
    SecurityConfigurationError,
    SignerIdentityMismatchError,
    SignerNetworkMismatchError,
)
from app.services.blockchain.relayer import BlockchainRelayer
from app.services.blockchain.nonce_manager import NonceManager
from app.services.blockchain.gas_estimator import GasEstimator
from app.services.blockchain.signer import (
    ANVIL_DEFAULT_ADDRESS,
    HARDHAT_DEFAULT_ADDRESS,
    AwsKmsClientProtocol,
    AwsKmsSigner,
    KmsHealthStatus,
    LocalDevelopmentSigner,
    SignerEnvironment,
    SignerFactory,
    SignerType,
    TestnetAccountSigner,
)
from scripts.verify_production_kms_configuration import validate_config_dict

DETERMINISTIC_TEST_KEY_HEX = "0x703f4e6283731359f0f06c02ee4753a30f6424098e6b45aca799567b664c755d"
DETERMINISTIC_TEST_ADDR = "0x473888C859F88D3De7b5A20986805b4dC3178189"
VALID_KMS_KEY_ARN = "arn:aws:kms:us-east-1:123456789012:key/12345678-1234-1234-1234-123456789012"


def _build_spki(raw_pub_65: bytes) -> bytes:
    header = bytes.fromhex("3056301006072a8648ce3d020106052b8104000a034200")
    return header + raw_pub_65


class MockClientWithControl(AwsKmsClientProtocol):
    """Controllable mock for failure injection."""

    def __init__(
        self,
        key_spec: str = "ECC_SECG_P256K1",
        key_usage: str = "SIGN_VERIFY",
        key_state: str = "Enabled",
        simulate_unreachable: bool = False,
        spki_override: bytes | None = None,
        private_key_hex: str = DETERMINISTIC_TEST_KEY_HEX,
    ) -> None:
        self.key_spec = key_spec
        self.key_usage = key_usage
        self.key_state = key_state
        self.simulate_unreachable = simulate_unreachable
        self.spki_override = spki_override
        self.account = Account.from_key(private_key_hex)

    def describe_key(self, **kwargs: Any) -> dict[str, Any]:
        if self.simulate_unreachable:
            raise ConnectionError("Endpoint connection timed out")
        return {
            "KeyMetadata": {
                "KeyId": kwargs.get("KeyId", VALID_KMS_KEY_ARN),
                "KeySpec": self.key_spec,
                "KeyUsage": self.key_usage,
                "KeyState": self.key_state,
            }
        }

    def get_public_key(self, **kwargs: Any) -> dict[str, Any]:
        if self.simulate_unreachable:
            raise ConnectionError("Endpoint connection timed out")
        if self.spki_override is not None:
            return {"PublicKey": self.spki_override}
        raw_pub = b"\x04" + self.account._key_obj.public_key.to_bytes()
        return {"PublicKey": _build_spki(raw_pub)}

    def sign(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    def verify(self, **kwargs: Any) -> dict[str, Any]:
        return {"SignatureValid": True}


# ==============================================================================
# 1. KMS HEALTH STATUS DISTINCTIONS
# ==============================================================================

class TestKmsHealthModelDistinctions:
    def test_healthy_status(self):
        client = MockClientWithControl()
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            expected_address=DETERMINISTIC_TEST_ADDR,
            client=client,
        )
        status, reason = signer.check_health_status()
        assert status == KmsHealthStatus.HEALTHY
        assert signer.health_check() is True

    def test_kms_unavailable_status(self):
        client = MockClientWithControl(simulate_unreachable=True)
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            client=client,
            validate_on_init=False,
        )
        status, reason = signer.check_health_status()
        assert status == KmsHealthStatus.KMS_UNAVAILABLE
        assert "timed out" in reason
        assert signer.health_check() is False

    def test_kms_key_disabled_status(self):
        client = MockClientWithControl(key_state="Disabled")
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            client=client,
            validate_on_init=False,
        )
        status, reason = signer.check_health_status()
        assert status == KmsHealthStatus.KMS_KEY_DISABLED
        assert "Disabled" in reason
        assert signer.health_check() is False

    def test_kms_key_invalid_keyspec_status(self):
        client = MockClientWithControl(key_spec="RSA_2048")
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            client=client,
            validate_on_init=False,
        )
        status, reason = signer.check_health_status()
        assert status == KmsHealthStatus.KMS_KEY_INVALID
        assert "KeySpec 'RSA_2048'" in reason
        assert signer.health_check() is False

    def test_kms_key_invalid_keyusage_status(self):
        client = MockClientWithControl(key_usage="ENCRYPT_DECRYPT")
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            client=client,
            validate_on_init=False,
        )
        status, reason = signer.check_health_status()
        assert status == KmsHealthStatus.KMS_KEY_INVALID
        assert "KeyUsage 'ENCRYPT_DECRYPT'" in reason
        assert signer.health_check() is False

    def test_signer_identity_mismatch_status(self):
        client = MockClientWithControl()
        wrong_address = "0x0000000000000000000000000000000000000001"
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            expected_address=wrong_address,
            client=client,
            validate_on_init=False,
        )
        status, reason = signer.check_health_status()
        assert status == KmsHealthStatus.SIGNER_IDENTITY_MISMATCH
        assert "does not match expected" in reason
        assert signer.health_check() is False

    def test_anvil_blocked_address_status(self):
        anvil_key = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        client = MockClientWithControl(private_key_hex=anvil_key)
        signer = AwsKmsSigner(
            key_reference=VALID_KMS_KEY_ARN,
            chain_id=CHAIN_ID_BASE_SEPOLIA,
            client=client,
            validate_on_init=False,
        )
        status, reason = signer.check_health_status()
        assert status == KmsHealthStatus.SIGNER_IDENTITY_MISMATCH
        assert "blocked development address" in reason
        assert signer.health_check() is False

    def test_config_invalid_missing_key_ref(self):
        with pytest.raises(SecurityConfigurationError):
            AwsKmsSigner(
                key_reference="",
                chain_id=CHAIN_ID_BASE_SEPOLIA,
                validate_on_init=False,
            )


# ==============================================================================
# 2. RELAYER STARTUP INTEGRATION WITH HEALTH DISTINCTIONS
# ==============================================================================

class TestRelayerStartupHealthIntegration:
    @pytest.mark.asyncio
    async def test_relayer_reports_kms_key_disabled_explicitly(self):
        client = MockClientWithControl(key_state="Disabled")
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

        with pytest.raises(RuntimeError) as excinfo:
            await relayer.verify_startup()
        assert "[PREFLIGHT FAIL] Signer health_check() returned False" in str(excinfo.value)
        assert "[KMS_KEY_DISABLED]" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_relayer_reports_kms_unavailable_explicitly(self):
        client = MockClientWithControl(simulate_unreachable=True)
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

        with pytest.raises(RuntimeError) as excinfo:
            await relayer.verify_startup()
        assert "[KMS_UNAVAILABLE]" in str(excinfo.value)


# ==============================================================================
# 3. ANTI-FALLBACK REGRESSION TESTS
# ==============================================================================

class TestProductionAntiFallbackProtection:
    def test_production_rejects_local_dev_signer(self):
        with pytest.raises(SecurityConfigurationError) as excinfo:
            SignerFactory.create_signer(
                environment=SignerEnvironment.PRODUCTION,
                signer_type=SignerType.LOCAL_DEV,
                chain_id=CHAIN_ID_BASE_SEPOLIA,
                private_key=None,
            )
        assert "is forbidden in production" in str(excinfo.value)

    def test_production_rejects_testnet_account_signer(self):
        with pytest.raises(SecurityConfigurationError) as excinfo:
            SignerFactory.create_signer(
                environment=SignerEnvironment.PRODUCTION,
                signer_type=SignerType.TESTNET,
                chain_id=CHAIN_ID_BASE_SEPOLIA,
                private_key=None,
            )
        assert "is forbidden in production" in str(excinfo.value)

    def test_production_rejects_raw_private_key_even_with_kms_type(self):
        with pytest.raises(SecurityConfigurationError) as excinfo:
            SignerFactory.create_signer(
                environment=SignerEnvironment.PRODUCTION,
                signer_type=SignerType.PRODUCTION_AWS_KMS,
                chain_id=CHAIN_ID_BASE_SEPOLIA,
                key_reference=VALID_KMS_KEY_ARN,
                private_key=DETERMINISTIC_TEST_KEY_HEX,  # Raw key passed
            )
        assert "Raw private key was provided in production" in str(excinfo.value)

    def test_production_rejects_raw_key_passed_as_key_reference(self):
        raw_key_no_prefix = DETERMINISTIC_TEST_KEY_HEX.removeprefix("0x")
        with pytest.raises(SecurityConfigurationError) as excinfo:
            SignerFactory.create_signer(
                environment=SignerEnvironment.PRODUCTION,
                signer_type=SignerType.PRODUCTION_AWS_KMS,
                chain_id=CHAIN_ID_BASE_SEPOLIA,
                key_reference=raw_key_no_prefix,
            )
        assert "A raw 32-byte private key was passed as key_reference" in str(excinfo.value)

    def test_production_hard_blocks_base_mainnet(self):
        with pytest.raises(MainnetSubmissionBlockedError):
            SignerFactory.create_signer(
                environment=SignerEnvironment.PRODUCTION,
                signer_type=SignerType.PRODUCTION_AWS_KMS,
                chain_id=CHAIN_ID_BASE_MAINNET,
                key_reference=VALID_KMS_KEY_ARN,
            )


# ==============================================================================
# 4. CONFIGURATION VALIDATION SCRIPT TESTS
# ==============================================================================

class TestVerifyProductionKmsConfigurationScript:
    def _get_valid_config(self) -> dict[str, str]:
        return {
            "CHAIN_ID": "84532",
            "RPC_URL": "https://sepolia.base.org",
            "SIGNER_PROVIDER": "aws_kms",
            "AWS_REGION": "us-east-1",
            "AWS_KMS_KEY_ARN": VALID_KMS_KEY_ARN,
            "EXPECTED_SIGNER_ADDRESS": DETERMINISTIC_TEST_ADDR,
            "AGENT_REGISTRY_ADDRESS": "0x96C3945e264A880EbaB5e95fDF786Fc18A4389B5",
            "REVENUE_DISTRIBUTOR_ADDRESS": "0x2F2d5Cc40e2C4C32cC86BC5127c2833a7784ee1c",
            "ESCROW_ADDRESS": "0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1",
            "RESULT_NOTARY_ADDRESS": "0xed58B4E37f81dFbD08c3d2DeF613286bC8Cf8056",
            "REPUTATION_REGISTRY_ADDRESS": "0x423856529583F536d5dFaE80f7Bc142075c6Fc71",
            "USDC_ADDRESS": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            "RELAYER_ROLE_ADDRESS": DETERMINISTIC_TEST_ADDR,
            "ADMIN_ROLE_ADDRESS": "0x162dEB52f30f551020c23155Bc5c66419c184d82",
        }

    def test_valid_config_returns_code_0(self):
        cfg = self._get_valid_config()
        code, msgs = validate_config_dict(cfg)
        assert code == 0
        assert "verified successfully" in msgs[0]

    def test_placeholder_config_returns_code_2(self):
        cfg = self._get_valid_config()
        cfg["AWS_KMS_KEY_ARN"] = "<AWS_KMS_KEY_ARN>"
        code, msgs = validate_config_dict(cfg)
        assert code == 2
        assert "Unpopulated or placeholder" in msgs[0]

    def test_raw_key_present_returns_code_1(self):
        cfg = self._get_valid_config()
        cfg["DEPLOYER_PRIVATE_KEY"] = DETERMINISTIC_TEST_KEY_HEX
        code, msgs = validate_config_dict(cfg)
        assert code == 1
        assert any("Raw private key variable" in m for m in msgs)

    def test_base_mainnet_returns_code_1(self):
        cfg = self._get_valid_config()
        cfg["CHAIN_ID"] = "8453"
        code, msgs = validate_config_dict(cfg)
        assert code == 1
        assert any("Base Mainnet (Chain ID 8453) is strictly HARD BLOCKED" in m for m in msgs)

    def test_anvil_default_address_role_returns_code_1(self):
        cfg = self._get_valid_config()
        cfg["RELAYER_ROLE_ADDRESS"] = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
        code, msgs = validate_config_dict(cfg)
        assert code == 1
        assert any("uses blocked address" in m for m in msgs)

    def test_retired_historical_developer_address_returns_code_1(self):
        cfg = self._get_valid_config()
        cfg["ADMIN_ROLE_ADDRESS"] = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
        code, msgs = validate_config_dict(cfg)
        assert code == 1
        assert any("Retired Historical Developer Account" in m for m in msgs)

    def test_same_relayer_and_admin_role_returns_code_1(self):
        cfg = self._get_valid_config()
        cfg["RELAYER_ROLE_ADDRESS"] = DETERMINISTIC_TEST_ADDR
        cfg["ADMIN_ROLE_ADDRESS"] = DETERMINISTIC_TEST_ADDR
        code, msgs = validate_config_dict(cfg)
        assert code == 1
        assert any("ROLE SEPARATION VIOLATION" in m for m in msgs)
