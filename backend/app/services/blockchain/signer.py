"""Production Signer Abstraction & Safety Boundaries for AgentChain.

Strictly separates:
1. Local Development Signer (Anvil / Localhost: Chain 31337 ONLY)
2. Testnet Signer (Base Sepolia: Chain 84532 ONLY)
3. Production Signer (KMS / HSM / MPC interface boundaries)

Non-Negotiable Invariants:
- Production code MUST NOT depend on a raw private key being present.
- Production environment strictly forbids in-memory raw private key signers.
- Base Mainnet (Chain 8453) signing is hard blocked.
- Signer network compatibility is strictly validated (no network/signer mismatch).
- Anvil default accounts (0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266) are hard blocked on non-local networks.
- MockSigner is never permitted outside test environments and is never labeled as KMS/HSM/MPC.
"""

from abc import ABC, abstractmethod
import enum
import logging
from typing import Any, Protocol, runtime_checkable
from eth_account import Account
from eth_account._utils.signing import (
    encode_transaction,
    serializable_unsigned_transaction_from_dict,
)
from eth_account._utils.typed_transactions import TypedTransaction
from eth_keys import datatypes as keys_datatypes
from web3 import Web3

from app.services.blockchain.abi import normalize_hex
from app.services.blockchain.config import (
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
)
from app.services.blockchain.errors import (
    MainnetSubmissionBlockedError,
    ProductionSignerUnavailableError,
    SecurityConfigurationError,
    SignerIdentityMismatchError,
    SignerNetworkMismatchError,
)

logger = logging.getLogger(__name__)

# secp256k1 curve parameters
SECP256K1_N: int = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BB5CA5B9659CA6D3D
SECP256K1_HALF_N: int = SECP256K1_N // 2

# DER / SPKI ASN.1 Constants
SECP256K1_SPKI_OID: bytes = bytes.fromhex("06052b8104000a")  # 1.3.132.0.10 (secp256k1)
ID_EC_PUBLIC_KEY_OID: bytes = bytes.fromhex("06072a8648ce3d0201")  # 1.2.840.10045.2.1 (id-ecPublicKey)

# Known default Anvil / Hardhat development addresses that must NEVER be used on live networks
ANVIL_DEFAULT_ADDRESS: str = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266".lower()
HARDHAT_DEFAULT_ADDRESS: str = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8".lower()
BLOCKED_DEVELOPMENT_ADDRESSES: frozenset[str] = frozenset({
    ANVIL_DEFAULT_ADDRESS,
    HARDHAT_DEFAULT_ADDRESS,
})


@runtime_checkable
class AwsKmsClientProtocol(Protocol):
    """Protocol defining the AWS KMS client interface used by AwsKmsSigner."""

    def describe_key(self, **kwargs: Any) -> dict[str, Any]:
        ...

    def get_public_key(self, **kwargs: Any) -> dict[str, Any]:
        ...

    def sign(self, **kwargs: Any) -> dict[str, Any]:
        ...

    def verify(self, **kwargs: Any) -> dict[str, Any]:
        ...


def decode_der_ecdsa_signature(der: bytes) -> tuple[int, int]:
    """Strictly parses and validates a DER-encoded ECDSA signature.

    Enforces ASN.1 SEQUENCE, two positive INTEGERs (r, s), no trailing data,
    no excessive zero padding, zero/order bounds checking against secp256k1.
    """
    if not isinstance(der, (bytes, bytearray)):
        raise ValueError("DER signature must be bytes.")
    if len(der) < 8:
        raise ValueError(f"DER signature too short: {len(der)} bytes.")

    if der[0] != 0x30:
        raise ValueError(f"Invalid DER: expected ASN.1 SEQUENCE tag 0x30, got 0x{der[0]:02x}.")

    idx = 1
    seq_len = der[idx]
    idx += 1
    if seq_len & 0x80:
        num_len_bytes = seq_len & 0x7F
        if num_len_bytes == 0 or idx + num_len_bytes > len(der):
            raise ValueError("Invalid DER sequence length encoding.")
        seq_len = int.from_bytes(der[idx:idx + num_len_bytes], "big")
        idx += num_len_bytes

    if idx + seq_len != len(der):
        raise ValueError(
            f"Invalid DER: sequence length mismatch (expected {idx + seq_len} total bytes, got {len(der)})."
        )

    def _parse_integer(offset: int, name: str) -> tuple[int, int]:
        if offset >= len(der):
            raise ValueError(f"Unexpected end of DER input while reading {name}.")
        if der[offset] != 0x02:
            raise ValueError(f"Invalid DER: expected INTEGER tag 0x02 for {name} at {offset}, got 0x{der[offset]:02x}.")
        offset += 1
        if offset >= len(der):
            raise ValueError(f"Unexpected end of DER input after {name} tag.")
        int_len = der[offset]
        offset += 1
        if int_len & 0x80:
            num_len_bytes = int_len & 0x7F
            if num_len_bytes == 0 or offset + num_len_bytes > len(der):
                raise ValueError(f"Invalid DER integer length encoding for {name}.")
            int_len = int.from_bytes(der[offset:offset + num_len_bytes], "big")
            offset += num_len_bytes
        if int_len <= 0:
            raise ValueError(f"Zero length integer for {name} in DER.")
        if offset + int_len > len(der):
            raise ValueError(f"Integer {name} length exceeds available DER buffer.")

        val_bytes = der[offset:offset + int_len]
        # In ASN.1 signed integers, MSB indicates negative number if not padded with 0x00
        if val_bytes[0] & 0x80:
            raise ValueError(f"Negative integer in DER signature for {name}.")
        # Reject superfluous zero padding (e.g. 0x00 0x01 instead of 0x01)
        if len(val_bytes) > 1 and val_bytes[0] == 0x00 and not (val_bytes[1] & 0x80):
            raise ValueError(f"Excessive leading zero padding in DER integer for {name}.")

        val = int.from_bytes(val_bytes, "big")
        return val, offset + int_len

    r, next_idx = _parse_integer(idx, "r")
    s, next_idx = _parse_integer(next_idx, "s")

    if next_idx != len(der):
        raise ValueError(f"Trailing data after DER signature: {len(der) - next_idx} bytes.")

    if r <= 0 or r >= SECP256K1_N:
        raise ValueError(f"ECDSA r value {r} out of valid secp256k1 range [1, N-1].")
    if s <= 0 or s >= SECP256K1_N:
        raise ValueError(f"ECDSA s value {s} out of valid secp256k1 range [1, N-1].")

    return r, s


