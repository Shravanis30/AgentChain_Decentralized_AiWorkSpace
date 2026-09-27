// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {IRevenueDistributor} from "./interfaces/IRevenueDistributor.sol";

/**
 * @title Escrow
 * @notice Production-oriented on-chain escrow foundation for AgentChain tasks.
 * @dev Manages USDC commitments, deterministic identifiers, explicit state machine transitions,
 *      checks-effects-interactions safe transfers, timeout refunds, and platform-managed arbitration.
 *      Enforces chain-specific payment token validation at the constructor level.
 */
contract Escrow is AccessControl, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    bytes32 public constant ARBITRATOR_ROLE = keccak256("ARBITRATOR_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");

    // Verified official Circle USDC token addresses
    address public constant BASE_MAINNET_USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address public constant BASE_SEPOLIA_USDC = 0x036CbD53842c5426634e7929541eC2318f3dCF7e;

    // Supported Chain IDs
    uint256 public constant CHAIN_ID_LOCAL = 31337;
    uint256 public constant CHAIN_ID_BASE_SEPOLIA = 84532;
    uint256 public constant CHAIN_ID_BASE_MAINNET = 8453;

    enum EscrowState {
        NONE, // 0: Uninitialized
        CREATED, // 1: Created, awaiting deposit
        FUNDED, // 2: Funded, awaiting execution lock
        LOCKED, // 3: Execution locked, work in progress
        RELEASED, // 4: Released to beneficiary (terminal)
        REFUNDED, // 5: Refunded to client (terminal)
        DISPUTED, // 6: Disputed, frozen awaiting arbitration
        RESOLVED // 7: Resolved by arbitrator (terminal)
    }

    struct EscrowRecord {
        bytes32 escrowId;
        address client;
        address beneficiary;
        uint256 amount;
        EscrowState state;
        uint256 createdAt;
        uint256 fundedAt;
        uint256 lockedAt;
        uint256 settledAt;
        uint256 executionDeadline;
        bytes32 referenceId;
        bytes32 disputeReasonHash;
    }

    /// @notice The accepted payment token (USDC)
    IERC20 public immutable paymentToken;

    /// @notice Dedicated 85/10/5 RevenueDistributor contract
    address public immutable distributor;

    /// @notice Cumulative active escrow balance currently tracked by the contract
    uint256 public totalEscrowBalance;

    /// @notice Mapping from unique escrowId to EscrowRecord
    mapping(bytes32 => EscrowRecord) private _escrows;

    // Events
    event EscrowCreated(
        bytes32 indexed escrowId,
        address indexed client,
        address indexed beneficiary,
        uint256 amount,
        bytes32 referenceId,
        uint256 executionDeadline
    );
    event EscrowFunded(bytes32 indexed escrowId, address indexed client, uint256 amount);
    event EscrowLocked(bytes32 indexed escrowId, address indexed operator);
    event EscrowReleased(bytes32 indexed escrowId, address indexed beneficiary, uint256 amount);
    event EscrowDistributed(bytes32 indexed escrowId, bytes32 indexed distributionId);
    event EscrowRefunded(bytes32 indexed escrowId, address indexed client, uint256 amount);
    event DisputeOpened(bytes32 indexed escrowId, address indexed initiator, bytes32 indexed reasonHash);
    event DisputeResolved(
        bytes32 indexed escrowId, address indexed arbitrator, uint256 beneficiaryAmount, uint256 clientRefundAmount
    );

    // Custom Errors
    error ZeroAddress();
    error InvalidAmount();
    error InvalidDeadline();
    error SelfPaymentNotAllowed(address account);
    error EscrowAlreadyExists(bytes32 escrowId);
    error EscrowNotFound(bytes32 escrowId);
    error InvalidEscrowState(bytes32 escrowId, EscrowState currentState);
    error UnauthorizedCaller(bytes32 escrowId, address caller);
    error DeadlineNotPassed(bytes32 escrowId, uint256 executionDeadline, uint256 currentTimestamp);
    error DisputeResolutionMismatch(uint256 totalAmount, uint256 resolvedSum);
    error InvalidTokenDecimals(uint8 expected, uint8 actual);
    error UnsupportedChainId(uint256 chainId);
    error InvalidPaymentToken(address provided, address expected);

    /**
     * @notice Initializes the Escrow contract with role holders, payment token, and 85/10/5 distributor.
     * @dev Strictly enforces official Circle USDC validation according to chain ID.
     * @param admin Initial administrator address for AccessControl.
     * @param arbitrator Designated arbitration authority (platform multisig).
     * @param pauser Designated emergency pauser.
     * @param tokenAddress The ERC-20 payment token address (must be official USDC on target chain).
     * @param distributor_ Dedicated 85/10/5 RevenueDistributor contract address.
     */
    constructor(
        address admin,
        address arbitrator,
        address pauser,
        address tokenAddress,
        address distributor_
    ) {
        if (admin == address(0) || arbitrator == address(0) || pauser == address(0) || tokenAddress == address(0)) {
            revert ZeroAddress();
        }

        uint256 chainId = block.chainid;
        if (chainId == CHAIN_ID_BASE_SEPOLIA) {
            if (tokenAddress != BASE_SEPOLIA_USDC) {
                revert InvalidPaymentToken(tokenAddress, BASE_SEPOLIA_USDC);
            }
            if (distributor_ == address(0)) {
                revert ZeroAddress();
            }
        } else if (chainId == CHAIN_ID_BASE_MAINNET) {
            if (tokenAddress != BASE_MAINNET_USDC) {
                revert InvalidPaymentToken(tokenAddress, BASE_MAINNET_USDC);
            }
            if (distributor_ == address(0)) {
                revert ZeroAddress();
            }
        } else if (chainId == CHAIN_ID_LOCAL) {
            // Local Anvil / Foundry testing environment: allow MockUSDC with 6 decimals and optional distributor
        } else {
            revert UnsupportedChainId(chainId);
        }

        uint8 decimals = IERC20Metadata(tokenAddress).decimals();
        if (decimals != 6) {
            revert InvalidTokenDecimals(6, decimals);
        }

        paymentToken = IERC20(tokenAddress);
        distributor = distributor_;

        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ARBITRATOR_ROLE, arbitrator);
        _grantRole(PAUSER_ROLE, pauser);
    }

    /**
     * @notice Computes a deterministic, cross-chain and domain-separated escrow identifier.
     * @param client The address of the client funding the escrow.
     * @param referenceId Bounded identifier of the off-chain task or job.
     * @param salt Additional salt to allow multiple escrows for the same reference ID.
     */
    function computeEscrowId(address client, bytes32 referenceId, uint256 salt) public view returns (bytes32) {
        return keccak256(abi.encode(block.chainid, address(this), client, referenceId, salt));
    }

    /**
     * @notice Creates an un-funded escrow record.
     */
    function createEscrow(
        bytes32 referenceId,
        address beneficiary,
        uint256 amount,
        uint256 executionDeadline,
        uint256 salt
    ) external whenNotPaused returns (bytes32 escrowId) {
        escrowId = _createEscrowRecord(referenceId, beneficiary, amount, executionDeadline, salt, false);
    }

    /**
     * @notice Atomically creates and funds an escrow in a single transaction.
     */
    function createAndFundEscrow(
        bytes32 referenceId,
        address beneficiary,
        uint256 amount,
        uint256 executionDeadline,
        uint256 salt
    ) external nonReentrant whenNotPaused returns (bytes32 escrowId) {
        escrowId = _createEscrowRecord(referenceId, beneficiary, amount, executionDeadline, salt, true);

        totalEscrowBalance += amount;
        emit EscrowFunded(escrowId, msg.sender, amount);

        paymentToken.safeTransferFrom(msg.sender, address(this), amount);
    }

    /**
     * @notice Funds an existing CREATED escrow.
     * @param escrowId The deterministic identifier of the escrow.
     */
    function fundEscrow(bytes32 escrowId) external nonReentrant whenNotPaused {
        EscrowRecord storage escrow = _escrows[escrowId];
        if (escrow.state == EscrowState.NONE) revert EscrowNotFound(escrowId);
        if (escrow.state != EscrowState.CREATED) revert InvalidEscrowState(escrowId, escrow.state);
        if (msg.sender != escrow.client) revert UnauthorizedCaller(escrowId, msg.sender);

        escrow.state = EscrowState.FUNDED;
        escrow.fundedAt = block.timestamp;
        totalEscrowBalance += escrow.amount;

        emit EscrowFunded(escrowId, msg.sender, escrow.amount);

        paymentToken.safeTransferFrom(msg.sender, address(this), escrow.amount);
    }

    /**
     * @notice Locks a FUNDED escrow upon task execution commencement.
     * @param escrowId The deterministic identifier of the escrow.
     */
    function lockEscrow(bytes32 escrowId) external whenNotPaused {
        EscrowRecord storage escrow = _escrows[escrowId];
        if (escrow.state == EscrowState.NONE) revert EscrowNotFound(escrowId);
        if (escrow.state != EscrowState.FUNDED) revert InvalidEscrowState(escrowId, escrow.state);

        bool isAuthorized =
            (msg.sender == escrow.client || msg.sender == escrow.beneficiary || hasRole(DEFAULT_ADMIN_ROLE, msg.sender)
                || hasRole(ARBITRATOR_ROLE, msg.sender));

        if (!isAuthorized) revert UnauthorizedCaller(escrowId, msg.sender);

        escrow.state = EscrowState.LOCKED;
        escrow.lockedAt = block.timestamp;

        emit EscrowLocked(escrowId, msg.sender);
    }

    /**
     * @notice Releases locked or funded escrow funds to the beneficiary.
     * @dev Beneficiary cannot unilaterally call release to prevent unauthorized self-payout.
     * @param escrowId The deterministic identifier of the escrow.
     */
    function releaseEscrow(bytes32 escrowId) external nonReentrant whenNotPaused {
        EscrowRecord storage escrow = _escrows[escrowId];
        if (escrow.state == EscrowState.NONE) revert EscrowNotFound(escrowId);

        if (escrow.state == EscrowState.LOCKED || escrow.state == EscrowState.FUNDED) {
            bool isAuthorized = (msg.sender == escrow.client || hasRole(ARBITRATOR_ROLE, msg.sender));
            if (!isAuthorized) revert UnauthorizedCaller(escrowId, msg.sender);
        } else if (escrow.state == EscrowState.DISPUTED) {
            if (!hasRole(ARBITRATOR_ROLE, msg.sender)) revert UnauthorizedCaller(escrowId, msg.sender);
        } else {
            revert InvalidEscrowState(escrowId, escrow.state);
        }

        uint256 payout = escrow.amount;
        address recipient = escrow.beneficiary;

        escrow.state = EscrowState.RELEASED;
        escrow.settledAt = block.timestamp;
        totalEscrowBalance -= payout;

        emit EscrowReleased(escrowId, recipient, payout);

        if (distributor != address(0)) {
            paymentToken.safeTransfer(distributor, payout);
            bytes32 distId = IRevenueDistributor(distributor).distribute(escrowId, escrow.referenceId, payout, recipient);
            emit EscrowDistributed(escrowId, distId);
        } else {
            paymentToken.safeTransfer(recipient, payout);
        }
    }

    /**
     * @notice Refunds escrow funds to the client.
     * @dev Allowed:
     *      - From CREATED: client can cancel unfunded escrow.
     *      - From FUNDED: client or beneficiary or arbitrator can refund.
     *      - From LOCKED: beneficiary can forfeit/refund; arbitrator can refund;
     *                     client can refund ONLY AFTER executionDeadline passes (block.timestamp >= executionDeadline).
     *      - From DISPUTED: arbitrator can refund.
     * @param escrowId The deterministic identifier of the escrow.
     */
    function refundEscrow(bytes32 escrowId) external nonReentrant whenNotPaused {
        EscrowRecord storage escrow = _escrows[escrowId];
        if (escrow.state == EscrowState.NONE) revert EscrowNotFound(escrowId);

        uint256 refundAmount = escrow.amount;
        address client = escrow.client;

        if (escrow.state == EscrowState.CREATED) {
            if (msg.sender != client && !hasRole(DEFAULT_ADMIN_ROLE, msg.sender)) {
                revert UnauthorizedCaller(escrowId, msg.sender);
            }
            escrow.state = EscrowState.REFUNDED;
            escrow.settledAt = block.timestamp;
            emit EscrowRefunded(escrowId, client, 0);
            return;
        } else if (escrow.state == EscrowState.FUNDED) {
            bool isAuthorized =
                (msg.sender == client || msg.sender == escrow.beneficiary || hasRole(ARBITRATOR_ROLE, msg.sender));
            if (!isAuthorized) revert UnauthorizedCaller(escrowId, msg.sender);
        } else if (escrow.state == EscrowState.LOCKED) {
            if (msg.sender == escrow.beneficiary || hasRole(ARBITRATOR_ROLE, msg.sender)) {
                // Beneficiary forfeiture or arbitrator action
            } else if (msg.sender == client) {
                if (block.timestamp < escrow.executionDeadline) {
                    revert DeadlineNotPassed(escrowId, escrow.executionDeadline, block.timestamp);
                }
            } else {
                revert UnauthorizedCaller(escrowId, msg.sender);
            }
        } else if (escrow.state == EscrowState.DISPUTED) {
            if (!hasRole(ARBITRATOR_ROLE, msg.sender)) revert UnauthorizedCaller(escrowId, msg.sender);
        } else {
            revert InvalidEscrowState(escrowId, escrow.state);
        }

        escrow.state = EscrowState.REFUNDED;
        escrow.settledAt = block.timestamp;
        totalEscrowBalance -= refundAmount;

        emit EscrowRefunded(escrowId, client, refundAmount);

        paymentToken.safeTransfer(client, refundAmount);
    }

    /**
     * @notice Opens a dispute on a locked escrow, freezing timeout refunds pending arbitration.
     * @param escrowId The deterministic identifier of the escrow.
     * @param reasonHash Cryptographic hash of the off-chain dispute claim / evidence.
     */
    function disputeEscrow(bytes32 escrowId, bytes32 reasonHash) external whenNotPaused {
        EscrowRecord storage escrow = _escrows[escrowId];
        if (escrow.state == EscrowState.NONE) revert EscrowNotFound(escrowId);
        if (escrow.state != EscrowState.LOCKED) revert InvalidEscrowState(escrowId, escrow.state);

        bool isAuthorized =
            (msg.sender == escrow.client || msg.sender == escrow.beneficiary || hasRole(ARBITRATOR_ROLE, msg.sender));
        if (!isAuthorized) revert UnauthorizedCaller(escrowId, msg.sender);

        escrow.state = EscrowState.DISPUTED;
        escrow.disputeReasonHash = reasonHash;

        emit DisputeOpened(escrowId, msg.sender, reasonHash);
    }

    /**
     * @notice Resolves a disputed escrow, splitting funds between beneficiary and client.
     * @param escrowId The deterministic identifier of the escrow.
     * @param beneficiaryAmount Amount to disburse to beneficiary in token base units.
     * @param clientRefundAmount Amount to refund to client in token base units.
     */
    function resolveDispute(bytes32 escrowId, uint256 beneficiaryAmount, uint256 clientRefundAmount)
        external
        nonReentrant
        whenNotPaused
    {
        if (!hasRole(ARBITRATOR_ROLE, msg.sender)) revert UnauthorizedCaller(escrowId, msg.sender);

        EscrowRecord storage escrow = _escrows[escrowId];
        if (escrow.state == EscrowState.NONE) revert EscrowNotFound(escrowId);
        if (escrow.state != EscrowState.DISPUTED) revert InvalidEscrowState(escrowId, escrow.state);

        if (beneficiaryAmount + clientRefundAmount != escrow.amount) {
            revert DisputeResolutionMismatch(escrow.amount, beneficiaryAmount + clientRefundAmount);
        }

        address beneficiary = escrow.beneficiary;
        address client = escrow.client;

        escrow.state = EscrowState.RESOLVED;
        escrow.settledAt = block.timestamp;
        totalEscrowBalance -= escrow.amount;

        emit DisputeResolved(escrowId, msg.sender, beneficiaryAmount, clientRefundAmount);

        if (beneficiaryAmount > 0) {
            paymentToken.safeTransfer(beneficiary, beneficiaryAmount);
        }
        if (clientRefundAmount > 0) {
            paymentToken.safeTransfer(client, clientRefundAmount);
        }
    }

    /**
     * @notice Emergency pauses the contract.
     */
    function pause() external {
        if (!hasRole(PAUSER_ROLE, msg.sender) && !hasRole(DEFAULT_ADMIN_ROLE, msg.sender)) {
            revert UnauthorizedCaller(bytes32(0), msg.sender);
        }
        _pause();
    }

    /**
     * @notice Unpauses the contract.
     */
    function unpause() external {
        if (!hasRole(PAUSER_ROLE, msg.sender) && !hasRole(DEFAULT_ADMIN_ROLE, msg.sender)) {
            revert UnauthorizedCaller(bytes32(0), msg.sender);
        }
        _unpause();
    }

    /**
     * @notice Returns the full EscrowRecord for a given escrow identifier.
     */
    function getEscrow(bytes32 escrowId) external view returns (EscrowRecord memory) {
        EscrowRecord memory record = _escrows[escrowId];
        if (record.state == EscrowState.NONE) revert EscrowNotFound(escrowId);
        return record;
    }

    /**
     * @dev Internal helper to validate and store an escrow record.
     */
    function _createEscrowRecord(
        bytes32 referenceId,
        address beneficiary,
        uint256 amount,
        uint256 executionDeadline,
        uint256 salt,
        bool isFunded
    ) internal returns (bytes32 escrowId) {
        if (beneficiary == address(0)) revert ZeroAddress();
        if (beneficiary == msg.sender) revert SelfPaymentNotAllowed(msg.sender);
        if (amount == 0) revert InvalidAmount();
        if (executionDeadline <= block.timestamp) revert InvalidDeadline();

        escrowId = computeEscrowId(msg.sender, referenceId, salt);
        if (_escrows[escrowId].state != EscrowState.NONE) revert EscrowAlreadyExists(escrowId);

        EscrowState initialState = isFunded ? EscrowState.FUNDED : EscrowState.CREATED;
        uint256 fundedTimestamp = isFunded ? block.timestamp : 0;

        _escrows[escrowId] = EscrowRecord({
            escrowId: escrowId,
            client: msg.sender,
            beneficiary: beneficiary,
            amount: amount,
            state: initialState,
            createdAt: block.timestamp,
            fundedAt: fundedTimestamp,
            lockedAt: 0,
            settledAt: 0,
            executionDeadline: executionDeadline,
            referenceId: referenceId,
            disputeReasonHash: bytes32(0)
        });

        emit EscrowCreated(escrowId, msg.sender, beneficiary, amount, referenceId, executionDeadline);
    }
}
