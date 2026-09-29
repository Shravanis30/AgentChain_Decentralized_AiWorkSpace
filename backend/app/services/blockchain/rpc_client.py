"""Robust JSON-RPC client for EVM blockchains with fail-closed chain ID validation.

Features:
- Validates chain identity via eth_chainId on initialize and every critical check.
- Categorizes RPC failures (TRANSIENT, PERMANENT).
- Implements bounded retries with exponential backoff on transient failures.
- Fails closed immediately on chain ID mismatch or invalid responses.
- HARD GATE: Blocks transaction submission on Base Mainnet.
"""

import asyncio
import enum
import logging
from typing import Any
import httpx

from app.services.blockchain.config import CHAIN_ID_BASE_MAINNET, ChainConfig
from app.services.blockchain.errors import (
    ChainMismatchError,
    ErrorClassification,
    MainnetSubmissionBlockedError,
    PermanentRpcError,
    TransientRpcError,
    classify_rpc_error,
)

logger = logging.getLogger(__name__)


class RpcQueryStatus(str, enum.Enum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    RPC_ERROR = "RPC_ERROR"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"


class RpcQueryResult:
    """Explicitly represents RPC query outcome distinguishing data, not-found, and errors."""

    def __init__(
        self,
        data: Any | None,
        status: RpcQueryStatus,
        error_message: str | None = None,
    ) -> None:
        self.data = data
        self.status = status
        self.error_message = error_message

    @property
    def is_found(self) -> bool:
        return self.status == RpcQueryStatus.FOUND and self.data is not None

    @property
    def is_not_found(self) -> bool:
        return self.status == RpcQueryStatus.NOT_FOUND

    @property
    def is_error(self) -> bool:
        return self.status in (RpcQueryStatus.RPC_ERROR, RpcQueryStatus.NETWORK_UNAVAILABLE)


class BlockchainRpcClient:
    """Async Ethereum JSON-RPC client."""

    def __init__(
        self,
        config: ChainConfig,
        timeout_seconds: float = 10.0,
        max_retries: int = 4,
        initial_backoff_seconds: float = 0.5,
    ) -> None:
        self.config = config
        self.rpc_url = config.rpc_url
        self.chain_id = config.chain_id
        self.timeout = timeout_seconds
        self.max_retries = max_retries
        self.initial_backoff = initial_backoff_seconds
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5.0),
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        """Executes a single JSON-RPC call with retry logic on transient errors."""
        params = params or []
        client = await self._get_client()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }

        attempt = 0
        backoff = self.initial_backoff

        while True:
            attempt += 1
            try:
                response = await client.post(self.rpc_url, json=payload)
                if response.status_code == 429:
                    raise TransientRpcError(f"HTTP 429 Rate limit exceeded from {self.rpc_url}")
                if response.status_code >= 500:
                    raise TransientRpcError(f"HTTP {response.status_code} server error from {self.rpc_url}")
                if response.status_code != 200:
                    raise PermanentRpcError(f"HTTP {response.status_code} error from {self.rpc_url}: {response.text}")

                data = response.json()
                if "error" in data and data["error"] is not None:
                    err_info = data["error"]
                    err_msg = err_info.get("message", "Unknown RPC error")
                    err_code = err_info.get("code")
                    classification = classify_rpc_error(Exception(err_msg))

                    if classification == ErrorClassification.TRANSIENT:
                        raise TransientRpcError(f"Transient RPC error ({err_code}): {err_msg}", details=err_info)
                    raise PermanentRpcError(f"Permanent RPC error ({err_code}): {err_msg}", details=err_info)

                if "result" not in data:
                    raise PermanentRpcError("Malformed JSON-RPC response: missing 'result' field")

                return data["result"]

            except (TransientRpcError, httpx.RequestError, httpx.TimeoutException) as exc:
                if attempt >= self.max_retries:
                    logger.error(
                        "RPC call %s failed permanently after %d attempts: %s",
                        method,
                        attempt,
                        str(exc),
                    )
                    if isinstance(exc, TransientRpcError):
                        raise
                    raise TransientRpcError(f"RPC communication failed after {attempt} attempts: {exc}") from exc

                sleep_time = backoff * (2 ** (attempt - 1))
                logger.warning(
                    "RPC call %s attempt %d failed (%s). Retrying in %.2fs...",
                    method,
                    attempt,
                    str(exc),
                    sleep_time,
                )
                await asyncio.sleep(sleep_time)

    async def verify_chain_id(self) -> int:
        """Queries eth_chainId and strictly validates against expected config."""
        result = await self.call("eth_chainId")
        actual_chain_id = int(result, 16) if isinstance(result, str) else int(result)

        if actual_chain_id != self.chain_id:
            raise ChainMismatchError(
                f"Chain ID mismatch! Configured: {self.chain_id}, RPC returned: {actual_chain_id}"
            )
        return actual_chain_id

    async def get_block_number(self) -> int:
        """Fetches latest block number."""
        result = await self.call("eth_blockNumber")
        return int(result, 16)

    async def get_block_by_number(self, block_number: int, full_transactions: bool = False) -> dict[str, Any] | None:
        """Fetches block details by number."""
        hex_num = hex(block_number)
        return await self.call("eth_getBlockByNumber", [hex_num, full_transactions])

    async def get_block_by_hash(self, block_hash: str, full_transactions: bool = False) -> dict[str, Any] | None:
        """Fetches block details by hash."""
        return await self.call("eth_getBlockByHash", [block_hash, full_transactions])

    async def get_logs(
        self,
        from_block: int,
        to_block: int,
        address: str | list[str] | None = None,
        topics: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetches logs matching filter parameters."""
        filter_params: dict[str, Any] = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
        }
        if address:
            filter_params["address"] = address
        if topics:
            filter_params["topics"] = topics

        result = await self.call("eth_getLogs", [filter_params])
        return result or []

    async def get_transaction_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        """Fetches receipt for transaction hash."""
        return await self.call("eth_getTransactionReceipt", [tx_hash])

    async def get_transaction_receipt_safe(self, tx_hash: str) -> RpcQueryResult:
        """Queries transaction receipt safely, distinguishing FOUND, NOT_FOUND, RPC_ERROR, and NETWORK_UNAVAILABLE."""
        try:
            res = await self.get_transaction_receipt(tx_hash)
            if res is not None:
                return RpcQueryResult(data=res, status=RpcQueryStatus.FOUND)
            return RpcQueryResult(data=None, status=RpcQueryStatus.NOT_FOUND)
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            logger.warning("Network unavailable querying receipt %s: %s", tx_hash, exc)
            return RpcQueryResult(data=None, status=RpcQueryStatus.NETWORK_UNAVAILABLE, error_message=str(exc))
        except Exception as exc:
            logger.warning("RPC error querying receipt %s: %s", tx_hash, exc)
            return RpcQueryResult(data=None, status=RpcQueryStatus.RPC_ERROR, error_message=str(exc))

    async def get_transaction(self, tx_hash: str) -> dict[str, Any] | None:
        """Fetches transaction object by hash."""
        return await self.call("eth_getTransactionByHash", [tx_hash])

    async def get_transaction_safe(self, tx_hash: str) -> RpcQueryResult:
        """Queries transaction object safely, distinguishing FOUND, NOT_FOUND, RPC_ERROR, and NETWORK_UNAVAILABLE."""
        try:
            res = await self.get_transaction(tx_hash)
            if res is not None:
                return RpcQueryResult(data=res, status=RpcQueryStatus.FOUND)
            return RpcQueryResult(data=None, status=RpcQueryStatus.NOT_FOUND)
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            logger.warning("Network unavailable querying transaction %s: %s", tx_hash, exc)
            return RpcQueryResult(data=None, status=RpcQueryStatus.NETWORK_UNAVAILABLE, error_message=str(exc))
        except Exception as exc:
            logger.warning("RPC error querying transaction %s: %s", tx_hash, exc)
            return RpcQueryResult(data=None, status=RpcQueryStatus.RPC_ERROR, error_message=str(exc))

    async def get_transaction_count(self, address: str, block_tag: str = "pending") -> int:
        """Fetches account nonce."""
        result = await self.call("eth_getTransactionCount", [address, block_tag])
        return int(result, 16)

    async def get_transaction_count_safe(self, address: str, block_tag: str = "pending") -> RpcQueryResult:
        """Queries account nonce safely, distinguishing FOUND, RPC_ERROR, and NETWORK_UNAVAILABLE."""
        try:
            nonce = await self.get_transaction_count(address, block_tag)
            return RpcQueryResult(data=nonce, status=RpcQueryStatus.FOUND)
        except (httpx.ConnectError, httpx.NetworkError) as exc:
            logger.warning("Network unavailable querying nonce for %s: %s", address, exc)
            return RpcQueryResult(data=None, status=RpcQueryStatus.NETWORK_UNAVAILABLE, error_message=str(exc))
        except Exception as exc:
            logger.warning("RPC error querying nonce for %s: %s", address, exc)
            return RpcQueryResult(data=None, status=RpcQueryStatus.RPC_ERROR, error_message=str(exc))

    async def estimate_gas(self, tx: dict[str, Any]) -> int:
        """Estimates gas limit for transaction."""
        result = await self.call("eth_estimateGas", [tx])
        return int(result, 16)

    async def get_fee_history(
        self,
        block_count: int,
        newest_block: str = "latest",
        reward_percentiles: list[float] | None = None,
    ) -> dict[str, Any]:
        """Queries EIP-1559 fee history."""
        params = [hex(block_count), newest_block, reward_percentiles or [25.0, 50.0, 75.0]]
        return await self.call("eth_feeHistory", params)

    async def send_raw_transaction(self, raw_tx_hex: str) -> str:
        """Submits signed raw transaction. HARD-FAIL closed on Base Mainnet."""
        if self.chain_id == CHAIN_ID_BASE_MAINNET:
            raise MainnetSubmissionBlockedError(
                "HARD SECURITY GATING: Base Mainnet transaction submission is disabled in Phase 6.2A"
            )
        if not self.config.allow_transactions:
            raise MainnetSubmissionBlockedError(
                f"Transaction submission is disabled for network {self.config.network_name} (chain_id={self.chain_id})"
            )

        result = await self.call("eth_sendRawTransaction", [raw_tx_hex])
        return str(result)

    async def get_code(self, address: str, block_tag: str = "latest") -> str:
        """Queries eth_getCode to verify contract deployment."""
        result = await self.call("eth_getCode", [address, block_tag])
        return str(result) if result else "0x"