def extract_uncompressed_public_key_from_spki(spki: bytes) -> bytes:
    """Extracts the 65-byte uncompressed secp256k1 public key (0x04 || X || Y) from DER SPKI.

    Validates that the key uses id-ecPublicKey and secp256k1 curve OIDs, and verifies
    that the extracted point lies on secp256k1.
    """
    if not isinstance(spki, (bytes, bytearray)):
        raise ValueError("SPKI public key must be bytes.")
    if len(spki) < 80:
        raise ValueError(f"SPKI public key too short ({len(spki)} bytes).")

    if spki[0] != 0x30:
        raise ValueError("Invalid SPKI: expected ASN.1 SEQUENCE (0x30).")

    if ID_EC_PUBLIC_KEY_OID not in spki:
        raise ValueError("Invalid SPKI: missing id-ecPublicKey algorithm OID.")
    if SECP256K1_SPKI_OID not in spki:
        raise ValueError("Invalid SPKI: curve is not secp256k1 (unsupported curve).")

    # Locate BIT STRING tag (0x03)
    bitstring_idx = spki.find(b"\x03\x42\x00\x04")
    if bitstring_idx == -1:
        idx = spki.find(b"\x03")
        if idx == -1 or idx + 4 >= len(spki):
            raise ValueError("Invalid SPKI: BIT STRING not found.")
        if spki[idx + 2] != 0x00:
            raise ValueError("Invalid SPKI: non-zero unused bits in BIT STRING.")
        if spki[idx + 3] != 0x04:
            raise ValueError("Invalid SPKI: public key is not uncompressed (must start with 0x04).")
        pub_bytes = spki[idx + 3:idx + 3 + 65]
    else:
        pub_bytes = spki[bitstring_idx + 3:bitstring_idx + 3 + 65]

    if len(pub_bytes) != 65:
        raise ValueError(f"Invalid public key length: expected 65 bytes, got {len(pub_bytes)}.")

    try:
        keys_datatypes.PublicKey(pub_bytes[1:])
    except Exception as exc:
        raise ValueError(f"Invalid secp256k1 public key point: {exc}") from exc

    return bytes(pub_bytes)


def derive_ethereum_address_from_public_key(pub_bytes: bytes) -> str:
    """Derives checksummed Ethereum address from uncompressed secp256k1 public key bytes."""
    if len(pub_bytes) == 65:
        if pub_bytes[0] != 0x04:
            raise ValueError("Uncompressed public key must start with 0x04 prefix.")
        pub_64 = pub_bytes[1:]
    elif len(pub_bytes) == 64:
        pub_64 = pub_bytes
    else:
        raise ValueError(f"Invalid public key length for address derivation: {len(pub_bytes)} bytes.")

    address_bytes = Web3.keccak(pub_64)[-20:]
    return Web3.to_checksum_address(address_bytes)


def determine_recovery_id(
    digest: bytes,
    r: int,
    s: int,
    expected_public_key_bytes: bytes,
) -> int:
    """Deterministically finds recovery ID (0 or 1) by matching recovered public key against KMS public key."""
    if len(expected_public_key_bytes) == 65 and expected_public_key_bytes[0] == 0x04:
        expected_pub_64 = expected_public_key_bytes[1:]
    else:
        expected_pub_64 = expected_public_key_bytes

    if len(expected_pub_64) != 64:
        raise ValueError(f"Expected public key must be 64 bytes, got {len(expected_pub_64)}.")

    for candidate_v in (0, 1):
        try:
            cand_sig = keys_datatypes.Signature(vrs=(candidate_v, r, s))
            recovered_pub = cand_sig.recover_public_key_from_msg_hash(digest)
            if recovered_pub.to_bytes() == expected_pub_64:
                return candidate_v
        except Exception:
            continue

    raise ValueError(
        "Failed to determine recovery ID: candidate public keys (v=0, 1) do not match KMS public key."
    )


class SignerType(str, enum.Enum):
    LOCAL_DEV = "LOCAL_DEV"
    TESTNET = "TESTNET"
    PRODUCTION_KMS = "PRODUCTION_KMS"
    PRODUCTION_AWS_KMS = "PRODUCTION_AWS_KMS"
    AWS_KMS = "AWS_KMS"
    PRODUCTION_HSM = "PRODUCTION_HSM"
    PRODUCTION_MPC = "PRODUCTION_MPC"
    MOCK = "MOCK"


class SignerEnvironment(str, enum.Enum):
    DEVELOPMENT = "development"
    TESTNET = "testnet"
    PRODUCTION = "production"


class KmsHealthStatus(str, enum.Enum):
    HEALTHY = "HEALTHY"
    CONFIG_INVALID = "CONFIG_INVALID"
    KMS_UNAVAILABLE = "KMS_UNAVAILABLE"
    KMS_KEY_DISABLED = "KMS_KEY_DISABLED"
    KMS_KEY_INVALID = "KMS_KEY_INVALID"
    SIGNER_IDENTITY_MISMATCH = "SIGNER_IDENTITY_MISMATCH"
    NETWORK_MISMATCH = "NETWORK_MISMATCH"


