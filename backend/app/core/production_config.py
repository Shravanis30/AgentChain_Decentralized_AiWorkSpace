"""Production configuration validation and environment isolation model.

Enforces strict fail-closed boundaries:
1. Environment separation: 'development', 'testnet', 'production'.
2. Production signer requirement: KMS / HSM / MPC only; raw private keys strictly forbidden.
3. Network safety: Base Mainnet (8453) remains HARD BLOCKED. Missing/unknown chain IDs fail.
4. Role isolation: Explicit role addresses required, Anvil & retired test accounts blocked.
5. Contract address binding: Explicit canonical addresses required with zero implicit fallbacks.
"""

from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, field_validator, model_validator
import re


class EnvironmentType(str, Enum):
    DEVELOPMENT = "development"
    TESTNET = "testnet"
    PRODUCTION = "production"


class ProductionSignerType(str, Enum):
    KMS = "kms"
    AWS_KMS = "aws_kms"
    HSM = "hsm"
    MPC = "mpc"


# Blocked addresses across live/production networks
BLOCKED_PRODUCTION_ADDRESSES = {
    "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266": "Anvil Account #0 (Development Only)",
    "0x70997970C51812dc3A010C7d01b50e0d17dc79C8": "Retired Historical Developer Account",
    "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC": "Anvil Account #2",
    "0x90F79bf6EB2c4f870365E785982E1f101E93b906": "Anvil Account #3",
    "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65": "Anvil Account #4",
    "0x9965507D1a55bcC2695C58ba16FB37d819B0A4df": "Anvil Account #5",
}

# Base Sepolia canonical verified contracts
CANONICAL_BASE_SEPOLIA_CONTRACTS = {
    "agent_registry": "0x96C3945e264A880EbaB5e95fDF786Fc18A4389B5",
    "revenue_distributor": "0x2F2d5Cc40e2C4C32cC86BC5127c2833a7784ee1c",
    "escrow": "0x3e5C09D2123cA106b2E24C4f76bFad4a022640D1",
    "result_notary": "0xed58B4E37f81dFbD08c3d2DeF613286bC8Cf8056",
    "reputation_registry": "0x423856529583F536d5dFaE80f7Bc142075c6Fc71",
    "usdc": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
}

ETH_ADDRESS_REGEX = re.compile(r"^0x[a-fA-F0-9]{40}$")


class ProductionConfigError(ValueError):
    """Raised when configuration violates production or security safety rules."""
    pass


class RoleConfig(BaseModel):
    relayer_role_address: str
    admin_role_address: str
    arbitrator_role_address: Optional[str] = None
    pauser_role_address: Optional[str] = None
    notarizer_role_address: Optional[str] = None

    @field_validator("relayer_role_address", "admin_role_address", mode="before")
    @classmethod
    def validate_address_format(cls, v: str) -> str:
        if not v or not ETH_ADDRESS_REGEX.match(str(v).strip()):
            raise ProductionConfigError(f"Invalid Ethereum address format for role: '{v}'")
        return str(v).strip()


class ContractConfig(BaseModel):
    agent_registry_address: str
    revenue_distributor_address: str
    escrow_address: str
    result_notary_address: str
    reputation_registry_address: str
    usdc_address: str

    @field_validator("*", mode="before")
    @classmethod
    def validate_contract_address(cls, v: str) -> str:
        if not v or not ETH_ADDRESS_REGEX.match(str(v).strip()):
            raise ProductionConfigError(f"Invalid contract address: '{v}'")
        return str(v).strip()


