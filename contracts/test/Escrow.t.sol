// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, console2} from "forge-std/Test.sol";
import {Escrow} from "../src/Escrow.sol";
import {MockUSDC} from "./mocks/MockUSDC.sol";
import {ReentrantToken} from "./mocks/ReentrantToken.sol";
import {FalseReturningToken} from "./mocks/FalseReturningToken.sol";
import {RevertingToken} from "./mocks/RevertingToken.sol";
import {FeeOnTransferToken} from "./mocks/FeeOnTransferToken.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/**
 * @title EscrowTest
 * @notice Adversarially hardened test suite covering token boundaries, access control, state machine,
 *         dispute accounting, deadline boundaries, reentrancy vectors, pause controls, and property fuzzing.
 */
contract EscrowTest is Test {
    Escrow public escrow;
    MockUSDC public usdc;

    address public admin = address(0xAD);
    address public arbitrator = address(0xAB);
    address public pauser = address(0xEE);
    address public client = address(0xC1);
    address public beneficiary = address(0xB1);
    address public stranger = address(0x99);

    uint256 public constant INITIAL_BALANCE = 10_000_000 * 1e6; // 10M USDC
    uint256 public constant DEFAULT_AMOUNT = 500 * 1e6; // 500 USDC
    bytes32 public constant DEFAULT_REF = keccak256("task-orchestration-001");
    uint256 public constant DEADLINE_DURATION = 1 days;

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
    event EscrowRefunded(bytes32 indexed escrowId, address indexed client, uint256 amount);
    event DisputeOpened(bytes32 indexed escrowId, address indexed initiator, bytes32 indexed reasonHash);
    event DisputeResolved(
        bytes32 indexed escrowId, address indexed arbitrator, uint256 beneficiaryAmount, uint256 clientRefundAmount
    );

    function setUp() public {
        // Run on default Foundry test chain 31337
        vm.chainId(31337);
        usdc = new MockUSDC();
        escrow = new Escrow(admin, arbitrator, pauser, address(usdc), address(0));

        // Fund client and stranger with USDC
        usdc.mint(client, INITIAL_BALANCE);
        usdc.mint(stranger, INITIAL_BALANCE);

        // Approvals
        vm.prank(client);
        usdc.approve(address(escrow), type(uint256).max);

        vm.prank(stranger);
        usdc.approve(address(escrow), type(uint256).max);
    }

    // ==========================================
    // 1. PAYMENT TOKEN TRUST BOUNDARY TESTS
    // ==========================================

    function test_TokenBoundary_AcceptsOfficialBaseSepoliaUSDC() public {
        vm.chainId(84532);
        // Using official Base Sepolia USDC address
        address officialSepoliaUSDC = 0x036CbD53842c5426634e7929541eC2318f3dCF7e;
        address mockDistributor = address(0xD15);

        // Mock bytecode and decimals for officialSepoliaUSDC at this address
        vm.mockCall(officialSepoliaUSDC, abi.encodeWithSignature("decimals()"), abi.encode(uint8(6)));

        Escrow sepoliaEscrow = new Escrow(admin, arbitrator, pauser, officialSepoliaUSDC, mockDistributor);
        assertEq(address(sepoliaEscrow.paymentToken()), officialSepoliaUSDC);
    }

    function test_TokenBoundary_RejectsArbitraryTokenOnBaseSepolia() public {
        vm.chainId(84532);
        address arbitraryToken = address(0x1234567890123456789012345678901234567890);
        address mockDistributor = address(0xD15);

        vm.expectRevert(
            abi.encodeWithSelector(
                Escrow.InvalidPaymentToken.selector, arbitraryToken, 0x036CbD53842c5426634e7929541eC2318f3dCF7e
            )
        );
        new Escrow(admin, arbitrator, pauser, arbitraryToken, mockDistributor);
    }

    function test_TokenBoundary_RejectsMainnetUSDCOnBaseSepolia() public {
        vm.chainId(84532);
        address mainnetUSDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
        address mockDistributor = address(0xD15);

        vm.expectRevert(
            abi.encodeWithSelector(
                Escrow.InvalidPaymentToken.selector, mainnetUSDC, 0x036CbD53842c5426634e7929541eC2318f3dCF7e
            )
        );
        new Escrow(admin, arbitrator, pauser, mainnetUSDC, mockDistributor);
    }

    function test_TokenBoundary_AcceptsOfficialBaseMainnetUSDC() public {
        vm.chainId(8453);
        address officialMainnetUSDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
        address mockDistributor = address(0xD15);

        vm.mockCall(officialMainnetUSDC, abi.encodeWithSignature("decimals()"), abi.encode(uint8(6)));

        Escrow mainnetEscrow = new Escrow(admin, arbitrator, pauser, officialMainnetUSDC, mockDistributor);
        assertEq(address(mainnetEscrow.paymentToken()), officialMainnetUSDC);
    }

    function test_TokenBoundary_RejectsArbitraryTokenOnBaseMainnet() public {
        vm.chainId(8453);
        address arbitraryToken = address(0x1234567890123456789012345678901234567890);
        address mockDistributor = address(0xD15);

        vm.expectRevert(
            abi.encodeWithSelector(
                Escrow.InvalidPaymentToken.selector, arbitraryToken, 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
            )
        );
        new Escrow(admin, arbitrator, pauser, arbitraryToken, mockDistributor);
    }

    function test_TokenBoundary_RejectsUnsupportedChainId() public {
        vm.chainId(1); // Ethereum Mainnet (not supported)
        MockUSDC token = new MockUSDC();

        vm.expectRevert(abi.encodeWithSelector(Escrow.UnsupportedChainId.selector, 1));
        new Escrow(admin, arbitrator, pauser, address(token), address(0));
    }

    function test_TokenBoundary_RejectsWrongDecimalsOnLocal() public {
        vm.chainId(31337);
        address badToken = address(0xBADD);
        vm.mockCall(
            badToken,
            abi.encodeWithSignature("decimals()"),
            abi.encode(uint8(18)) // 18 decimals instead of 6
        );

        vm.expectRevert(abi.encodeWithSelector(Escrow.InvalidTokenDecimals.selector, 6, 18));
        new Escrow(admin, arbitrator, pauser, badToken, address(0));
    }

    // ==========================================
    // 2. CONSTRUCTOR & INITIALIZATION TESTS
    // ==========================================

    function test_Constructor_Success() public view {
        assertEq(address(escrow.paymentToken()), address(usdc));
        assertTrue(escrow.hasRole(escrow.DEFAULT_ADMIN_ROLE(), admin));
        assertTrue(escrow.hasRole(escrow.ARBITRATOR_ROLE(), arbitrator));
        assertTrue(escrow.hasRole(escrow.PAUSER_ROLE(), pauser));
        assertEq(escrow.totalEscrowBalance(), 0);
    }

    function test_Constructor_RevertOnZeroAddress() public {
        vm.expectRevert(Escrow.ZeroAddress.selector);
        new Escrow(address(0), arbitrator, pauser, address(usdc), address(0));

        vm.expectRevert(Escrow.ZeroAddress.selector);
        new Escrow(admin, address(0), pauser, address(usdc), address(0));

        vm.expectRevert(Escrow.ZeroAddress.selector);
        new Escrow(admin, arbitrator, address(0), address(usdc), address(0));

        vm.expectRevert(Escrow.ZeroAddress.selector);
        new Escrow(admin, arbitrator, pauser, address(0), address(0));

        vm.chainId(84532);
        address officialSepoliaUSDC = 0x036CbD53842c5426634e7929541eC2318f3dCF7e;
        vm.mockCall(officialSepoliaUSDC, abi.encodeWithSignature("decimals()"), abi.encode(uint8(6)));
        vm.expectRevert(Escrow.ZeroAddress.selector);
        new Escrow(admin, arbitrator, pauser, officialSepoliaUSDC, address(0));
    }

    // ==========================================
    // 3. ACCESS CONTROL HARNESS TESTS
    // ==========================================

    function test_AccessControl_AdminCannotRelease() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        // Admin does NOT have release authority
        vm.prank(admin);
        vm.expectRevert(abi.encodeWithSelector(Escrow.UnauthorizedCaller.selector, escrowId, admin));
        escrow.releaseEscrow(escrowId);
    }

    function test_AccessControl_AdminCannotRefundLocked() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        // Admin cannot refund a locked escrow
        vm.prank(admin);
        vm.expectRevert(abi.encodeWithSelector(Escrow.UnauthorizedCaller.selector, escrowId, admin));
        escrow.refundEscrow(escrowId);
    }

    function test_AccessControl_AdminCannotResolveDispute() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(client);
        escrow.disputeEscrow(escrowId, keccak256("dispute"));

        // Admin cannot resolve dispute (only arbitrator)
        vm.prank(admin);
        vm.expectRevert(abi.encodeWithSelector(Escrow.UnauthorizedCaller.selector, escrowId, admin));
        escrow.resolveDispute(escrowId, DEFAULT_AMOUNT, 0);
    }

    function test_AccessControl_PauserCannotReleaseOrResolve() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(pauser);
        vm.expectRevert(abi.encodeWithSelector(Escrow.UnauthorizedCaller.selector, escrowId, pauser));
        escrow.releaseEscrow(escrowId);

        vm.prank(client);
        escrow.disputeEscrow(escrowId, keccak256("dispute"));

        vm.prank(pauser);
        vm.expectRevert(abi.encodeWithSelector(Escrow.UnauthorizedCaller.selector, escrowId, pauser));
        escrow.resolveDispute(escrowId, DEFAULT_AMOUNT, 0);
    }

    function test_AccessControl_ArbitratorCannotPause() public {
        vm.prank(arbitrator);
        vm.expectRevert(abi.encodeWithSelector(Escrow.UnauthorizedCaller.selector, bytes32(0), arbitrator));
        escrow.pause();
    }

    function test_AccessControl_ArbitratorCannotUnpause() public {
        vm.prank(pauser);
        escrow.pause();

        vm.prank(arbitrator);
        vm.expectRevert(abi.encodeWithSelector(Escrow.UnauthorizedCaller.selector, bytes32(0), arbitrator));
        escrow.unpause();
    }

    function test_AccessControl_UnauthorizedCannotGrantArbitrator() public {
        bytes32 adminRole = escrow.DEFAULT_ADMIN_ROLE();
        bytes32 arbitratorRole = escrow.ARBITRATOR_ROLE();

        vm.prank(client);
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, client, adminRole)
        );
        escrow.grantRole(arbitratorRole, client);
    }

    // ==========================================
    // 4. CREATION & FUNDING TESTS
    // ==========================================

    function test_CreateEscrow_SeparateFund() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        bytes32 expectedId = escrow.computeEscrowId(client, DEFAULT_REF, 0);

        vm.expectEmit(true, true, true, true);
        emit EscrowCreated(expectedId, client, beneficiary, DEFAULT_AMOUNT, DEFAULT_REF, deadline);

        vm.prank(client);
        bytes32 escrowId = escrow.createEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);
        assertEq(escrowId, expectedId);

        Escrow.EscrowRecord memory rec = escrow.getEscrow(escrowId);
        assertEq(rec.client, client);
        assertEq(rec.beneficiary, beneficiary);
        assertEq(rec.amount, DEFAULT_AMOUNT);
        assertEq(uint8(rec.state), uint8(Escrow.EscrowState.CREATED));
        assertEq(rec.executionDeadline, deadline);
        assertEq(rec.fundedAt, 0);
        assertEq(escrow.totalEscrowBalance(), 0);

        // Fund the created escrow
        vm.expectEmit(true, true, false, true);
        emit EscrowFunded(escrowId, client, DEFAULT_AMOUNT);

        vm.prank(client);
        escrow.fundEscrow(escrowId);

        rec = escrow.getEscrow(escrowId);
        assertEq(uint8(rec.state), uint8(Escrow.EscrowState.FUNDED));
        assertEq(rec.fundedAt, block.timestamp);
        assertEq(escrow.totalEscrowBalance(), DEFAULT_AMOUNT);
        assertEq(usdc.balanceOf(address(escrow)), DEFAULT_AMOUNT);
    }

    function test_CreateAndFundEscrow_Atomic() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        bytes32 expectedId = escrow.computeEscrowId(client, DEFAULT_REF, 0);

        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);
        assertEq(escrowId, expectedId);

        Escrow.EscrowRecord memory rec = escrow.getEscrow(escrowId);
        assertEq(uint8(rec.state), uint8(Escrow.EscrowState.FUNDED));
        assertEq(rec.amount, DEFAULT_AMOUNT);
        assertEq(escrow.totalEscrowBalance(), DEFAULT_AMOUNT);
        assertEq(usdc.balanceOf(address(escrow)), DEFAULT_AMOUNT);
    }

    function test_CreateEscrow_RevertOnZeroAmount() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        vm.expectRevert(Escrow.InvalidAmount.selector);
        escrow.createEscrow(DEFAULT_REF, beneficiary, 0, deadline, 0);
    }

    function test_CreateEscrow_RevertOnZeroBeneficiary() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        vm.expectRevert(Escrow.ZeroAddress.selector);
        escrow.createEscrow(DEFAULT_REF, address(0), DEFAULT_AMOUNT, deadline, 0);
    }

    function test_CreateEscrow_RevertOnSelfPayment() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        vm.expectRevert(abi.encodeWithSelector(Escrow.SelfPaymentNotAllowed.selector, client));
        escrow.createEscrow(DEFAULT_REF, client, DEFAULT_AMOUNT, deadline, 0);
    }

    function test_CreateEscrow_RevertOnDuplicateId() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 id = escrow.createEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        vm.expectRevert(abi.encodeWithSelector(Escrow.EscrowAlreadyExists.selector, id));
        escrow.createEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);
    }

    // ==========================================
    // 5. DEADLINE BOUNDARY TESTS
    // ==========================================

    function test_Deadline_RevertAtOrBeforeCurrentTimestamp() public {
        // Exactly at block.timestamp reverts
        vm.prank(client);
        vm.expectRevert(Escrow.InvalidDeadline.selector);
        escrow.createEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, block.timestamp, 0);

        // Before block.timestamp reverts
        vm.prank(client);
        vm.expectRevert(Escrow.InvalidDeadline.selector);
        escrow.createEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, block.timestamp - 1, 0);

        // Zero deadline reverts
        vm.prank(client);
        vm.expectRevert(Escrow.InvalidDeadline.selector);
        escrow.createEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, 0, 0);
    }

    function test_Deadline_OneSecondBeforeDeadlineReverts() public {
        uint256 deadline = block.timestamp + 1000;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        // Warp to exactly deadline - 1
        vm.warp(deadline - 1);

        vm.prank(client);
        vm.expectRevert(abi.encodeWithSelector(Escrow.DeadlineNotPassed.selector, escrowId, deadline, deadline - 1));
        escrow.refundEscrow(escrowId);
    }

    function test_Deadline_ExactAtDeadlineRefundSucceeds() public {
        uint256 deadline = block.timestamp + 1000;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        // Warp exactly to deadline: block.timestamp == executionDeadline
        vm.warp(deadline);

        uint256 preBal = usdc.balanceOf(client);
        vm.prank(client);
        escrow.refundEscrow(escrowId);

        assertEq(usdc.balanceOf(client), preBal + DEFAULT_AMOUNT);
        assertEq(uint8(escrow.getEscrow(escrowId).state), uint8(Escrow.EscrowState.REFUNDED));
    }

    function test_Deadline_OneSecondAfterDeadlineRefundSucceeds() public {
        uint256 deadline = block.timestamp + 1000;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        // Warp to deadline + 1
        vm.warp(deadline + 1);

        uint256 preBal = usdc.balanceOf(client);
        vm.prank(client);
        escrow.refundEscrow(escrowId);

        assertEq(usdc.balanceOf(client), preBal + DEFAULT_AMOUNT);
    }

    function test_Deadline_ExtremelyLargeDeadlineSafe() public {
        uint256 hugeDeadline = block.timestamp + 3650 days; // 10 years
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, hugeDeadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        // Early refund fails
        vm.prank(client);
        vm.expectRevert(
            abi.encodeWithSelector(Escrow.DeadlineNotPassed.selector, escrowId, hugeDeadline, block.timestamp)
        );
        escrow.refundEscrow(escrowId);

        // Beneficiary forfeiture works at any time
        vm.prank(beneficiary);
        escrow.refundEscrow(escrowId);
        assertEq(uint8(escrow.getEscrow(escrowId).state), uint8(Escrow.EscrowState.REFUNDED));
    }

    // ==========================================
    // 6. DISPUTE & ARBITRATION EXPLICIT ACCOUNTING
    // ==========================================

    function test_Dispute_Resolve100PercentBeneficiary() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(client);
        escrow.disputeEscrow(escrowId, keccak256("dispute"));

        uint256 preBeneficiary = usdc.balanceOf(beneficiary);
        uint256 preClient = usdc.balanceOf(client);

        vm.prank(arbitrator);
        escrow.resolveDispute(escrowId, DEFAULT_AMOUNT, 0);

        assertEq(usdc.balanceOf(beneficiary), preBeneficiary + DEFAULT_AMOUNT);
        assertEq(usdc.balanceOf(client), preClient);
        assertEq(escrow.totalEscrowBalance(), 0);
    }

    function test_Dispute_Resolve100PercentClient() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(beneficiary);
        escrow.disputeEscrow(escrowId, keccak256("dispute"));

        uint256 preBeneficiary = usdc.balanceOf(beneficiary);
        uint256 preClient = usdc.balanceOf(client);

        vm.prank(arbitrator);
        escrow.resolveDispute(escrowId, 0, DEFAULT_AMOUNT);

        assertEq(usdc.balanceOf(beneficiary), preBeneficiary);
        assertEq(usdc.balanceOf(client), preClient + DEFAULT_AMOUNT);
        assertEq(escrow.totalEscrowBalance(), 0);
    }

    function test_Dispute_Resolve5050Split() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(client);
        escrow.disputeEscrow(escrowId, keccak256("dispute"));

        uint256 half = DEFAULT_AMOUNT / 2;
        vm.prank(arbitrator);
        escrow.resolveDispute(escrowId, half, half);

        assertEq(uint8(escrow.getEscrow(escrowId).state), uint8(Escrow.EscrowState.RESOLVED));
        assertEq(escrow.totalEscrowBalance(), 0);
    }

    function test_Dispute_RevertIfSumExceedsAmount() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(client);
        escrow.disputeEscrow(escrowId, keccak256("dispute"));

        vm.prank(arbitrator);
        vm.expectRevert(
            abi.encodeWithSelector(Escrow.DisputeResolutionMismatch.selector, DEFAULT_AMOUNT, DEFAULT_AMOUNT + 10)
        );
        escrow.resolveDispute(escrowId, DEFAULT_AMOUNT, 10);
    }

    function test_Dispute_RevertIfSumLessThanAmount() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(client);
        escrow.disputeEscrow(escrowId, keccak256("dispute"));

        vm.prank(arbitrator);
        vm.expectRevert(
            abi.encodeWithSelector(Escrow.DisputeResolutionMismatch.selector, DEFAULT_AMOUNT, DEFAULT_AMOUNT - 10)
        );
        escrow.resolveDispute(escrowId, DEFAULT_AMOUNT - 10, 0);
    }

    function test_Dispute_RevertOnRepeatedResolution() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(client);
        escrow.disputeEscrow(escrowId, keccak256("dispute"));

        vm.prank(arbitrator);
        escrow.resolveDispute(escrowId, DEFAULT_AMOUNT, 0);

        // Second resolution must revert
        vm.prank(arbitrator);
        vm.expectRevert(
            abi.encodeWithSelector(Escrow.InvalidEscrowState.selector, escrowId, Escrow.EscrowState.RESOLVED)
        );
        escrow.resolveDispute(escrowId, DEFAULT_AMOUNT, 0);
    }

    // ==========================================
    // 7. REPLAY & TERMINAL TRANSITIONS
    // ==========================================

    function test_Replay_ReleaseTwiceReverts() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(client);
        escrow.releaseEscrow(escrowId);

        vm.prank(client);
        vm.expectRevert(
            abi.encodeWithSelector(Escrow.InvalidEscrowState.selector, escrowId, Escrow.EscrowState.RELEASED)
        );
        escrow.releaseEscrow(escrowId);
    }

    function test_Replay_RefundTwiceReverts() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.refundEscrow(escrowId);

        vm.prank(client);
        vm.expectRevert(
            abi.encodeWithSelector(Escrow.InvalidEscrowState.selector, escrowId, Escrow.EscrowState.REFUNDED)
        );
        escrow.refundEscrow(escrowId);
    }

    function test_Replay_RefundAfterReleaseReverts() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(client);
        escrow.releaseEscrow(escrowId);

        vm.prank(client);
        vm.expectRevert(
            abi.encodeWithSelector(Escrow.InvalidEscrowState.selector, escrowId, Escrow.EscrowState.RELEASED)
        );
        escrow.refundEscrow(escrowId);
    }

    function test_Replay_ReleaseAfterRefundReverts() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.refundEscrow(escrowId);

        vm.prank(client);
        vm.expectRevert(
            abi.encodeWithSelector(Escrow.InvalidEscrowState.selector, escrowId, Escrow.EscrowState.REFUNDED)
        );
        escrow.releaseEscrow(escrowId);
    }

    function test_Replay_DisputeAfterReleaseReverts() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(client);
        escrow.releaseEscrow(escrowId);

        vm.prank(client);
        vm.expectRevert(
            abi.encodeWithSelector(Escrow.InvalidEscrowState.selector, escrowId, Escrow.EscrowState.RELEASED)
        );
        escrow.disputeEscrow(escrowId, keccak256("dispute"));
    }

    function test_Replay_DisputeAfterRefundReverts() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.refundEscrow(escrowId);

        vm.prank(client);
        vm.expectRevert(
            abi.encodeWithSelector(Escrow.InvalidEscrowState.selector, escrowId, Escrow.EscrowState.REFUNDED)
        );
        escrow.disputeEscrow(escrowId, keccak256("dispute"));
    }

    function test_Replay_LockAfterReleaseReverts() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.releaseEscrow(escrowId);

        vm.prank(client);
        vm.expectRevert(
            abi.encodeWithSelector(Escrow.InvalidEscrowState.selector, escrowId, Escrow.EscrowState.RELEASED)
        );
        escrow.lockEscrow(escrowId);
    }

    // ==========================================
    // 8. PAUSE SAFETY TESTS
    // ==========================================

    function test_Pause_BlocksAllOperations() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(pauser);
        escrow.pause();

        assertTrue(escrow.paused());

        // createEscrow
        vm.prank(client);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        escrow.createEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 1);

        // createAndFundEscrow
        vm.prank(client);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 2);

        // fundEscrow
        vm.prank(client);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        escrow.fundEscrow(escrowId);

        // lockEscrow
        vm.prank(client);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        escrow.lockEscrow(escrowId);

        // releaseEscrow
        vm.prank(client);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        escrow.releaseEscrow(escrowId);

        // refundEscrow
        vm.prank(client);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        escrow.refundEscrow(escrowId);

        // disputeEscrow
        vm.prank(client);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        escrow.disputeEscrow(escrowId, keccak256("dispute"));

        // resolveDispute
        vm.prank(arbitrator);
        vm.expectRevert(Pausable.EnforcedPause.selector);
        escrow.resolveDispute(escrowId, DEFAULT_AMOUNT, 0);

        // View functions STILL WORK while paused
        Escrow.EscrowRecord memory rec = escrow.getEscrow(escrowId);
        assertEq(rec.amount, DEFAULT_AMOUNT);

        // Unpause resumes operations cleanly
        vm.prank(pauser);
        escrow.unpause();

        assertFalse(escrow.paused());
        vm.prank(client);
        escrow.lockEscrow(escrowId);
        assertEq(uint8(escrow.getEscrow(escrowId).state), uint8(Escrow.EscrowState.LOCKED));
    }

    // ==========================================
    // 9. REENTRANCY ATTACK TESTS
    // ==========================================

    function test_Reentrancy_AttackOnReleaseBlocked() public {
        ReentrantToken badToken = new ReentrantToken();
        Escrow reentrantEscrow = new Escrow(admin, arbitrator, pauser, address(badToken), address(0));

        badToken.mint(client, INITIAL_BALANCE);
        vm.prank(client);
        badToken.approve(address(reentrantEscrow), type(uint256).max);

        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = reentrantEscrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        reentrantEscrow.lockEscrow(escrowId);

        // Target: re-enter release during transfer
        badToken.setAttackConfig(
            address(reentrantEscrow), escrowId, ReentrantToken.AttackTarget.Release, ReentrantToken.AttackTarget.None
        );

        vm.prank(client);
        vm.expectRevert();
        reentrantEscrow.releaseEscrow(escrowId);
    }

    function test_Reentrancy_AttackOnRefundBlocked() public {
        ReentrantToken badToken = new ReentrantToken();
        Escrow reentrantEscrow = new Escrow(admin, arbitrator, pauser, address(badToken), address(0));

        badToken.mint(client, INITIAL_BALANCE);
        vm.prank(client);
        badToken.approve(address(reentrantEscrow), type(uint256).max);

        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = reentrantEscrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        // Target: re-enter refund during transfer
        badToken.setAttackConfig(
            address(reentrantEscrow), escrowId, ReentrantToken.AttackTarget.Refund, ReentrantToken.AttackTarget.None
        );

        vm.prank(client);
        vm.expectRevert();
        reentrantEscrow.refundEscrow(escrowId);
    }

    function test_Reentrancy_AttackOnResolveDisputeBlocked() public {
        ReentrantToken badToken = new ReentrantToken();
        Escrow reentrantEscrow = new Escrow(admin, arbitrator, pauser, address(badToken), address(0));

        badToken.mint(client, INITIAL_BALANCE);
        vm.prank(client);
        badToken.approve(address(reentrantEscrow), type(uint256).max);

        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = reentrantEscrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        reentrantEscrow.lockEscrow(escrowId);

        vm.prank(client);
        reentrantEscrow.disputeEscrow(escrowId, keccak256("dispute"));

        // Target: re-enter resolveDispute during transfer
        badToken.setAttackConfig(
            address(reentrantEscrow),
            escrowId,
            ReentrantToken.AttackTarget.ResolveDispute,
            ReentrantToken.AttackTarget.None
        );

        vm.prank(arbitrator);
        vm.expectRevert();
        reentrantEscrow.resolveDispute(escrowId, DEFAULT_AMOUNT, 0);
    }

    // ==========================================
    // 10. TOKEN FAILURE & NON-STANDARD BEHAVIOR
    // ==========================================

    function test_TokenBehavior_FalseReturningTokenFailsSafely() public {
        FalseReturningToken falseToken = new FalseReturningToken();
        Escrow fEscrow = new Escrow(admin, arbitrator, pauser, address(falseToken), address(0));

        falseToken.mint(client, INITIAL_BALANCE);
        vm.prank(client);
        falseToken.approve(address(fEscrow), type(uint256).max);

        // Trigger failure on transferFrom
        falseToken.setFailureConfig(false, true);

        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        vm.expectRevert(abi.encodeWithSelector(SafeERC20.SafeERC20FailedOperation.selector, address(falseToken)));
        fEscrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);
    }

    function test_TokenBehavior_RevertingTokenFailsSafely() public {
        RevertingToken revToken = new RevertingToken();
        Escrow rEscrow = new Escrow(admin, arbitrator, pauser, address(revToken), address(0));

        revToken.mint(client, INITIAL_BALANCE);
        vm.prank(client);
        revToken.approve(address(rEscrow), type(uint256).max);

        // Trigger revert on transferFrom
        revToken.setRevertConfig(false, true);

        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        vm.expectRevert(RevertingToken.TransferReverted.selector);
        rEscrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);
    }

    // ==========================================
    // 11. MULTIPLE SIMULTANEOUS ESCROWS & FUZZING
    // ==========================================

    function test_MultipleSimultaneousEscrows_AccountingConsistency() public {
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.startPrank(client);
        bytes32 id1 = escrow.createAndFundEscrow(keccak256("task-1"), beneficiary, 100 * 1e6, deadline, 1);
        bytes32 id2 = escrow.createAndFundEscrow(keccak256("task-2"), beneficiary, 200 * 1e6, deadline, 2);
        bytes32 id3 = escrow.createAndFundEscrow(keccak256("task-3"), beneficiary, 300 * 1e6, deadline, 3);
        vm.stopPrank();

        assertEq(escrow.totalEscrowBalance(), 600 * 1e6);
        assertEq(usdc.balanceOf(address(escrow)), 600 * 1e6);

        // Release id2
        vm.prank(client);
        escrow.releaseEscrow(id2);
        assertEq(escrow.totalEscrowBalance(), 400 * 1e6);

        // Refund id1
        vm.prank(client);
        escrow.refundEscrow(id1);
        assertEq(escrow.totalEscrowBalance(), 300 * 1e6);

        // Lock & dispute id3
        vm.prank(client);
        escrow.lockEscrow(id3);
        vm.prank(client);
        escrow.disputeEscrow(id3, keccak256("dispute-3"));

        vm.prank(arbitrator);
        escrow.resolveDispute(id3, 150 * 1e6, 150 * 1e6);

        assertEq(escrow.totalEscrowBalance(), 0);
        assertEq(usdc.balanceOf(address(escrow)), 0);
    }

    function testFuzz_CreateAndFund_ArbitraryAmounts(uint256 amount) public {
        amount = bound(amount, 1e6, 1_000_000 * 1e6);

        usdc.mint(client, amount);
        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        bytes32 ref = keccak256(abi.encodePacked(amount));

        vm.prank(client);
        bytes32 id = escrow.createAndFundEscrow(ref, beneficiary, amount, deadline, 0);

        Escrow.EscrowRecord memory rec = escrow.getEscrow(id);
        assertEq(rec.amount, amount);
        assertEq(uint8(rec.state), uint8(Escrow.EscrowState.FUNDED));
    }

    function testFuzz_DisputeResolution_Splits(uint256 beneficiaryAmount, uint256 clientRefundAmount) public {
        beneficiaryAmount = bound(beneficiaryAmount, 0, DEFAULT_AMOUNT);
        clientRefundAmount = DEFAULT_AMOUNT - beneficiaryAmount;

        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(client);
        escrow.disputeEscrow(escrowId, keccak256("dispute"));

        uint256 preBeneficiary = usdc.balanceOf(beneficiary);
        uint256 preClient = usdc.balanceOf(client);

        vm.prank(arbitrator);
        escrow.resolveDispute(escrowId, beneficiaryAmount, clientRefundAmount);

        assertEq(usdc.balanceOf(beneficiary), preBeneficiary + beneficiaryAmount);
        assertEq(usdc.balanceOf(client), preClient + clientRefundAmount);
        assertEq(escrow.totalEscrowBalance(), 0);
    }

    function testFuzz_DeterministicId_NoCollisionAcrossSalts(uint256 salt1, uint256 salt2) public view {
        vm.assume(salt1 != salt2);
        bytes32 id1 = escrow.computeEscrowId(client, DEFAULT_REF, salt1);
        bytes32 id2 = escrow.computeEscrowId(client, DEFAULT_REF, salt2);
        assertTrue(id1 != id2, "Different salts must produce different IDs");
    }

    function testFuzz_DeterministicId_NoCollisionAcrossClients(address client1, address client2) public view {
        vm.assume(client1 != client2);
        bytes32 id1 = escrow.computeEscrowId(client1, DEFAULT_REF, 0);
        bytes32 id2 = escrow.computeEscrowId(client2, DEFAULT_REF, 0);
        assertTrue(id1 != id2, "Different clients must produce different IDs");
    }

    function testFuzz_UnauthorizedCallersCannotRelease(address caller) public {
        vm.assume(caller != client && caller != arbitrator);

        uint256 deadline = block.timestamp + DEADLINE_DURATION;
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(DEFAULT_REF, beneficiary, DEFAULT_AMOUNT, deadline, 0);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(caller);
        vm.expectRevert(abi.encodeWithSelector(Escrow.UnauthorizedCaller.selector, escrowId, caller));
        escrow.releaseEscrow(escrowId);
    }
}