class TransactionSigner(ABC):
    """Abstract base signer interface decoupling key management from transaction lifecycle."""

    @property
    def signer_type(self) -> SignerType:
        """Returns the classified signer type."""
        return SignerType.LOCAL_DEV

    @property
    def allowed_chains(self) -> frozenset[int]:
        """Returns the frozenset of chain IDs permitted for this signer."""
        return frozenset({CHAIN_ID_ANVIL, CHAIN_ID_BASE_SEPOLIA})

    @abstractmethod
    def get_address(self) -> str:
        """Returns the checksummed Ethereum address of the signer."""
        pass

    @property
    def address(self) -> str:
        """Convenience property returning the checksummed Ethereum address."""
        return self.get_address()

    @property
    def chain_id(self) -> int:
        """Returns the configured chain ID for this signer."""
        return getattr(self, "_chain_id", 0)

    @abstractmethod
    def sign_transaction(self, tx_dict: dict[str, Any]) -> str:
        """Signs an EIP-1559 transaction dict and returns raw hex-encoded signed transaction.

        Args:
            tx_dict: EIP-1559 transaction dictionary.
        Returns:
            Raw signed transaction as a hex string.
        Raises:
            RuntimeError or PermanentRpcError: If signing fails, network mismatches, or signer is unavailable.
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Returns True if the signer is available and operational.

        Called at relayer startup and health checks.
        A return value of False must halt startup and alert operators.
        """
        pass

    def verify_network_compatibility(self, target_chain_id: int) -> None:
        """Validates that this signer is permitted to sign for the target chain ID.

        Raises:
            SignerNetworkMismatchError: If the target chain is not allowed.
            MainnetSubmissionBlockedError: If target chain is Base Mainnet.
        """
        if target_chain_id == CHAIN_ID_BASE_MAINNET:
            raise MainnetSubmissionBlockedError(
                "HARD SECURITY BARRIER: Base Mainnet transaction signing is blocked."
            )
        if target_chain_id not in self.allowed_chains:
            raise SignerNetworkMismatchError(
                f"Signer of type '{self.signer_type.value}' is only allowed on chains "
                f"{sorted(self.allowed_chains)}, but target chain is {target_chain_id}."
            )


class LocalDevelopmentSigner(TransactionSigner):
    """In-memory private key signer strictly confined to local development (Anvil chain 31337).

    Refuses all external networks including Base Sepolia and Base Mainnet.
    """

    def __init__(self, private_key_hex: str, chain_id: int = CHAIN_ID_ANVIL) -> None:
        if chain_id != CHAIN_ID_ANVIL:
            raise SignerNetworkMismatchError(
                f"LocalDevelopmentSigner is restricted to chain {CHAIN_ID_ANVIL}, "
                f"cannot be configured for chain {chain_id}."
            )
        self._chain_id = chain_id
        self._account = Account.from_key(private_key_hex)
        self._address = Web3.to_checksum_address(self._account.address)

    @property
    def signer_type(self) -> SignerType:
        return SignerType.LOCAL_DEV

    @property
    def allowed_chains(self) -> frozenset[int]:
        return frozenset({CHAIN_ID_ANVIL})

    def get_address(self) -> str:
        return self._address

    def sign_transaction(self, tx_dict: dict[str, Any]) -> str:
        tx_chain_id = tx_dict.get("chainId", self._chain_id)
        if tx_chain_id != CHAIN_ID_ANVIL:
            raise SignerNetworkMismatchError(
                f"LocalDevelopmentSigner refuses to sign for chain {tx_chain_id}."
            )
        signed = self._account.sign_transaction(tx_dict)
        raw = getattr(signed, "rawTransaction", getattr(signed, "raw_transaction", None))
        return normalize_hex(raw)

    def health_check(self) -> bool:
        return self._account is not None and bool(self._address)


class TestnetAccountSigner(TransactionSigner):
    """Signer dedicated strictly to public testnet operations (Base Sepolia: chain 84532).

    Hard Guards:
    - Strictly forbids Base Mainnet (8453).
    - Strictly forbids Anvil / Localhost (31337) to prevent local/testnet cross-contamination.
    - Strictly forbids Anvil default accounts.
    """

    __test__ = False

    def __init__(self, private_key_hex: str, chain_id: int = CHAIN_ID_BASE_SEPOLIA) -> None:
        if chain_id == CHAIN_ID_BASE_MAINNET:
            raise MainnetSubmissionBlockedError(
                "TestnetAccountSigner MUST NOT be configured for Base Mainnet (chain 8453)."
            )
        if chain_id != CHAIN_ID_BASE_SEPOLIA:
            raise SignerNetworkMismatchError(
                f"TestnetAccountSigner is restricted to Base Sepolia (chain {CHAIN_ID_BASE_SEPOLIA}), "
                f"got chain {chain_id}."
            )
        self._chain_id = chain_id
        self._account = Account.from_key(private_key_hex)
        self._address = Web3.to_checksum_address(self._account.address)

        if self._address.lower() in BLOCKED_DEVELOPMENT_ADDRESSES:
            raise SecurityConfigurationError(
                f"SECURITY VIOLATION: Anvil/Hardhat default account {self._address} "
                "is strictly forbidden on public testnet!"
            )

    @property
    def signer_type(self) -> SignerType:
        return SignerType.TESTNET

    @property
    def allowed_chains(self) -> frozenset[int]:
        return frozenset({CHAIN_ID_BASE_SEPOLIA})

    def get_address(self) -> str:
        return self._address

    def sign_transaction(self, tx_dict: dict[str, Any]) -> str:
        tx_chain_id = tx_dict.get("chainId", self._chain_id)
        if tx_chain_id == CHAIN_ID_BASE_MAINNET:
            raise MainnetSubmissionBlockedError(
                "SECURITY VIOLATION: TestnetAccountSigner refuses to sign Mainnet transactions."
            )
        if tx_chain_id != CHAIN_ID_BASE_SEPOLIA:
            raise SignerNetworkMismatchError(
                f"TestnetAccountSigner cannot sign for chain {tx_chain_id}, only {CHAIN_ID_BASE_SEPOLIA}."
            )
        signed = self._account.sign_transaction(tx_dict)
        raw = getattr(signed, "rawTransaction", getattr(signed, "raw_transaction", None))
        return normalize_hex(raw)

    def health_check(self) -> bool:
        return self._account is not None and bool(self._address)


