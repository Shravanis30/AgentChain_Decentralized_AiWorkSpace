"""Escrow Smart Contract ABI and typed event decoders.

Provides:
- ESCROW_ABI: Complete Contract ABI for Escrow.sol.
- EVENT_TOPICS: Mapping of topic0 hex -> event name.
- Event decoding utilities.
"""

from typing import Any
import eth_abi
from web3 import Web3
from web3._utils.events import get_event_data

ESCROW_ABI: list[dict[str, Any]] = [
    {
        "type": "event",
        "name": "EscrowCreated",
        "inputs": [
            {"name": "escrowId", "type": "bytes32", "indexed": True},
            {"name": "client", "type": "address", "indexed": True},
            {"name": "beneficiary", "type": "address", "indexed": True},
            {"name": "amount", "type": "uint256", "indexed": False},
            {"name": "referenceId", "type": "bytes32", "indexed": False},
            {"name": "executionDeadline", "type": "uint256", "indexed": False},
        ],
        "anonymous": False,
    },
    {
        "type": "event",
        "name": "EscrowFunded",
        "inputs": [
            {"name": "escrowId", "type": "bytes32", "indexed": True},
            {"name": "client", "type": "address", "indexed": True},
            {"name": "amount", "type": "uint256", "indexed": False},
        ],
        "anonymous": False,
    },
    {
        "type": "event",
        "name": "EscrowLocked",
        "inputs": [
            {"name": "escrowId", "type": "bytes32", "indexed": True},
            {"name": "operator", "type": "address", "indexed": True},
        ],
        "anonymous": False,
    },
    {
        "type": "event",
        "name": "EscrowReleased",
        "inputs": [
            {"name": "escrowId", "type": "bytes32", "indexed": True},
            {"name": "beneficiary", "type": "address", "indexed": True},
            {"name": "amount", "type": "uint256", "indexed": False},
        ],
        "anonymous": False,
    },
    {
        "type": "event",
        "name": "EscrowDistributed",
        "inputs": [
            {"name": "escrowId", "type": "bytes32", "indexed": True},
            {"name": "distributionId", "type": "bytes32", "indexed": True},
        ],
        "anonymous": False,
    },
    {
        "type": "event",
        "name": "DistributionExecuted",
        "inputs": [
            {"name": "distributionId", "type": "bytes32", "indexed": True},
            {"name": "escrowId", "type": "bytes32", "indexed": True},
            {"name": "referenceId", "type": "bytes32", "indexed": True},
            {"name": "grossAmount", "type": "uint256", "indexed": False},
            {"name": "developerRecipient", "type": "address", "indexed": False},
            {"name": "developerAmount", "type": "uint256", "indexed": False},
            {"name": "stakerRecipient", "type": "address", "indexed": False},
            {"name": "stakerAmount", "type": "uint256", "indexed": False},
            {"name": "daoRecipient", "type": "address", "indexed": False},
            {"name": "daoAmount", "type": "uint256", "indexed": False},
            {"name": "distributionVersion", "type": "uint256", "indexed": False},
        ],
        "anonymous": False,
    },
    {
        "type": "event",
        "name": "EscrowRefunded",
        "inputs": [
            {"name": "escrowId", "type": "bytes32", "indexed": True},
            {"name": "client", "type": "address", "indexed": True},
            {"name": "amount", "type": "uint256", "indexed": False},
        ],
        "anonymous": False,
    },
    {
        "type": "event",
        "name": "DisputeOpened",
        "inputs": [
            {"name": "escrowId", "type": "bytes32", "indexed": True},
            {"name": "initiator", "type": "address", "indexed": True},
            {"name": "reasonHash", "type": "bytes32", "indexed": True},
        ],
        "anonymous": False,
    },
    {
        "type": "event",
        "name": "DisputeResolved",
        "inputs": [
            {"name": "escrowId", "type": "bytes32", "indexed": True},
            {"name": "arbitrator", "type": "address", "indexed": True},
            {"name": "beneficiaryAmount", "type": "uint256", "indexed": False},
            {"name": "clientRefundAmount", "type": "uint256", "indexed": False},
        ],
        "anonymous": False,
    },
    {
        "type": "event",
        "name": "Paused",
        "inputs": [
            {"name": "account", "type": "address", "indexed": False},
        ],
        "anonymous": False,
    },
    {
        "type": "event",
        "name": "Unpaused",
        "inputs": [
            {"name": "account", "type": "address", "indexed": False},
        ],
        "anonymous": False,
    },
    # Function definitions
    {
        "type": "function",
        "name": "createEscrow",
        "inputs": [
            {"name": "referenceId", "type": "bytes32"},
            {"name": "beneficiary", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "executionDeadline", "type": "uint256"},
            {"name": "salt", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "createAndFundEscrow",
        "inputs": [
            {"name": "referenceId", "type": "bytes32"},
            {"name": "beneficiary", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "executionDeadline", "type": "uint256"},
            {"name": "salt", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "fundEscrow",
        "inputs": [{"name": "escrowId", "type": "bytes32"}],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "lockEscrow",
        "inputs": [{"name": "escrowId", "type": "bytes32"}],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "releaseEscrow",
        "inputs": [{"name": "escrowId", "type": "bytes32"}],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "refundEscrow",
        "inputs": [{"name": "escrowId", "type": "bytes32"}],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "disputeEscrow",
        "inputs": [
            {"name": "escrowId", "type": "bytes32"},
            {"name": "reasonHash", "type": "bytes32"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "resolveDispute",
        "inputs": [
            {"name": "escrowId", "type": "bytes32"},
            {"name": "beneficiaryAmount", "type": "uint256"},
            {"name": "clientRefundAmount", "type": "uint256"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "pause",
        "inputs": [],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "unpause",
        "inputs": [],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "getEscrow",
        "inputs": [{"name": "escrowId", "type": "bytes32"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "escrowId", "type": "bytes32"},
                    {"name": "client", "type": "address"},
                    {"name": "beneficiary", "type": "address"},
                    {"name": "amount", "type": "uint256"},
                    {"name": "state", "type": "uint8"},
                    {"name": "createdAt", "type": "uint256"},
                    {"name": "fundedAt", "type": "uint256"},
                    {"name": "lockedAt", "type": "uint256"},
                    {"name": "settledAt", "type": "uint256"},
                    {"name": "executionDeadline", "type": "uint256"},
                    {"name": "referenceId", "type": "bytes32"},
                    {"name": "disputeReasonHash", "type": "bytes32"},
                ],
            }
        ],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "computeEscrowId",
        "inputs": [
            {"name": "client", "type": "address"},
            {"name": "referenceId", "type": "bytes32"},
            {"name": "salt", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "pure",
    },
]

RESULT_NOTARY_ABI: list[dict[str, Any]] = [
    {
        "type": "event",
        "name": "ResultNotarized",
        "inputs": [
            {"name": "executionId", "type": "bytes32", "indexed": True},
            {"name": "resultHash", "type": "bytes32", "indexed": True},
            {"name": "artifactCommitment", "type": "bytes32", "indexed": False},
            {"name": "notarizer", "type": "address", "indexed": True},
            {"name": "timestamp", "type": "uint256", "indexed": False},
        ],
        "anonymous": False,
    },
    {
        "type": "function",
        "name": "notarizeResult",
        "inputs": [
            {"name": "executionId", "type": "bytes32"},
            {"name": "resultHash", "type": "bytes32"},
            {"name": "artifactCommitment", "type": "bytes32"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "getProof",
        "inputs": [{"name": "executionId", "type": "bytes32"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "executionId", "type": "bytes32"},
                    {"name": "resultHash", "type": "bytes32"},
                    {"name": "artifactCommitment", "type": "bytes32"},
                    {"name": "notarizer", "type": "address"},
                    {"name": "timestamp", "type": "uint256"},
                    {"name": "blockNumber", "type": "uint256"},
                ],
            }
        ],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "hasProof",
        "inputs": [{"name": "executionId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "verifyProof",
        "inputs": [
            {"name": "executionId", "type": "bytes32"},
            {"name": "expectedHash", "type": "bytes32"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "getChainId",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
]

REPUTATION_REGISTRY_ABI: list[dict[str, Any]] = [
    {
        "type": "event",
        "name": "ReputationEventRegistered",
        "inputs": [
            {"name": "agentId", "type": "bytes32", "indexed": True},
            {"name": "executionId", "type": "bytes32", "indexed": True},
            {"name": "agentVersionId", "type": "bytes32", "indexed": False},
            {"name": "outcomeType", "type": "uint8", "indexed": False},
            {"name": "resultHash", "type": "bytes32", "indexed": False},
            {"name": "evidenceHash", "type": "bytes32", "indexed": False},
            {"name": "reporter", "type": "address", "indexed": True},
            {"name": "timestamp", "type": "uint256", "indexed": False},
        ],
        "anonymous": False,
    },
    {
        "type": "function",
        "name": "registerReputationEvent",
        "inputs": [
            {"name": "agentId", "type": "bytes32"},
            {"name": "executionId", "type": "bytes32"},
            {"name": "agentVersionId", "type": "bytes32"},
            {"name": "outcomeType", "type": "uint8"},
            {"name": "resultHash", "type": "bytes32"},
            {"name": "evidenceHash", "type": "bytes32"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "getRecord",
        "inputs": [{"name": "executionId", "type": "bytes32"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "agentId", "type": "bytes32"},
                    {"name": "executionId", "type": "bytes32"},
                    {"name": "agentVersionId", "type": "bytes32"},
                    {"name": "outcomeType", "type": "uint8"},
                    {"name": "resultHash", "type": "bytes32"},
                    {"name": "evidenceHash", "type": "bytes32"},
                    {"name": "reporter", "type": "address"},
                    {"name": "timestamp", "type": "uint64"},
                    {"name": "blockNumber", "type": "uint64"},
                ],
            }
        ],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "hasRecord",
        "inputs": [{"name": "executionId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "resultNotary",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "getChainId",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
]

# Topic0 lookup map: "0x..." -> (event ABI entry, contract_instance)
_w3 = Web3()
_escrow_contract = _w3.eth.contract(abi=ESCROW_ABI)
_notary_contract = _w3.eth.contract(abi=RESULT_NOTARY_ABI)
_reputation_contract = _w3.eth.contract(abi=REPUTATION_REGISTRY_ABI)
_contract = _escrow_contract  # Backward compatibility reference

EVENT_TOPICS: dict[str, dict[str, Any]] = {}
EVENT_CONTRACT_MAP: dict[str, Any] = {}


def normalize_hex(val: Any) -> str:
    """Normalize any hex or bytes input into a standard lowercase 0x-prefixed string."""
    if isinstance(val, (bytes, bytearray)):
        h = val.hex()
    else:
        h = str(val)
    while h.startswith("0x0x"):
        h = h[2:]
    if not h.startswith("0x"):
        h = "0x" + h
    return h.lower()


for item in ESCROW_ABI:
    if item["type"] == "event":
        types_str = ",".join(inp["type"] for inp in item["inputs"])
        sig = f"{item['name']}({types_str})"
        topic = normalize_hex(Web3.keccak(text=sig).hex())
        EVENT_TOPICS[topic] = item
        EVENT_CONTRACT_MAP[topic] = _escrow_contract

for item in RESULT_NOTARY_ABI:
    if item["type"] == "event":
        types_str = ",".join(inp["type"] for inp in item["inputs"])
        sig = f"{item['name']}({types_str})"
        topic = normalize_hex(Web3.keccak(text=sig).hex())
        EVENT_TOPICS[topic] = item
        EVENT_CONTRACT_MAP[topic] = _notary_contract

for item in REPUTATION_REGISTRY_ABI:
    if item["type"] == "event":
        types_str = ",".join(inp["type"] for inp in item["inputs"])
        sig = f"{item['name']}({types_str})"
        topic = normalize_hex(Web3.keccak(text=sig).hex())
        EVENT_TOPICS[topic] = item
        EVENT_CONTRACT_MAP[topic] = _reputation_contract


def get_event_abi_by_topic(topic0: Any) -> dict[str, Any] | None:
    """Retrieve event ABI item by topic0 hex string."""
    return EVENT_TOPICS.get(normalize_hex(topic0))


def decode_escrow_log(log: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Decodes an Ethereum log receipt against the Escrow and ResultNotary ABIs.
    
    Returns (event_name, decoded_dict) or None if log does not match supported events.
    """
    topics = log.get("topics", [])
    if not topics:
        return None

    topic0 = normalize_hex(topics[0])
    event_abi = get_event_abi_by_topic(topic0)
    if not event_abi:
        return None

    contract_instance = EVENT_CONTRACT_MAP.get(topic0, _escrow_contract)

    try:
        # Prepare a cleaned copy of log for get_event_data so web3 doesn't choke on 0x0x or malformed hex
        cleaned_log = dict(log)
        cleaned_log["topics"] = [normalize_hex(t) for t in topics]
        if "data" in cleaned_log and cleaned_log["data"] is not None:
            cleaned_log["data"] = normalize_hex(cleaned_log["data"])

        # Use web3 event decoder
        event_name = event_abi["name"]
        event_template = getattr(contract_instance.events, event_name)
        decoded = get_event_data(_w3.codec, event_template()._get_event_abi(), cleaned_log)
        args = dict(decoded.get("args", {}))

        # Format types safely for JSON serialization
        formatted_args: dict[str, Any] = {}
        for k, v in args.items():
            if isinstance(v, (bytes, bytearray)):
                formatted_args[k] = normalize_hex(v)
            elif isinstance(v, int):
                formatted_args[k] = str(v)
            else:
                formatted_args[k] = str(v)

        return event_name, formatted_args
    except Exception:
        return None

