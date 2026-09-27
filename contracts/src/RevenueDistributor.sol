// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {IRevenueDistributor} from "./interfaces/IRevenueDistributor.sol";

/**
 * @title RevenueDistributor
 * @notice Production-grade 85/10/5 economic distribution contract for AgentChain settlements.
 * @dev Receives USDC from Escrow on settlement release and atomically splits funds:
 *      - 85% to Agent Developer
 *      - 10% to Staking Pool
 *      - 5% (exact remainder) to DAO Treasury
 *      Ensures exact financial conservation: developer + staker + dao == gross.
 *      Transient custody only: contract holds 0 balance between distribute calls.
 */
contract RevenueDistributor is IRevenueDistributor, AccessControl, ReentrancyGuard {
    using SafeERC20 for IERC20;

    bytes32 public constant DISTRIBUTOR_ADMIN_ROLE = keccak256("DISTRIBUTOR_ADMIN_ROLE");

    uint256 public constant DISTRIBUTION_VERSION = 1;

    // Supported Chain IDs matching Escrow
    uint256 public constant CHAIN_ID_LOCAL = 31337;
    uint256 public constant CHAIN_ID_BASE_SEPOLIA = 84532;
    uint256 public constant CHAIN_ID_BASE_MAINNET = 8453;

    // Verified official Circle USDC token addresses
    address public constant BASE_MAINNET_USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address public constant BASE_SEPOLIA_USDC = 0x036CbD53842c5426634e7929541eC2318f3dCF7e;

    /// @notice The accepted payment token (USDC)
    IERC20 public immutable paymentTokenContract;

    /// @notice Authorized escrow contract allowed to initiate distributions
    address public authorizedEscrow;

    /// @notice Platform-configured staker recipient (pool address)
    address public immutable stakerRecipientAddress;

    /// @notice Platform-configured DAO / treasury recipient
    address public immutable daoRecipientAddress;

    // Errors
    error ZeroAddress();
    error InvalidAmount();
    error DuplicateRecipient(address duplicate);
    error UnauthorizedCaller(address caller);
    error UnsupportedChainId(uint256 chainId);
    error InvalidPaymentToken(address provided, address expected);
    error InvalidTokenDecimals(uint8 expected, uint8 actual);
    error ConservationInvariantViolated(uint256 sum, uint256 gross);
    error DistributionAlreadyProcessed(bytes32 distributionId);
    error EscrowAlreadySet();

    // Track executed distributions to prevent duplicate execution
    mapping(bytes32 => bool) private _executedDistributions;

    constructor(
        address admin_,
        address escrow_,
        address token_,
        address stakerRecipient_,
        address daoRecipient_
    ) {
        if (
            admin_ == address(0) ||
            token_ == address(0) ||
            stakerRecipient_ == address(0) ||
            daoRecipient_ == address(0)
        ) {
            revert ZeroAddress();
        }

        if (stakerRecipient_ == daoRecipient_) {
            revert DuplicateRecipient(stakerRecipient_);
        }

        if (escrow_ != address(0)) {
            authorizedEscrow = escrow_;
        }

        // Validate chain and payment token
        uint256 currentChain = block.chainid;
        if (currentChain == CHAIN_ID_BASE_MAINNET) {
            if (token_ != BASE_MAINNET_USDC) {
                revert InvalidPaymentToken(token_, BASE_MAINNET_USDC);
            }
        } else if (currentChain == CHAIN_ID_BASE_SEPOLIA) {
            if (token_ != BASE_SEPOLIA_USDC) {
                revert InvalidPaymentToken(token_, BASE_SEPOLIA_USDC);
            }
        } else if (currentChain == CHAIN_ID_LOCAL) {
            // Local chain: token must have 6 decimals (matching official USDC)
            try IERC20Metadata(token_).decimals() returns (uint8 dec) {
                if (dec != 6) {
                    revert InvalidTokenDecimals(6, dec);
                }
            } catch {
                revert InvalidPaymentToken(token_, address(0));
            }
        } else {
            revert UnsupportedChainId(currentChain);
        }

        _grantRole(DEFAULT_ADMIN_ROLE, admin_);
        _grantRole(DISTRIBUTOR_ADMIN_ROLE, admin_);

        authorizedEscrow = escrow_;
        paymentTokenContract = IERC20(token_);
        stakerRecipientAddress = stakerRecipient_;
        daoRecipientAddress = daoRecipient_;
    }

    event EscrowContractBound(address indexed escrowContract);

    /**
     * @notice Binds the authorized Escrow contract if not set during construction.
     * @dev Can only be called once by an account with DEFAULT_ADMIN_ROLE.
     */
    function setEscrowContract(address escrow_) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (escrow_ == address(0)) revert ZeroAddress();
        if (authorizedEscrow != address(0)) revert EscrowAlreadySet();
        authorizedEscrow = escrow_;
        emit EscrowContractBound(escrow_);
    }

    /**
     * @notice Distributes escrow gross amount across Developer (85%), Staker (10%), and DAO (5% + remainder).
     * @dev Called atomically by the authorized Escrow contract during releaseEscrow.
     *      Gross amount must already have been transferred to this contract by Escrow.
     * @param escrowId The deterministic identifier of the releasing escrow.
     * @param referenceId Reference/settlement ID associated with the task.
     * @param grossAmount Total payout amount in USDC base units.
     * @param developerRecipient Address of the authorized agent developer beneficiary.
     * @return distributionId Deterministic unique identifier of the executed distribution.
     */
    function distribute(
        bytes32 escrowId,
        bytes32 referenceId,
        uint256 grossAmount,
        address developerRecipient
    ) external override nonReentrant returns (bytes32 distributionId) {
        if (authorizedEscrow == address(0) || msg.sender != authorizedEscrow) {
            revert UnauthorizedCaller(msg.sender);
        }

        if (grossAmount == 0) {
            revert InvalidAmount();
        }

        if (developerRecipient == address(0)) {
            revert ZeroAddress();
        }

        if (developerRecipient == stakerRecipientAddress || developerRecipient == daoRecipientAddress) {
            revert DuplicateRecipient(developerRecipient);
        }

        distributionId = keccak256(
            abi.encode(block.chainid, address(this), escrowId, grossAmount, DISTRIBUTION_VERSION)
        );

        if (_executedDistributions[distributionId]) {
            revert DistributionAlreadyProcessed(distributionId);
        }
        _executedDistributions[distributionId] = true;

        // Deterministic integer arithmetic: 85% developer, 10% staker, remainder to DAO
        uint256 devAmount = (grossAmount * 85) / 100;
        uint256 stkAmount = (grossAmount * 10) / 100;
        uint256 daoAmount = grossAmount - devAmount - stkAmount;

        // Conservation assertion
        if (devAmount + stkAmount + daoAmount != grossAmount) {
            revert ConservationInvariantViolated(devAmount + stkAmount + daoAmount, grossAmount);
        }

        emit DistributionExecuted(
            distributionId,
            escrowId,
            referenceId,
            grossAmount,
            developerRecipient,
            devAmount,
            stakerRecipientAddress,
            stkAmount,
            daoRecipientAddress,
            daoAmount,
            DISTRIBUTION_VERSION
        );

        // Safe transfers
        paymentTokenContract.safeTransfer(developerRecipient, devAmount);
        paymentTokenContract.safeTransfer(stakerRecipientAddress, stkAmount);
        paymentTokenContract.safeTransfer(daoRecipientAddress, daoAmount);
    }

    // View functions implementing IRevenueDistributor
    function paymentToken() external view override returns (address) {
        return address(paymentTokenContract);
    }

    function stakerRecipient() external view override returns (address) {
        return stakerRecipientAddress;
    }

    function daoRecipient() external view override returns (address) {
        return daoRecipientAddress;
    }

    function escrowContract() external view override returns (address) {
        return authorizedEscrow;
    }

    function distributionVersion() external pure override returns (uint256) {
        return DISTRIBUTION_VERSION;
    }

    function isDistributionProcessed(bytes32 distributionId) external view returns (bool) {
        return _executedDistributions[distributionId];
    }
}