class LocalAccountSigner(TransactionSigner):
    """In-memory signer supporting local Anvil and Base Sepolia test execution.

    Maintained for backwards compatibility with existing test suites.
    CRITICAL:
    - Never serialized, never logged, never written to DB or Redis.
    - REFUSED on Base Mainnet (chain_id=8453) at construction and at sign time.
    """

    def __init__(self, private_key_hex: str, chain_id: int | None = None) -> None:
        if chain_id == CHAIN_ID_BASE_MAINNET:
            raise RuntimeError(
                "SECURITY VIOLATION: LocalAccountSigner MUST NOT be used on Base Mainnet "
                "(chain_id=8453). Use a KMS/HSM/MPC production signer."
            )
        self._chain_id = chain_id
        self._account = Account.from_key(private_key_hex)
        self._address = Web3.to_checksum_address(self._account.address)

    @property
    def signer_type(self) -> SignerType:
        if self._chain_id == CHAIN_ID_BASE_SEPOLIA:
            return SignerType.TESTNET
        return SignerType.LOCAL_DEV

    @property
    def allowed_chains(self) -> frozenset[int]:
        return frozenset({CHAIN_ID_ANVIL, CHAIN_ID_BASE_SEPOLIA})

    def get_address(self) -> str:
        return self._address

    def sign_transaction(self, tx_dict: dict[str, Any]) -> str:
        tx_chain_id = tx_dict.get("chainId")
        if tx_chain_id == CHAIN_ID_BASE_MAINNET or self._chain_id == CHAIN_ID_BASE_MAINNET:
            raise RuntimeError(
                "SECURITY VIOLATION: LocalAccountSigner refuses to sign Mainnet transactions."
            )
        if self._chain_id is not None and tx_chain_id is not None and tx_chain_id != self._chain_id:
            raise SignerNetworkMismatchError(
                f"Transaction chainId ({tx_chain_id}) does not match signer configured chainId ({self._chain_id})."
            )
        signed = self._account.sign_transaction(tx_dict)
        raw = getattr(signed, "rawTransaction", getattr(signed, "raw_transaction", None))
        return normalize_hex(raw)

    def health_check(self) -> bool:
        return self._account is not None and bool(self._address)


class MockSigner(TransactionSigner):
    """Mock signer for tests that do not require live signing.

    STRICTLY FORBIDDEN IN PRODUCTION OR LIVE OPERATIONAL RUNS.
    """

    def __init__(self, address: str = "0x90F79bf6EB2c4f870365E785982E1f101E93b906", chain_id: int = CHAIN_ID_ANVIL) -> None:
        self._address = Web3.to_checksum_address(address)
        self._chain_id = chain_id

    @property
    def signer_type(self) -> SignerType:
        return SignerType.MOCK

    @property
    def allowed_chains(self) -> frozenset[int]:
        return frozenset({CHAIN_ID_ANVIL, CHAIN_ID_BASE_SEPOLIA})

    def get_address(self) -> str:
        return self._address

    def sign_transaction(self, tx_dict: dict[str, Any]) -> str:
        tx_chain_id = tx_dict.get("chainId", self._chain_id)
        if tx_chain_id == CHAIN_ID_BASE_MAINNET:
            raise MainnetSubmissionBlockedError("MockSigner refuses to sign Mainnet transactions.")
        return "0x" + "00" * 65

    def health_check(self) -> bool:
        return True


class ProductionSigner(TransactionSigner, ABC):
    """Abstract base class for all production hardware/remote key custody signers.

    Enforces:
    - ZERO raw private keys in memory, parameters, or environment variables.
    - Requires authenticated connection to KMS / HSM / MPC key manager.
    - Fail-closed if remote provider is unresponsive or unauthenticated.
    """

    def __init__(self, key_reference: str, chain_id: int) -> None:
        if not key_reference or not key_reference.strip():
            raise SecurityConfigurationError("Production signer requires a valid non-empty key reference.")
        # Reject if key_reference appears to be a raw 32-byte private key
        clean_ref = key_reference.strip().removeprefix("0x")
        if len(clean_ref) == 64 and all(c in "0123456789abcdefABCDEF" for c in clean_ref):
            raise SecurityConfigurationError(
                "CRITICAL SECURITY VIOLATION: A raw 32-byte private key was passed as key_reference! "
                "Production signers MUST use KMS ARN, Vault transit path, or MPC key ID."
            )
        self._key_reference = key_reference
        self._chain_id = chain_id

    @property
    def key_reference(self) -> str:
        return self._key_reference


class KmsSignerAdapter(ProductionSigner):
    """Adapter boundary for AWS KMS / GCP Cloud KMS production signing.

    STATUS: INTERFACE / ADAPTER BOUNDARY ONLY — NO LIVE PROVIDER INTEGRATED.
    Fail-closed: All signing calls fail closed until a tested cloud provider is provisioned.
    """

    def __init__(self, key_reference: str, chain_id: int, region: str = "us-east-1") -> None:
        super().__init__(key_reference, chain_id)
        self._region = region
        self._provider_active = False  # Real cloud SDK not wired in repository

    @property
    def signer_type(self) -> SignerType:
        return SignerType.PRODUCTION_KMS

    @property
    def allowed_chains(self) -> frozenset[int]:
        # Production KMS is intended for Base Mainnet / Base Sepolia, but Mainnet is currently hard blocked
        return frozenset({CHAIN_ID_BASE_SEPOLIA, CHAIN_ID_BASE_MAINNET})

    def get_address(self) -> str:
        if not self._provider_active:
            raise ProductionSignerUnavailableError(
                "KMS provider integration is not active. Address cannot be derived."
            )
        return "0x0000000000000000000000000000000000000000"

    def sign_transaction(self, tx_dict: dict[str, Any]) -> str:
        tx_chain_id = tx_dict.get("chainId", self._chain_id)
        if tx_chain_id == CHAIN_ID_BASE_MAINNET:
            raise MainnetSubmissionBlockedError(
                "Base Mainnet transaction signing is blocked under current repository gate."
            )
        raise ProductionSignerUnavailableError(
            "AWS/GCP KMS signing provider is not integrated. Production signing remains blocked."
        )

    def health_check(self) -> bool:
        # Fails closed because provider is not integrated
        return False


