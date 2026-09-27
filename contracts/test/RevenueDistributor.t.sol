// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, console2} from "forge-std/Test.sol";
import {RevenueDistributor} from "../src/RevenueDistributor.sol";
import {Escrow} from "../src/Escrow.sol";
import {MockUSDC} from "./mocks/MockUSDC.sol";
import {ReentrantToken} from "./mocks/ReentrantToken.sol";
import {FalseReturningToken} from "./mocks/FalseReturningToken.sol";
import {RevertingToken} from "./mocks/RevertingToken.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

contract RevenueDistributorTest is Test {
    using SafeERC20 for MockUSDC;

    RevenueDistributor public distributor;
    Escrow public escrow;
    MockUSDC public usdc;

    address public admin = address(0xAD);
    address public arbitrator = address(0xAB);
    address public pauser = address(0xEE);
    address public client = address(0xC1);
    address public developer = address(0xD1);
    address public staker = address(0x51A);
    address public dao = address(0xDA0);
    address public stranger = address(0x99);

    uint256 public constant INITIAL_BALANCE = 100_000_000 * 1e6; // 100M USDC

    event DistributionExecuted(
        bytes32 indexed distributionId,
        bytes32 indexed escrowId,
        bytes32 indexed referenceId,
        uint256 grossAmount,
        address developerRecipient,
        uint256 developerAmount,
        address stakerRecipient,
        uint256 stakerAmount,
        address daoRecipient,
        uint256 daoAmount,
        uint256 distributionVersion
    );

    function setUp() public {
        vm.chainId(31337);
        usdc = new MockUSDC();

        // Deploy distributor first
        distributor = new RevenueDistributor(admin, address(0), address(usdc), staker, dao);

        // Deploy escrow with distributor
        escrow = new Escrow(admin, arbitrator, pauser, address(usdc), address(distributor));

        // Bind escrow in distributor
        vm.prank(admin);
        distributor.setEscrowContract(address(escrow));

        // Fund client
        usdc.mint(client, INITIAL_BALANCE);
        vm.prank(client);
        usdc.approve(address(escrow), type(uint256).max);
    }

    // ==========================================
    // 1. EXACT ACCOUNTING & REMAINDER CONSERVATION
    // ==========================================

    function test_Accounting_ExactFormulaValues() public pure {
        // A = 1
        uint256 g1 = 1;
        uint256 d1 = (g1 * 85) / 100; // 0
        uint256 s1 = (g1 * 10) / 100; // 0
        uint256 rem1 = g1 - d1 - s1;  // 1
        assertEq(d1, 0);
        assertEq(s1, 0);
        assertEq(rem1, 1);
        assertEq(d1 + s1 + rem1, g1);

        // A = 2
        uint256 g2 = 2;
        uint256 d2 = (g2 * 85) / 100; // 1
        uint256 s2 = (g2 * 10) / 100; // 0
        uint256 rem2 = g2 - d2 - s2;  // 1
        assertEq(d2, 1);
        assertEq(s2, 0);
        assertEq(rem2, 1);
        assertEq(d2 + s2 + rem2, g2);

        // A = 3
        uint256 g3 = 3;
        uint256 d3 = (g3 * 85) / 100; // 2
        uint256 s3 = (g3 * 10) / 100; // 0
        uint256 rem3 = g3 - d3 - s3;  // 1
        assertEq(d3, 2);
        assertEq(s3, 0);
        assertEq(rem3, 1);
        assertEq(d3 + s3 + rem3, g3);

        // A = 99
        uint256 g99 = 99;
        uint256 d99 = (g99 * 85) / 100; // 84
        uint256 s99 = (g99 * 10) / 100; // 9
        uint256 rem99 = g99 - d99 - s99; // 6
        assertEq(d99, 84);
        assertEq(s99, 9);
        assertEq(rem99, 6);
        assertEq(d99 + s99 + rem99, g99);

        // A = 100
        uint256 g100 = 100;
        uint256 d100 = (g100 * 85) / 100; // 85
        uint256 s100 = (g100 * 10) / 100; // 10
        uint256 rem100 = g100 - d100 - s100; // 5
        assertEq(d100, 85);
        assertEq(s100, 10);
        assertEq(rem100, 5);
        assertEq(d100 + s100 + rem100, g100);

        // A = 101
        uint256 g101 = 101;
        uint256 d101 = (g101 * 85) / 100; // 85
        uint256 s101 = (g101 * 10) / 100; // 10
        uint256 rem101 = g101 - d101 - s101; // 6
        assertEq(d101, 85);
        assertEq(s101, 10);
        assertEq(rem101, 6);
        assertEq(d101 + s101 + rem101, g101);

        // A = 999,999
        uint256 g999k = 999_999;
        uint256 d999k = (g999k * 85) / 100; // 849,999
        uint256 s999k = (g999k * 10) / 100; // 99,999
        uint256 rem999k = g999k - d999k - s999k; // 50,001
        assertEq(d999k, 849_999);
        assertEq(s999k, 99_999);
        assertEq(rem999k, 50_001);
        assertEq(d999k + s999k + rem999k, g999k);

        // A = 1,000,000 (1 USDC)
        uint256 g1m = 1_000_000;
        uint256 d1m = (g1m * 85) / 100; // 850,000
        uint256 s1m = (g1m * 10) / 100; // 100,000
        uint256 rem1m = g1m - d1m - s1m; // 50,000
        assertEq(d1m, 850_000);
        assertEq(s1m, 100_000);
        assertEq(rem1m, 50_000);
        assertEq(d1m + s1m + rem1m, g1m);

        // A = 1,000,001
        uint256 g1m1 = 1_000_001;
        uint256 d1m1 = (g1m1 * 85) / 100; // 850,000
        uint256 s1m1 = (g1m1 * 10) / 100; // 100,000
        uint256 rem1m1 = g1m1 - d1m1 - s1m1; // 50,001
        assertEq(d1m1, 850_000);
        assertEq(s1m1, 100_000);
        assertEq(rem1m1, 50_001);
        assertEq(d1m1 + s1m1 + rem1m1, g1m1);
    }

    function testFuzz_AccountingConservation(uint128 amount) public pure {
        vm.assume(amount > 0);
        uint256 gross = uint256(amount);
        uint256 dev = (gross * 85) / 100;
        uint256 stk = (gross * 10) / 100;
        uint256 rem = gross - dev - stk;

        assertEq(dev + stk + rem, gross, "Conservation violated");
        assertTrue(dev <= gross, "Dev exceeds gross");
        assertTrue(stk <= gross, "Staker exceeds gross");
        assertTrue(rem <= gross, "DAO exceeds gross");
    }

    // ==========================================
    // 2. CONSTRUCTOR & INITIALIZATION SAFETY
    // ==========================================

    function test_Constructor_RevertOnZeroAddresses() public {
        vm.expectRevert(RevenueDistributor.ZeroAddress.selector);
        new RevenueDistributor(address(0), address(escrow), address(usdc), staker, dao);

        vm.expectRevert(RevenueDistributor.ZeroAddress.selector);
        new RevenueDistributor(admin, address(escrow), address(0), staker, dao);

        vm.expectRevert(RevenueDistributor.ZeroAddress.selector);
        new RevenueDistributor(admin, address(escrow), address(usdc), address(0), dao);

        vm.expectRevert(RevenueDistributor.ZeroAddress.selector);
        new RevenueDistributor(admin, address(escrow), address(usdc), staker, address(0));
    }

    function test_Constructor_RevertOnDuplicateStakerAndDao() public {
        vm.expectRevert(abi.encodeWithSelector(RevenueDistributor.DuplicateRecipient.selector, staker));
        new RevenueDistributor(admin, address(escrow), address(usdc), staker, staker);
    }

    function test_SetEscrowContract_CanOnlyBeCalledOnce() public {
        RevenueDistributor rd = new RevenueDistributor(admin, address(0), address(usdc), staker, dao);
        assertEq(rd.authorizedEscrow(), address(0));

        vm.prank(admin);
        rd.setEscrowContract(address(0x123));
        assertEq(rd.authorizedEscrow(), address(0x123));

        // Second call must revert
        vm.prank(admin);
        vm.expectRevert(RevenueDistributor.EscrowAlreadySet.selector);
        rd.setEscrowContract(address(0x456));
    }

    function test_SetEscrowContract_RevertOnUnauthorized() public {
        RevenueDistributor rd = new RevenueDistributor(admin, address(0), address(usdc), staker, dao);

        vm.prank(stranger);
        vm.expectRevert();
        rd.setEscrowContract(address(0x123));
    }

    function test_SetEscrowContract_RevertOnZeroAddress() public {
        RevenueDistributor rd = new RevenueDistributor(admin, address(0), address(usdc), staker, dao);

        vm.prank(admin);
        vm.expectRevert(RevenueDistributor.ZeroAddress.selector);
        rd.setEscrowContract(address(0));
    }

    // ==========================================
    // 3. AUTHORIZATION & ACCESS CONTROL
    // ==========================================

    function test_Distribute_RevertOnUnauthorizedCaller() public {
        bytes32 escrowId = keccak256("escrow-001");
        bytes32 refId = keccak256("ref-001");

        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(RevenueDistributor.UnauthorizedCaller.selector, stranger));
        distributor.distribute(escrowId, refId, 1000 * 1e6, developer);
    }

    function test_Distribute_RevertOnZeroAmount() public {
        bytes32 escrowId = keccak256("escrow-002");
        bytes32 refId = keccak256("ref-002");

        vm.prank(address(escrow));
        vm.expectRevert(RevenueDistributor.InvalidAmount.selector);
        distributor.distribute(escrowId, refId, 0, developer);
    }

    function test_Distribute_RevertOnZeroDeveloperRecipient() public {
        bytes32 escrowId = keccak256("escrow-003");
        bytes32 refId = keccak256("ref-003");

        vm.prank(address(escrow));
        vm.expectRevert(RevenueDistributor.ZeroAddress.selector);
        distributor.distribute(escrowId, refId, 1000 * 1e6, address(0));
    }

    function test_Distribute_RevertOnDeveloperMatchingStakerOrDao() public {
        bytes32 escrowId = keccak256("escrow-004");
        bytes32 refId = keccak256("ref-004");

        // Developer == Staker
        vm.prank(address(escrow));
        vm.expectRevert(abi.encodeWithSelector(RevenueDistributor.DuplicateRecipient.selector, staker));
        distributor.distribute(escrowId, refId, 1000 * 1e6, staker);

        // Developer == DAO
        vm.prank(address(escrow));
        vm.expectRevert(abi.encodeWithSelector(RevenueDistributor.DuplicateRecipient.selector, dao));
        distributor.distribute(escrowId, refId, 1000 * 1e6, dao);
    }

    // ==========================================
    // 4. FULL END-TO-END ESCROW + DISTRIBUTION
    // ==========================================

    function test_EndToEnd_CreateFundLockAndRelease_ExactSplit() public {
        uint256 grossAmount = 10_000 * 1e6; // 10,000 USDC
        uint256 deadline = block.timestamp + 1 days;
        bytes32 refId = keccak256("task-distribution-10k");

        // Initial balances
        uint256 devBefore = usdc.balanceOf(developer);
        uint256 stakerBefore = usdc.balanceOf(staker);
        uint256 daoBefore = usdc.balanceOf(dao);
        assertEq(devBefore, 0);
        assertEq(stakerBefore, 0);
        assertEq(daoBefore, 0);

        // 1. Create and fund escrow
        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(refId, developer, grossAmount, deadline, 0);

        // 2. Lock escrow
        vm.prank(client);
        escrow.lockEscrow(escrowId);

        // 3. Release escrow (triggers atomic 85/10/5 distribution)
        vm.prank(client);
        escrow.releaseEscrow(escrowId);

        // Expected amounts
        uint256 expectedDev = (grossAmount * 85) / 100; // 8,500 USDC
        uint256 expectedStaker = (grossAmount * 10) / 100; // 1,000 USDC
        uint256 expectedDao = grossAmount - expectedDev - expectedStaker; // 500 USDC

        assertEq(usdc.balanceOf(developer), expectedDev, "Developer should receive exactly 85%");
        assertEq(usdc.balanceOf(staker), expectedStaker, "Staker should receive exactly 10%");
        assertEq(usdc.balanceOf(dao), expectedDao, "DAO should receive exactly 5%");

        // Transient custody invariant: Distributor balance must be 0
        assertEq(usdc.balanceOf(address(distributor)), 0, "Distributor must not retain custody of funds");
        assertEq(escrow.totalEscrowBalance(), 0, "Escrow total balance must be 0");
    }

    function test_EndToEnd_OddAmountSplitConservation() public {
        uint256 grossAmount = 777_777; // 0.777777 USDC
        uint256 deadline = block.timestamp + 1 days;
        bytes32 refId = keccak256("task-odd-amount");

        vm.prank(client);
        bytes32 escrowId = escrow.createAndFundEscrow(refId, developer, grossAmount, deadline, 1);

        vm.prank(client);
        escrow.lockEscrow(escrowId);

        vm.prank(client);
        escrow.releaseEscrow(escrowId);

        uint256 devBal = usdc.balanceOf(developer);
        uint256 stkBal = usdc.balanceOf(staker);
        uint256 daoBal = usdc.balanceOf(dao);

        assertEq(devBal + stkBal + daoBal, grossAmount, "Total distributed must match gross exactly");
        assertEq(usdc.balanceOf(address(distributor)), 0, "Distributor holds 0 balance");
    }

    // ==========================================
    // 5. IDEMPOTENCY / REPLAY PROTECTION
    // ==========================================

    function test_Distribute_ReplayPrevention() public {
        bytes32 escrowId = keccak256("escrow-replay-test");
        bytes32 refId = keccak256("ref-replay");
        uint256 amount = 100 * 1e6;

        // Mint USDC to distributor directly to simulate test call
        usdc.mint(address(distributor), amount * 2);

        vm.prank(address(escrow));
        bytes32 distId = distributor.distribute(escrowId, refId, amount, developer);
        assertTrue(distributor.isDistributionProcessed(distId));

        // Replay of same escrowId and amount from escrow must revert
        vm.prank(address(escrow));
        vm.expectRevert(abi.encodeWithSelector(RevenueDistributor.DistributionAlreadyProcessed.selector, distId));
        distributor.distribute(escrowId, refId, amount, developer);
    }

    // ==========================================
    // 6. ADVERSARIAL VALUE ARRAY (SECTION 5 & 6)
    // ==========================================

    function test_Accounting_RequiredAdversarialValues() public {
        uint256[14] memory testValues = [
            uint256(1),
            uint256(2),
            uint256(3),
            uint256(99),
            uint256(100),
            uint256(101),
            uint256(999),
            uint256(1_000_000),
            uint256(1_000_001),
            uint256(10**12),
            uint256(10**18),
            uint256(10**24),
            uint256(10**30),
            uint256(100_000_000_000 * 1e6) // 100 Billion USDC (Max Practical)
        ];

        for (uint256 i = 0; i < testValues.length; i++) {
            uint256 gross = testValues[i];
            bytes32 escrowId = keccak256(abi.encodePacked("escrow-val-", i));
            bytes32 refId = keccak256(abi.encodePacked("ref-val-", i));

            // Invariant: balance must be 0 before distribution
            assertEq(usdc.balanceOf(address(distributor)), 0, "Distributor balance must be 0 before");

            uint256 devBefore = usdc.balanceOf(developer);
            uint256 stkBefore = usdc.balanceOf(staker);
            uint256 daoBefore = usdc.balanceOf(dao);

            // Fund distributor transiently
            usdc.mint(address(distributor), gross);

            // Execute distribution
            vm.prank(address(escrow));
            bytes32 distId = distributor.distribute(escrowId, refId, gross, developer);

            // Calculations
            uint256 devAlloc = (gross * 85) / 100;
            uint256 stkAlloc = (gross * 10) / 100;
            uint256 daoAlloc = gross - devAlloc - stkAlloc;

            // Mathematical conservation invariant
            assertEq(devAlloc + stkAlloc + daoAlloc, gross, "Conservation broken");

            // Recipient balance invariants
            uint256 devIncrease = usdc.balanceOf(developer) - devBefore;
            uint256 stkIncrease = usdc.balanceOf(staker) - stkBefore;
            uint256 daoIncrease = usdc.balanceOf(dao) - daoBefore;

            assertEq(devIncrease, devAlloc, "Developer balance increase mismatch");
            assertEq(stkIncrease, stkAlloc, "Staker balance increase mismatch");
            assertEq(daoIncrease, daoAlloc, "DAO balance increase mismatch");
            assertEq(devIncrease + stkIncrease + daoIncrease, gross, "Total recipient increase mismatch");

            // Invariant: balance must be 0 after distribution
            assertEq(usdc.balanceOf(address(distributor)), 0, "Distributor must retain 0 balance");
            assertTrue(distributor.isDistributionProcessed(distId), "Distribution must be marked processed");
        }
    }

    // ==========================================
    // 7. DIRECT CONTRACT ATTACKS (SECTION 7)
    // ==========================================

    function test_DirectAttack_AllUnauthorizedCallersRevert() public {
        bytes32 escrowId = keccak256("attack-escrow");
        bytes32 refId = keccak256("attack-ref");
        uint256 amount = 1000 * 1e6;

        address[7] memory unauthorizedCallers = [
            stranger,
            developer,
            staker,
            dao,
            client,
            address(0xCAFE),
            address(0xBEEF)
        ];

        for (uint256 i = 0; i < unauthorizedCallers.length; i++) {
            address caller = unauthorizedCallers[i];
            vm.prank(caller);
            vm.expectRevert(abi.encodeWithSelector(RevenueDistributor.UnauthorizedCaller.selector, caller));
            distributor.distribute(escrowId, refId, amount, developer);
        }
    }

    function test_DirectAttack_FakeEscrowContractReverts() public {
        bytes32 escrowId = keccak256("fake-escrow-id");
        bytes32 refId = keccak256("fake-ref-id");
        uint256 amount = 1000 * 1e6;

        FakeEscrow fake = new FakeEscrow(address(distributor));
        vm.expectRevert(abi.encodeWithSelector(RevenueDistributor.UnauthorizedCaller.selector, address(fake)));
        fake.attemptDistribution(escrowId, refId, amount, developer);
    }

    function test_DirectAttack_ZeroAddressCallerReverts() public {
        bytes32 escrowId = keccak256("zero-caller");
        bytes32 refId = keccak256("zero-ref");
        uint256 amount = 1000 * 1e6;

        vm.prank(address(0));
        vm.expectRevert(abi.encodeWithSelector(RevenueDistributor.UnauthorizedCaller.selector, address(0)));
        distributor.distribute(escrowId, refId, amount, developer);
    }

    function test_DirectAttack_MalformedCalldataReverts() public {
        bytes memory badCalldata = abi.encodeWithSignature("distribute(bytes32,bytes32)", bytes32(0), bytes32(0));
        (bool success, ) = address(distributor).call(badCalldata);
        assertFalse(success, "Malformed calldata call must fail");
    }

    // ==========================================
    // 8. TOKEN SAFETY TESTING (SECTION 10)
    // ==========================================

    function test_TokenSafety_FalseReturningTokenRevertsSafely() public {
        // Deploy FalseReturningToken
        FalseReturningToken badToken = new FalseReturningToken();
        badToken.mint(client, INITIAL_BALANCE);

        RevenueDistributor rd = new RevenueDistributor(admin, address(0), address(badToken), staker, dao);
        vm.prank(admin);
        rd.setEscrowContract(address(escrow));

        // When badToken returns false on transfer, SafeERC20 must revert
        badToken.setFailureConfig(true, false);
        badToken.mint(address(rd), 1000 * 1e6);

        vm.prank(address(escrow));
        vm.expectRevert(); // SafeERC20FailedOperation
        rd.distribute(keccak256("bad-e"), keccak256("bad-r"), 1000 * 1e6, developer);

        // Funds must not be partially drained
        assertEq(badToken.balanceOf(developer), 0);
        assertEq(badToken.balanceOf(staker), 0);
        assertEq(badToken.balanceOf(dao), 0);
    }

    function test_TokenSafety_RevertingTokenRevertsAtomically() public {
        RevertingToken revToken = new RevertingToken();
        revToken.mint(client, INITIAL_BALANCE);

        RevenueDistributor rd = new RevenueDistributor(admin, address(0), address(revToken), staker, dao);
        vm.prank(admin);
        rd.setEscrowContract(address(escrow));

        revToken.setRevertConfig(true, false);
        revToken.mint(address(rd), 1000 * 1e6);

        vm.prank(address(escrow));
        vm.expectRevert(RevertingToken.TransferReverted.selector);
        rd.distribute(keccak256("rev-e"), keccak256("rev-r"), 1000 * 1e6, developer);

        assertEq(revToken.balanceOf(developer), 0);
        assertEq(revToken.balanceOf(staker), 0);
        assertEq(revToken.balanceOf(dao), 0);
    }

    // ==========================================
    // 9. REENTRANCY AND ATOMICITY (SECTION 11)
    // ==========================================

    function test_Reentrancy_TokenReentrantAttackBlocked() public {
        ReentrantToken reentrantToken = new ReentrantToken();
        reentrantToken.mint(client, INITIAL_BALANCE);

        RevenueDistributor rd = new RevenueDistributor(admin, address(0), address(reentrantToken), staker, dao);
        Escrow reentrantEscrow = new Escrow(admin, arbitrator, pauser, address(reentrantToken), address(rd));
        vm.prank(admin);
        rd.setEscrowContract(address(reentrantEscrow));

        vm.prank(client);
        reentrantToken.approve(address(reentrantEscrow), type(uint256).max);

        uint256 deadline = block.timestamp + 1 days;
        vm.prank(client);
        bytes32 escrowId = reentrantEscrow.createAndFundEscrow(keccak256("reentrancy-task"), developer, 1000 * 1e6, deadline, 0);

        vm.prank(client);
        reentrantEscrow.lockEscrow(escrowId);

        // Configure attack to reenter release during transfer
        reentrantToken.setAttackConfig(address(reentrantEscrow), escrowId, ReentrantToken.AttackTarget.Release, ReentrantToken.AttackTarget.None);

        // Calling releaseEscrow must revert due to reentrancy / state guard
        vm.prank(client);
        vm.expectRevert();
        reentrantEscrow.releaseEscrow(escrowId);

        // Verify no funds were stolen or retained in distributor
        assertEq(reentrantToken.balanceOf(address(rd)), 0, "Distributor must retain 0 balance");
    }

}

contract FakeEscrow {
    address public immutable target;

    constructor(address _target) {
        target = _target;
    }

    function attemptDistribution(bytes32 escrowId, bytes32 refId, uint256 gross, address dev) external {
        RevenueDistributor(target).distribute(escrowId, refId, gross, dev);
    }
}
