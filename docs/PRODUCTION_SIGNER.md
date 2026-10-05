# AgentChain Production Signer Architecture

> **Status**: `LocalAccountSigner` is active for Anvil and Base Sepolia testnet only.
> A KMS/HSM/MPC signer is **required** before Base Mainnet activation.

---

## 1. Architecture Overview

```
Settlement Service
      |
      v  (creates inert intent record)
Transaction Intent (PostgreSQL)
      |
      v  (outbox dispatch)
Redis Stream / Outbox
      |
      v
Relayer Worker
      |
      v
[TransactionSigner interface]
      |
      +--- LocalAccountSigner  (Anvil / Base Sepolia ONLY)
      |
      +--- KmsAccountSigner    (Production — AWS KMS / GCP Cloud KMS)
      |
      +--- VaultTransitSigner  (Production — HashiCorp Vault)
      |
      +--- MpcSigner           (Production — Fireblocks / Turnkey)
      |
      v
Escrow Smart Contract
```

The application **never** requires private keys in:
- PostgreSQL
- Redis
- API request/response
- Frontend
- Logs (key material is never logged)

---

## 2. TransactionSigner Interface

```python
class TransactionSigner(ABC):
    @abstractmethod
    def get_address(self) -> str:
        """Returns the checksummed Ethereum address controlled by this signer."""
        ...

    @abstractmethod
    def sign_transaction(self, tx_dict: dict) -> str:
        """Signs an EIP-1559 transaction dict, returns raw hex signed tx.

        Args:
            tx_dict: {chainId, from, to, nonce, maxFeePerGas,
                      maxPriorityFeePerGas, gas, data, value}

        Returns:
            Raw signed transaction as hex string.

        Raises:
            RuntimeError: If signer is unhealthy or signing fails.
        """
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """Returns True if the signer is operational.

        Called at relayer startup and by the /health endpoint.
        False must trigger relayer shutdown and alerting.
        """
        ...
```

---

## 3. Current Implementations

### LocalAccountSigner (Testnet Only)

- **Permitted chains**: Anvil (31337), Base Sepolia (84532)
- **Construction guard**: Raises `RuntimeError` if `chain_id=8453` is passed.
- **Sign-time guard**: Refuses to sign any tx with `chainId=8453`.
- **Key storage**: In-memory only, never serialized.
- **Configuration**: `RELAYER_PRIVATE_KEY` environment variable.

```python
signer = LocalAccountSigner(
    private_key_hex=os.environ["RELAYER_PRIVATE_KEY"],
    chain_id=84532,  # Base Sepolia
)
```

### MockSigner (Tests Only)

- Returns zero-filled bytes; no actual signing.
- health_check always returns True.

---

## 4. Production Signer Implementations (Not Yet Built)

Production signing must be implemented before Base Mainnet activation.

### Option A: AWS KMS

```python
class KmsAccountSigner(TransactionSigner):
    """Signs via AWS KMS secp256k1 key."""

    def __init__(self, key_id: str, region: str, chain_id: int):
        import boto3
        self._kms = boto3.client("kms", region_name=region)
        self._key_id = key_id
        self._chain_id = chain_id
        # Derive public key and address from KMS
        self._address = self._derive_address()

    def sign_transaction(self, tx_dict: dict) -> str:
        # Serialize tx to RLP, call KMS Sign API, parse DER-encoded signature
        # Recover R, S, V from DER encoding
        # Re-encode as raw signed tx
        ...

    def health_check(self) -> bool:
        try:
            self._kms.describe_key(KeyId=self._key_id)
            return True
        except Exception:
            return False
```

### Option B: HashiCorp Vault Transit

```python
class VaultTransitSigner(TransactionSigner):
    """Signs via Vault Transit secret engine."""

    def __init__(self, vault_url: str, token: str, key_name: str, chain_id: int):
        import hvac
        self._client = hvac.Client(url=vault_url, token=token)
        self._key_name = key_name
        self._chain_id = chain_id
        self._address = self._derive_address()

    def sign_transaction(self, tx_dict: dict) -> str:
        # Hash tx, call Vault transit/sign, parse result
        ...

    def health_check(self) -> bool:
        return self._client.is_authenticated()
```

### Option C: Fireblocks / Turnkey (MPC)

```python
class FireblocksSigner(TransactionSigner):
    """Signs via Fireblocks MPC service."""
    # Implement using fireblocks-sdk
    ...
```

---

## 5. Security Requirements for Production Signer

| Requirement | Description |
|------------|-------------|
| Key material never in plaintext | Private key bytes never touch application memory |
| Audit trail | Every signing request is logged by the KMS/HSM |
| Signing policy | Only whitelisted operations / chain IDs can be signed |
| IP allowlisting | KMS access restricted to relayer service IP range |
| MFA / approval | High-value or anomalous transactions require human approval |
| HSM-backed | Keys stored in FIPS 140-2 Level 3 or equivalent hardware |
| Rotation capability | Key rotation without application downtime |
| Health monitoring | Signer health_check() exposed via Prometheus `agentchain_signer_healthy` gauge |

---

## 6. LocalAccountSigner Mainnet Guards

Defense-in-depth: LocalAccountSigner refuses Mainnet in two places:

1. **At construction** (`__init__`): raises `RuntimeError` if `chain_id=8453`.
2. **At sign time** (`sign_transaction`): raises `RuntimeError` if tx `chainId=8453`.
3. **Relayer startup** (`verify_startup`): raises if `isinstance(signer, LocalAccountSigner) and chain_id == 8453`.

No configuration path permits a LocalAccountSigner to sign Mainnet transactions.

---

## 7. Signer Health Prometheus Metric

Production signers must emit:

```python
metrics_registry.set_gauge(
    "agentchain_signer_healthy",
    1.0 if signer.health_check() else 0.0,
    {"chain_id": str(chain_id), "signer_type": "kms"},
)
```

Alert: `SignerUnhealthy` fires when `agentchain_signer_healthy == 0`.

---

## 8. Pre-Mainnet Checklist

- [ ] Production signer (KMS/HSM/MPC) implemented and code-reviewed
- [ ] Signing policy restricts to `Escrow` contract on Base Mainnet only
- [ ] Key rotation tested
- [ ] Independent security audit of signer implementation
- [ ] Signer health_check() integrated into relayer health endpoint
- [ ] Prometheus alert `SignerUnhealthy` configured with PagerDuty routing
- [ ] `BLOCK_MAINNET_SETTLEMENT=false` explicitly set only after audit approval
- [ ] `LocalAccountSigner` removed from production container build