class AwsKmsSigner(ProductionSigner):
    """Concrete production signer using AWS Key Management Service (KMS).

    Uses asymmetric KMS key with KeySpec ECC_SECG_P256K1 and KeyUsage SIGN_VERIFY.
    The private key remains strictly protected inside AWS KMS HSM hardware and is never exposed.
    Signs precomputed 32-byte Ethereum digests using MessageType='DIGEST' without double-hashing.
    Enforces strict DER decoding, low-S normalization, recovery ID determination,
    and signer identity binding against EXPECTED_SIGNER_ADDRESS.
    """

    def __init__(
        self,
        key_reference: str,
        chain_id: int = CHAIN_ID_BASE_SEPOLIA,
        expected_address: str | None = None,
        region: str = "us-east-1",
        client: AwsKmsClientProtocol | None = None,
        validate_on_init: bool = True,
    ) -> None:
        if chain_id == CHAIN_ID_BASE_MAINNET:
            raise MainnetSubmissionBlockedError(
                "SECURITY VIOLATION: AwsKmsSigner MUST NOT be configured for Base Mainnet (chain 8453). "
                "Base Mainnet remains hard blocked."
            )
        if chain_id == CHAIN_ID_ANVIL:
            raise SignerNetworkMismatchError(
                f"AwsKmsSigner cannot be used on local development network (chain {CHAIN_ID_ANVIL}). "
                "Use LocalDevelopmentSigner for Anvil."
            )
        if chain_id != CHAIN_ID_BASE_SEPOLIA:
            raise SignerNetworkMismatchError(
                f"AwsKmsSigner is restricted to Base Sepolia (chain {CHAIN_ID_BASE_SEPOLIA}), got {chain_id}."
            )

        super().__init__(key_reference=key_reference, chain_id=chain_id)

        if expected_address is not None:
            if not isinstance(expected_address, str) or not Web3.is_address(expected_address):
                raise SecurityConfigurationError(
                    f"Invalid expected_signer_address format: '{expected_address}'."
                )
            self._expected_address: str | None = Web3.to_checksum_address(expected_address)
        else:
            self._expected_address = None

        self._region = region
        self._client = client
        self._cached_pub_key_64: bytes | None = None
        self._cached_address: str | None = None
        self._key_metadata: dict[str, Any] | None = None

        if validate_on_init:
            self._validate_and_cache_key()

    @property
    def signer_type(self) -> SignerType:
        return SignerType.PRODUCTION_AWS_KMS

    @property
    def allowed_chains(self) -> frozenset[int]:
        return frozenset({CHAIN_ID_BASE_SEPOLIA})

    def _get_client(self) -> AwsKmsClientProtocol:
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore
        except ImportError as exc:
            raise ProductionSignerUnavailableError(
                "boto3 is required to use AwsKmsSigner when client is not injected."
            ) from exc
        try:
            self._client = boto3.client("kms", region_name=self._region)
            return self._client
        except Exception as exc:
            raise ProductionSignerUnavailableError(
                f"Failed to instantiate AWS KMS client in region {self._region}: {exc}"
            ) from exc

    def _validate_and_cache_key(self) -> None:
        if self._cached_address is not None and self._cached_pub_key_64 is not None:
            return

        client = self._get_client()

        # 1. Validate Key Metadata
        try:
            desc_resp = client.describe_key(KeyId=self._key_reference)
        except (SecurityConfigurationError, SignerIdentityMismatchError):
            raise
        except Exception as exc:
            raise ProductionSignerUnavailableError(
                f"Failed to describe KMS key '{self._key_reference}': {exc}"
            ) from exc

        meta = desc_resp.get("KeyMetadata", {})
        key_spec = meta.get("KeySpec")
        key_usage = meta.get("KeyUsage")
        key_state = meta.get("KeyState")

        if key_spec != "ECC_SECG_P256K1":
            raise SecurityConfigurationError(
                f"Unsupported KMS KeySpec: '{key_spec}'. Required: 'ECC_SECG_P256K1'."
            )
        if key_usage != "SIGN_VERIFY":
            raise SecurityConfigurationError(
                f"Unsupported KMS KeyUsage: '{key_usage}'. Required: 'SIGN_VERIFY'."
            )
        if key_state != "Enabled":
            raise SecurityConfigurationError(
                f"KMS key '{self._key_reference}' is in state '{key_state}'. Required: 'Enabled'."
            )

        # 2. Retrieve Public Key and parse SPKI
        try:
            pub_resp = client.get_public_key(KeyId=self._key_reference)
        except Exception as exc:
            raise ProductionSignerUnavailableError(
                f"Failed to get public key for KMS key '{self._key_reference}': {exc}"
            ) from exc

        raw_spki = pub_resp.get("PublicKey")
        if not raw_spki or not isinstance(raw_spki, (bytes, bytearray)):
            raise SecurityConfigurationError("KMS get_public_key returned empty or invalid PublicKey.")

        try:
            uncompressed_pub_65 = extract_uncompressed_public_key_from_spki(bytes(raw_spki))
            derived_address = derive_ethereum_address_from_public_key(uncompressed_pub_65)
        except Exception as exc:
            raise SecurityConfigurationError(f"Failed to parse KMS secp256k1 public key: {exc}") from exc

        # 3. Block Development Addresses
        if derived_address.lower() in BLOCKED_DEVELOPMENT_ADDRESSES:
            raise SecurityConfigurationError(
                f"SECURITY VIOLATION: Derived KMS address {derived_address} is a blocked development account."
            )

        # 4. Enforce Signer Identity Binding
        if self._expected_address is not None:
            if derived_address.lower() != self._expected_address.lower():
                raise SignerIdentityMismatchError(
                    f"KMS derived address {derived_address} does not match expected address {self._expected_address}."
                )

        self._cached_pub_key_64 = uncompressed_pub_65[1:]
        self._cached_address = derived_address
        self._key_metadata = meta

    def validate_key_metadata(self) -> dict[str, Any]:
        """Validates key metadata with KMS and returns the KeyMetadata dict."""
        self._validate_and_cache_key()
        return dict(self._key_metadata or {})

    def get_address(self) -> str:
        """Returns the derived Ethereum checksum address of the KMS key."""
        self._validate_and_cache_key()
        if self._cached_address is None:
            raise ProductionSignerUnavailableError("Signer address could not be derived from KMS.")
        return self._cached_address

    def sign_transaction(self, tx_dict: dict[str, Any]) -> str:
        """Signs an Ethereum transaction using AWS KMS with low-S normalization and deterministic recovery ID.

        Enforces that the transaction hash is signed via MessageType='DIGEST' without double hashing.
        """
        tx_chain_id = tx_dict.get("chainId", self._chain_id)
        if tx_chain_id == CHAIN_ID_BASE_MAINNET or self._chain_id == CHAIN_ID_BASE_MAINNET:
            raise MainnetSubmissionBlockedError(
                "SECURITY VIOLATION: AwsKmsSigner refuses to sign Base Mainnet transactions."
            )
        if tx_chain_id == CHAIN_ID_ANVIL:
            raise SignerNetworkMismatchError(
                f"AwsKmsSigner cannot sign transactions for local development network (Chain {CHAIN_ID_ANVIL})."
            )
        if tx_chain_id != CHAIN_ID_BASE_SEPOLIA:
            raise SignerNetworkMismatchError(
                f"AwsKmsSigner cannot sign for chain {tx_chain_id}, only {CHAIN_ID_BASE_SEPOLIA}."
            )

        self._validate_and_cache_key()
        if self._cached_pub_key_64 is None:
            raise ProductionSignerUnavailableError("Public key not available for KMS signing.")

        # Serialize transaction to get Keccak-256 transaction hash
        unsigned_tx = serializable_unsigned_transaction_from_dict(tx_dict)
        tx_hash: bytes = unsigned_tx.hash()
        if len(tx_hash) != 32:
            raise ValueError(f"Expected 32-byte transaction hash, got {len(tx_hash)} bytes.")

        client = self._get_client()
        try:
            sign_resp = client.sign(
                KeyId=self._key_reference,
                Message=tx_hash,
                MessageType="DIGEST",
                SigningAlgorithm="ECDSA_SHA_256",
            )
        except Exception as exc:
            raise ProductionSignerUnavailableError(
                f"AWS KMS sign operation failed: {exc}"
            ) from exc

        der_sig = sign_resp.get("Signature")
        if not der_sig or not isinstance(der_sig, (bytes, bytearray)):
            raise ProductionSignerUnavailableError("AWS KMS sign returned empty or invalid signature.")

        # Decode strict DER
        try:
            r, s = decode_der_ecdsa_signature(bytes(der_sig))
        except Exception as exc:
            raise ProductionSignerUnavailableError(f"Failed to decode KMS signature DER: {exc}") from exc

        # Enforce canonical low-S
        if s > SECP256K1_HALF_N:
            s = SECP256K1_N - s

        # Deterministic recovery ID (0 or 1)
        rec_id = determine_recovery_id(tx_hash, r, s, self._cached_pub_key_64)

        # Determine v
        # EIP-1559 (TypedTransaction): v is 0 or 1
        # Legacy EIP-155: v = chain_id * 2 + 35 + rec_id
        if isinstance(unsigned_tx, TypedTransaction):
            v = rec_id
        else:
            v = tx_chain_id * 2 + 35 + rec_id

        signed_raw_bytes = encode_transaction(unsigned_tx, vrs=(v, r, s))
        return normalize_hex(signed_raw_bytes)

    def sign_digest(self, digest: bytes) -> tuple[int, int, int]:
        """Signs an arbitrary 32-byte digest with KMS, returning (v, r, s).

        v is the canonical recovery ID (0 or 1), and s is strictly low-S normalized.
        """
        if len(digest) != 32:
            raise ValueError(f"Digest must be exactly 32 bytes, got {len(digest)}.")

        self._validate_and_cache_key()
        if self._cached_pub_key_64 is None:
            raise ProductionSignerUnavailableError("Public key not available for KMS signing.")

        client = self._get_client()
        try:
            sign_resp = client.sign(
                KeyId=self._key_reference,
                Message=digest,
                MessageType="DIGEST",
                SigningAlgorithm="ECDSA_SHA_256",
            )
        except Exception as exc:
            raise ProductionSignerUnavailableError(
                f"AWS KMS sign operation failed: {exc}"
            ) from exc

        der_sig = sign_resp.get("Signature")
        if not der_sig or not isinstance(der_sig, (bytes, bytearray)):
            raise ProductionSignerUnavailableError("AWS KMS sign returned empty or invalid signature.")

        try:
            r, s = decode_der_ecdsa_signature(bytes(der_sig))
        except Exception as exc:
            raise ProductionSignerUnavailableError(f"Failed to decode KMS signature DER: {exc}") from exc

        if s > SECP256K1_HALF_N:
            s = SECP256K1_N - s

        rec_id = determine_recovery_id(digest, r, s, self._cached_pub_key_64)
        return rec_id, r, s

    def check_health_status(self) -> tuple[KmsHealthStatus, str]:
        """Performs structured health inspection distinguishing all operational failure modes."""
        # 1. Network check
        if self._chain_id != CHAIN_ID_BASE_SEPOLIA:
            return (
                KmsHealthStatus.NETWORK_MISMATCH,
                f"Chain ID {self._chain_id} is not supported. Required: {CHAIN_ID_BASE_SEPOLIA}.",
            )

        # 2. Config check
        if not self._key_reference or not self._key_reference.strip():
            return KmsHealthStatus.CONFIG_INVALID, "KMS key reference is empty."
        if not self._region or not self._region.strip():
            return KmsHealthStatus.CONFIG_INVALID, "AWS region is empty."

        # 3. Client & connectivity check
        try:
            client = self._get_client()
        except Exception as exc:
            return KmsHealthStatus.KMS_UNAVAILABLE, f"Failed to instantiate KMS client: {exc}"

        # 4. DescribeKey check
        try:
            desc_resp = client.describe_key(KeyId=self._key_reference)
        except Exception as exc:
            return KmsHealthStatus.KMS_UNAVAILABLE, f"KMS DescribeKey failed: {exc}"

        meta = desc_resp.get("KeyMetadata", {})
        key_spec = meta.get("KeySpec")
        key_usage = meta.get("KeyUsage")
        key_state = meta.get("KeyState")

        if key_state != "Enabled":
            return KmsHealthStatus.KMS_KEY_DISABLED, f"KMS key is '{key_state}', required: 'Enabled'."

        if key_spec != "ECC_SECG_P256K1":
            return KmsHealthStatus.KMS_KEY_INVALID, f"KMS KeySpec '{key_spec}' is not 'ECC_SECG_P256K1'."

        if key_usage != "SIGN_VERIFY":
            return KmsHealthStatus.KMS_KEY_INVALID, f"KMS KeyUsage '{key_usage}' is not 'SIGN_VERIFY'."

        # 5. Public key check
        try:
            pub_resp = client.get_public_key(KeyId=self._key_reference)
            raw_spki = pub_resp.get("PublicKey")
            if not raw_spki:
                return KmsHealthStatus.KMS_KEY_INVALID, "KMS GetPublicKey returned empty PublicKey."
            uncompressed_pub_65 = extract_uncompressed_public_key_from_spki(bytes(raw_spki))
            derived_address = derive_ethereum_address_from_public_key(uncompressed_pub_65)
        except Exception as exc:
            return KmsHealthStatus.KMS_KEY_INVALID, f"Failed to parse KMS public key: {exc}"

        if derived_address.lower() in BLOCKED_DEVELOPMENT_ADDRESSES:
            return (
                KmsHealthStatus.SIGNER_IDENTITY_MISMATCH,
                f"Derived address {derived_address} is a blocked development address.",
            )

        if self._expected_address is not None:
            if derived_address.lower() != self._expected_address.lower():
                return (
                    KmsHealthStatus.SIGNER_IDENTITY_MISMATCH,
                    f"Derived address {derived_address} does not match expected {self._expected_address}.",
                )

        # Cache key if healthy
        self._cached_pub_key_64 = uncompressed_pub_65[1:]
        self._cached_address = derived_address
        self._key_metadata = meta

        return KmsHealthStatus.HEALTHY, f"KMS key healthy and bound to address {derived_address}."

    def health_check(self) -> bool:
        """Returns True if the KMS key can be reached, metadata is valid, and address derived."""
        status, reason = self.check_health_status()
        if status != KmsHealthStatus.HEALTHY:
            logger.warning("AwsKmsSigner health check failed [%s]: %s", status.value, reason)
            return False
        return True