class ProductionEnvironmentConfig(BaseModel):
    app_env: EnvironmentType = Field(default=EnvironmentType.PRODUCTION)
    chain_id: int
    rpc_url: str
    signer_provider: ProductionSignerType
    signer_key_reference: str
    roles: RoleConfig
    contracts: ContractConfig
    debug: bool = Field(default=False)

    # Optional private key field must NEVER be present or set in production config
    raw_private_key: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def pre_validate_environment(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ProductionConfigError("Configuration data must be a dictionary.")

        raw_env = data.get("app_env", "production")
        env_val = raw_env.value if isinstance(raw_env, EnvironmentType) else str(raw_env).lower()
        if env_val not in [e.value for e in EnvironmentType]:
            raise ProductionConfigError(f"Invalid app_env '{env_val}'. Must be one of {[e.value for e in EnvironmentType]}")

        # Check for raw private key injection
        raw_key = data.get("raw_private_key") or data.get("deployer_private_key") or data.get("relayer_private_key")
        if env_val == EnvironmentType.PRODUCTION.value and raw_key:
            raise ProductionConfigError(
                "CRITICAL SECURITY VIOLATION: Raw private key configured in production environment. "
                "Production execution strictly requires KMS, HSM, or MPC signers."
            )

        return data

    @model_validator(mode="after")
    def validate_production_invariants(self) -> "ProductionEnvironmentConfig":
        # 1. Mainnet Hard Block Invariant
        if self.chain_id == 8453:
            raise ProductionConfigError(
                "CRITICAL SAFETY GATE: Base Mainnet (Chain ID 8453) remains HARD BLOCKED. "
                "Production deployment or transaction execution to Mainnet is prohibited."
            )

        # 2. Supported production chain check
        if self.chain_id not in (84532,):
            raise ProductionConfigError(
                f"Unsupported chain ID {self.chain_id} for production-grade execution. "
                f"Currently approved live chain: 84532 (Base Sepolia)."
            )

        # 3. Disallow debug mode in production
        if self.app_env == EnvironmentType.PRODUCTION and self.debug:
            raise ProductionConfigError("Debug mode must be False in production environment.")

        # 4. Signer provider validation
        if self.app_env == EnvironmentType.PRODUCTION:
            if self.signer_provider not in (
                ProductionSignerType.KMS,
                ProductionSignerType.AWS_KMS,
                ProductionSignerType.HSM,
                ProductionSignerType.MPC,
            ):
                raise ProductionConfigError(
                    f"Invalid production signer provider '{self.signer_provider}'. "
                    f"Must be one of {[p.value for p in ProductionSignerType]}."
                )
            if not self.signer_key_reference or len(self.signer_key_reference.strip()) < 5:
                raise ProductionConfigError("A valid signer_key_reference is required for production signer.")

        # 5. Check role addresses against blocked test addresses
        all_roles = [
            ("relayer_role_address", self.roles.relayer_role_address),
            ("admin_role_address", self.roles.admin_role_address),
        ]
        if self.roles.arbitrator_role_address:
            all_roles.append(("arbitrator_role_address", self.roles.arbitrator_role_address))
        if self.roles.pauser_role_address:
            all_roles.append(("pauser_role_address", self.roles.pauser_role_address))
        if self.roles.notarizer_role_address:
            all_roles.append(("notarizer_role_address", self.roles.notarizer_role_address))

        for role_name, addr in all_roles:
            for blocked_addr, reason in BLOCKED_PRODUCTION_ADDRESSES.items():
                if addr.lower() == blocked_addr.lower():
                    raise ProductionConfigError(
                        f"CRITICAL SECURITY VIOLATION: Role '{role_name}' uses blocked address {addr} ({reason})."
                    )

        # 6. Check that relayer and admin roles are not silently identical
        if self.roles.relayer_role_address.lower() == self.roles.admin_role_address.lower():
            raise ProductionConfigError(
                "CRITICAL ROLE SEPARATION VIOLATION: Relayer role and Admin role cannot use the same address. "
                "Production requires distinct operational and administrative roles."
            )

        # 7. Check canonical contracts when target is Base Sepolia
        if self.chain_id == 84532:
            expected = CANONICAL_BASE_SEPOLIA_CONTRACTS
            if self.contracts.agent_registry_address.lower() != expected["agent_registry"].lower():
                raise ProductionConfigError(
                    f"AgentRegistry address mismatch: expected {expected['agent_registry']}, got {self.contracts.agent_registry_address}"
                )
            if self.contracts.revenue_distributor_address.lower() != expected["revenue_distributor"].lower():
                raise ProductionConfigError(
                    f"RevenueDistributor address mismatch: expected {expected['revenue_distributor']}, got {self.contracts.revenue_distributor_address}"
                )
            if self.contracts.escrow_address.lower() != expected["escrow"].lower():
                raise ProductionConfigError(
                    f"Escrow address mismatch: expected {expected['escrow']}, got {self.contracts.escrow_address}"
                )
            if self.contracts.result_notary_address.lower() != expected["result_notary"].lower():
                raise ProductionConfigError(
                    f"ResultNotary address mismatch: expected {expected['result_notary']}, got {self.contracts.result_notary_address}"
                )
            if self.contracts.reputation_registry_address.lower() != expected["reputation_registry"].lower():
                raise ProductionConfigError(
                    f"ReputationRegistry address mismatch: expected {expected['reputation_registry']}, got {self.contracts.reputation_registry_address}"
                )
            if self.contracts.usdc_address.lower() != expected["usdc"].lower():
                raise ProductionConfigError(
                    f"USDC address mismatch: expected {expected['usdc']}, got {self.contracts.usdc_address}"
                )


        return self
