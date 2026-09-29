"""Blockchain indexing and transaction infrastructure services."""

from app.services.blockchain.config import (
    ALLOWED_OPERATIONS,
    BASE_MAINNET_USDC,
    BASE_SEPOLIA_USDC,
    CHAIN_ID_ANVIL,
    CHAIN_ID_BASE_MAINNET,
    CHAIN_ID_BASE_SEPOLIA,
    ChainConfig,
    get_chain_config,
    get_chain_configs,
)
from app.services.blockchain.errors import (
    ArbitraryCalldataBlockedError,
    BlockchainError,
    ChainMismatchError,
    ContractMismatchError,
    ErrorClassification,
    InvalidRoleAddressError,
    MainnetSubmissionBlockedError,
    NonceCollisionError,
    PermanentRpcError,
    ProductionSignerUnavailableError,
    ReceiptTimeoutError,
    ReorgDetectedError,
    SecurityConfigurationError,
    SignerIdentityMismatchError,
    SignerNetworkMismatchError,
    StuckTransactionError,
    TransactionRevertedError,
    TransientRpcError,
    UnauthorizedOperationError,
    classify_rpc_error,
)
from app.services.blockchain.abi import (
    ESCROW_ABI,
    EVENT_TOPICS,
    decode_escrow_log,
)
from app.services.blockchain.rpc_client import BlockchainRpcClient
from app.services.blockchain.block_tracker import BlockTracker
from app.services.blockchain.reorg_handler import ReorgHandler
from app.services.blockchain.escrow_state_projector import EscrowStateProjector
from app.services.blockchain.event_indexer import EventIndexer
from app.services.blockchain.nonce_manager import NonceManager
from app.services.blockchain.gas_estimator import GasEstimator
from app.services.blockchain.transaction_intent import TransactionIntentService
from app.services.blockchain.tx_outbox import BlockchainTxOutboxDispatcher
from app.services.blockchain.relayer import BlockchainRelayer
from app.services.blockchain.signer import (
    ANVIL_DEFAULT_ADDRESS,
    BLOCKED_DEVELOPMENT_ADDRESSES,
    AwsKmsClientProtocol,
    AwsKmsSigner,
    HsmSignerAdapter,
    KmsHealthStatus,
    KmsSignerAdapter,
    LocalAccountSigner,
    LocalDevelopmentSigner,
    MockSigner,
    MpcSignerAdapter,
    ProductionSigner,
    SignerEnvironment,
    SignerFactory,
    SignerType,
    TestnetAccountSigner,
    TransactionSigner,
    decode_der_ecdsa_signature,
    determine_recovery_id,
    extract_uncompressed_public_key_from_spki,
)
from app.services.blockchain.reconciliation import BlockchainReconciliationService
from app.services.blockchain.metrics import BlockchainMetrics

__all__ = [
    "ALLOWED_OPERATIONS",
    "ArbitraryCalldataBlockedError",
    "AwsKmsClientProtocol",
    "AwsKmsSigner",
    "BASE_MAINNET_USDC",
    "BASE_SEPOLIA_USDC",
    "BlockchainError",
    "BlockchainMetrics",
    "BlockchainReconciliationService",
    "BlockchainRelayer",
    "BlockchainRpcClient",
    "BlockchainTxOutboxDispatcher",
    "BlockTracker",
    "CHAIN_ID_ANVIL",
    "CHAIN_ID_BASE_MAINNET",
    "CHAIN_ID_BASE_SEPOLIA",
    "ChainConfig",
    "ChainMismatchError",
    "ContractMismatchError",
    "ESCROW_ABI",
    "EVENT_TOPICS",
    "ErrorClassification",
    "EscrowStateProjector",
    "EventIndexer",
    "GasEstimator",
    "LocalAccountSigner",
    "MainnetSubmissionBlockedError",
    "MockSigner",
    "NonceCollisionError",
    "NonceManager",
    "PermanentRpcError",
    "ReceiptTimeoutError",
    "ReorgDetectedError",
    "ReorgHandler",
    "SignerIdentityMismatchError",
    "StuckTransactionError",
    "TransactionIntentService",
    "TransactionRevertedError",
    "TransactionSigner",
    "TransientRpcError",
    "UnauthorizedOperationError",
    "classify_rpc_error",
    "decode_escrow_log",
    "get_chain_config",
    "get_chain_configs",
]