class HsmSignerAdapter(ProductionSigner):
    """Adapter boundary for Hardware Security Module (HSM / PKCS#11) production signing.

    STATUS: INTERFACE / ADAPTER BOUNDARY ONLY — NO LIVE PROVIDER INTEGRATED.
    Fail-closed: All signing calls fail closed until HSM hardware is configured.
    """

    def __init__(self, key_reference: str, chain_id: int, slot_id: int = 0) -> None:
        super().__init__(key_reference, chain_id)
        self._slot_id = slot_id

    @property
    def signer_type(self) -> SignerType:
        return SignerType.PRODUCTION_HSM

    @property
    def allowed_chains(self) -> frozenset[int]:
        return frozenset({CHAIN_ID_BASE_SEPOLIA, CHAIN_ID_BASE_MAINNET})

    def get_address(self) -> str:
        raise ProductionSignerUnavailableError(
            "HSM PKCS#11 provider is not integrated. Address cannot be derived."
        )

    def sign_transaction(self, tx_dict: dict[str, Any]) -> str:
        tx_chain_id = tx_dict.get("chainId", self._chain_id)
        if tx_chain_id == CHAIN_ID_BASE_MAINNET:
            raise MainnetSubmissionBlockedError(
                "Base Mainnet transaction signing is blocked under current repository gate."
            )
        raise ProductionSignerUnavailableError(
            "HSM PKCS#11 signing provider is not integrated. Production signing remains blocked."
        )

    def health_check(self) -> bool:
        return False


class MpcSignerAdapter(ProductionSigner):
    """Adapter boundary for Multi-Party Computation (Fireblocks / Turnkey) signing.

    STATUS: INTERFACE / ADAPTER BOUNDARY ONLY — NO LIVE PROVIDER INTEGRATED.
    Fail-closed: All signing calls fail closed until MPC provider credentials are verified.
    """

    def __init__(self, key_reference: str, chain_id: int, api_client_id: str = "") -> None:
        super().__init__(key_reference, chain_id)
        self._api_client_id = api_client_id

    @property
    def signer_type(self) -> SignerType:
        return SignerType.PRODUCTION_MPC

    @property
    def allowed_chains(self) -> frozenset[int]:
        return frozenset({CHAIN_ID_BASE_SEPOLIA, CHAIN_ID_BASE_MAINNET})

    def get_address(self) -> str:
        raise ProductionSignerUnavailableError(
            "MPC signing provider is not integrated. Address cannot be derived."
        )

    def sign_transaction(self, tx_dict: dict[str, Any]) -> str:
        tx_chain_id = tx_dict.get("chainId", self._chain_id)
        if tx_chain_id == CHAIN_ID_BASE_MAINNET:
            raise MainnetSubmissionBlockedError(
                "Base Mainnet transaction signing is blocked under current repository gate."
            )
        raise ProductionSignerUnavailableError(
            "MPC (Fireblocks/Turnkey) signing provider is not integrated. Production signing remains blocked."
        )

    def health_check(self) -> bool:
        return False


class SignerFactory:
    """Factory enforcing strict environment separation and safe signer instantiation."""

    @staticmethod
    def create_signer(
        environment: str | SignerEnvironment,
        signer_type: str | SignerType,
        chain_id: int,
        private_key: str | None = None,
        key_reference: str | None = None,
        mock_address: str | None = None,
        **kwargs: Any,
    ) -> TransactionSigner:
        """Instantiates a strictly validated TransactionSigner for the given environment.

        Raises:
            SecurityConfigurationError: On unsafe credential or environment combinations.
            SignerNetworkMismatchError: If the signer is not compatible with the chain ID.
            MainnetSubmissionBlockedError: If chain ID is Base Mainnet.
            ProductionSignerUnavailableError: If a production provider is requested.
        """
        env_str = environment.value if hasattr(environment, "value") else str(environment).lower()
        st_str = signer_type.value.upper() if hasattr(signer_type, "value") else str(signer_type).upper()


        # Hard safety barrier: Base Mainnet signing is unconditionally blocked
        if chain_id == CHAIN_ID_BASE_MAINNET:
            raise MainnetSubmissionBlockedError(
                "SECURITY BARRIER: Base Mainnet transaction signing is blocked by repository safety gate."
            )

        if env_str == SignerEnvironment.PRODUCTION.value:
            # 1. Never accept raw private keys in production
            if private_key and private_key.strip():
                raise SecurityConfigurationError(
                    "CRITICAL SECURITY VIOLATION: Raw private key was provided in production environment. "
                    "Production MUST use KMS/HSM/MPC key custody."
                )

            # 2. Reject non-production signer types
            if st_str in (SignerType.LOCAL_DEV.value, SignerType.TESTNET.value, SignerType.MOCK.value):
                raise SecurityConfigurationError(
                    f"Signer type '{st_str}' is forbidden in production environment."
                )

            if not key_reference or not key_reference.strip():
                raise SecurityConfigurationError(
                    "Production signer requires a valid non-empty 'key_reference' (KMS ARN or MPC key ID)."
                )

            if st_str in (
                SignerType.PRODUCTION_AWS_KMS.value,
                SignerType.AWS_KMS.value,
                "AWS_KMS",
                "PRODUCTION_AWS_KMS",
                "AWS-KMS",
            ):
                return AwsKmsSigner(key_reference=key_reference, chain_id=chain_id, **kwargs)
            elif st_str == SignerType.PRODUCTION_KMS.value:
                if kwargs.get("client") is not None or kwargs.get("use_aws_kms") is True:
                    return AwsKmsSigner(key_reference=key_reference, chain_id=chain_id, **kwargs)
                return KmsSignerAdapter(key_reference=key_reference, chain_id=chain_id, **kwargs)
            elif st_str == SignerType.PRODUCTION_HSM.value:
                return HsmSignerAdapter(key_reference=key_reference, chain_id=chain_id, **kwargs)
            elif st_str == SignerType.PRODUCTION_MPC.value:
                return MpcSignerAdapter(key_reference=key_reference, chain_id=chain_id, **kwargs)
            else:
                raise SecurityConfigurationError(f"Unsupported production signer type: {signer_type}")

        elif env_str == SignerEnvironment.TESTNET.value:
            if chain_id != CHAIN_ID_BASE_SEPOLIA:
                raise SignerNetworkMismatchError(
                    f"Testnet environment requires chain {CHAIN_ID_BASE_SEPOLIA}, got {chain_id}."
                )
            if st_str == SignerType.LOCAL_DEV.value:
                raise SecurityConfigurationError(
                    "LOCAL_DEV signer cannot be used in testnet environment."
                )
            if st_str == SignerType.MOCK.value:
                raise SecurityConfigurationError(
                    "MOCK signer cannot be used in live testnet environment."
                )
            if not private_key or not private_key.strip():
                raise SecurityConfigurationError(
                    "Testnet environment requires a valid testnet private key."
                )
            return TestnetAccountSigner(private_key_hex=private_key, chain_id=chain_id)

        elif env_str == SignerEnvironment.DEVELOPMENT.value:
            if chain_id != CHAIN_ID_ANVIL:
                raise SignerNetworkMismatchError(
                    f"Development environment requires chain {CHAIN_ID_ANVIL}, got {chain_id}."
                )
            if st_str == SignerType.MOCK.value:
                return MockSigner(address=mock_address or "0x90F79bf6EB2c4f870365E785982E1f101E93b906", chain_id=chain_id)
            if not private_key or not private_key.strip():
                raise SecurityConfigurationError(
                    "Development environment requires a private key or mock configuration."
                )
            return LocalDevelopmentSigner(private_key_hex=private_key, chain_id=chain_id)

        else:
            raise SecurityConfigurationError(f"Unknown environment: {environment}")
