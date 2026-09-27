// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, console2} from "forge-std/Test.sol";
import {Escrow} from "../src/Escrow.sol";
import {MockUSDC} from "./mocks/MockUSDC.sol";

/**
 * @title EscrowHandler
 * @notice Actor handler for Foundry stateful invariant testing covering all lifecycle paths and dispute modes.
 */
contract EscrowHandler is Test {
    Escrow public escrow;
    MockUSDC public usdc;

    address public client;
    address public beneficiary;
    address public arbitrator;

    bytes32[] public activeEscrowIds;

    uint256 public ghost_totalFunded;
    uint256 public ghost_totalReleased;
    uint256 public ghost_totalRefunded;

    uint256 private nonce;

    constructor(Escrow _escrow, MockUSDC _usdc, address _client, address _beneficiary, address _arbitrator) {
        escrow = _escrow;
        usdc = _usdc;
        client = _client;
        beneficiary = _beneficiary;
        arbitrator = _arbitrator;
    }

    function createAndFund(uint256 amountSeed) external {
        uint256 amount = bound(amountSeed, 10 * 1e6, 10_000 * 1e6); // 10 to 10k USDC
        usdc.mint(client, amount);

        vm.startPrank(client);
        usdc.approve(address(escrow), amount);

        bytes32 ref = keccak256(abi.encodePacked("task-atomic", nonce++));
        uint256 deadline = block.timestamp + 1 days;

        bytes32 escrowId = escrow.createAndFundEscrow(ref, beneficiary, amount, deadline, nonce);
        vm.stopPrank();

        activeEscrowIds.push(escrowId);
        ghost_totalFunded += amount;
    }

    function separateCreateAndFund(uint256 amountSeed) external {
        uint256 amount = bound(amountSeed, 10 * 1e6, 10_000 * 1e6);
        usdc.mint(client, amount);

        vm.startPrank(client);
        usdc.approve(address(escrow), amount);

        bytes32 ref = keccak256(abi.encodePacked("task-separate", nonce++));
        uint256 deadline = block.timestamp + 1 days;

        bytes32 escrowId = escrow.createEscrow(ref, beneficiary, amount, deadline, nonce);
        escrow.fundEscrow(escrowId);
        vm.stopPrank();

        activeEscrowIds.push(escrowId);
        ghost_totalFunded += amount;
    }

    function lockAndRelease(uint256 indexSeed) external {
        if (activeEscrowIds.length == 0) return;
        uint256 idx = indexSeed % activeEscrowIds.length;
        bytes32 id = activeEscrowIds[idx];

        Escrow.EscrowRecord memory rec = escrow.getEscrow(id);
        if (rec.state == Escrow.EscrowState.FUNDED) {
            vm.prank(client);
            escrow.lockEscrow(id);
            rec = escrow.getEscrow(id);
        }

        if (rec.state == Escrow.EscrowState.LOCKED) {
            uint256 amt = rec.amount;
            vm.prank(client);
            escrow.releaseEscrow(id);
            ghost_totalReleased += amt;
            _removeActive(idx);
        }
    }

    function lockAndRefundAfterDeadline(uint256 indexSeed) external {
        if (activeEscrowIds.length == 0) return;
        uint256 idx = indexSeed % activeEscrowIds.length;
        bytes32 id = activeEscrowIds[idx];

        Escrow.EscrowRecord memory rec = escrow.getEscrow(id);
        if (rec.state == Escrow.EscrowState.FUNDED) {
            vm.prank(client);
            escrow.lockEscrow(id);
            rec = escrow.getEscrow(id);
        }

        if (rec.state == Escrow.EscrowState.LOCKED) {
            uint256 amt = rec.amount;
            vm.warp(rec.executionDeadline + 1);
            vm.prank(client);
            escrow.refundEscrow(id);
            ghost_totalRefunded += amt;
            _removeActive(idx);
        }
    }

    function refundBeforeLock(uint256 indexSeed) external {
        if (activeEscrowIds.length == 0) return;
        uint256 idx = indexSeed % activeEscrowIds.length;
        bytes32 id = activeEscrowIds[idx];

        Escrow.EscrowRecord memory rec = escrow.getEscrow(id);
        if (rec.state == Escrow.EscrowState.FUNDED) {
            uint256 amt = rec.amount;
            vm.prank(client);
            escrow.refundEscrow(id);
            ghost_totalRefunded += amt;
            _removeActive(idx);
        }
    }

    function forfeitRefundByBeneficiary(uint256 indexSeed) external {
        if (activeEscrowIds.length == 0) return;
        uint256 idx = indexSeed % activeEscrowIds.length;
        bytes32 id = activeEscrowIds[idx];

        Escrow.EscrowRecord memory rec = escrow.getEscrow(id);
        if (rec.state == Escrow.EscrowState.FUNDED) {
            vm.prank(client);
            escrow.lockEscrow(id);
            rec = escrow.getEscrow(id);
        }

        if (rec.state == Escrow.EscrowState.LOCKED) {
            uint256 amt = rec.amount;
            vm.prank(beneficiary);
            escrow.refundEscrow(id);
            ghost_totalRefunded += amt;
            _removeActive(idx);
        }
    }

    function arbitratorRelease(uint256 indexSeed) external {
        if (activeEscrowIds.length == 0) return;
        uint256 idx = indexSeed % activeEscrowIds.length;
        bytes32 id = activeEscrowIds[idx];

        Escrow.EscrowRecord memory rec = escrow.getEscrow(id);
        if (rec.state == Escrow.EscrowState.FUNDED) {
            vm.prank(client);
            escrow.lockEscrow(id);
            rec = escrow.getEscrow(id);
        }

        if (rec.state == Escrow.EscrowState.LOCKED) {
            uint256 amt = rec.amount;
            vm.prank(arbitrator);
            escrow.releaseEscrow(id);
            ghost_totalReleased += amt;
            _removeActive(idx);
        }
    }

    function disputeAndResolve(uint256 indexSeed, uint256 beneficiaryPercentSeed) external {
        if (activeEscrowIds.length == 0) return;
        uint256 idx = indexSeed % activeEscrowIds.length;
        bytes32 id = activeEscrowIds[idx];

        Escrow.EscrowRecord memory rec = escrow.getEscrow(id);
        if (rec.state == Escrow.EscrowState.FUNDED) {
            vm.prank(client);
            escrow.lockEscrow(id);
            rec = escrow.getEscrow(id);
        }

        if (rec.state == Escrow.EscrowState.LOCKED) {
            vm.prank(client);
            escrow.disputeEscrow(id, keccak256("dispute_reason"));

            uint256 pct = bound(beneficiaryPercentSeed, 0, 100);
            uint256 beneficiaryAmt = (rec.amount * pct) / 100;
            uint256 clientAmt = rec.amount - beneficiaryAmt;

            vm.prank(arbitrator);
            escrow.resolveDispute(id, beneficiaryAmt, clientAmt);

            ghost_totalReleased += beneficiaryAmt;
            ghost_totalRefunded += clientAmt;
            _removeActive(idx);
        }
    }

    function _removeActive(uint256 idx) internal {
        activeEscrowIds[idx] = activeEscrowIds[activeEscrowIds.length - 1];
        activeEscrowIds.pop();
    }
}

/**
 * @title EscrowInvariantTest
 * @notice Invariant test suite verifying strict financial conservation and token accounting across all transitions.
 */
contract EscrowInvariantTest is Test {
    Escrow public escrow;
    MockUSDC public usdc;
    EscrowHandler public handler;

    address public admin = address(0xAD);
    address public arbitrator = address(0xAB);
    address public pauser = address(0xEE);
    address public client = address(0xC1);
    address public beneficiary = address(0xB1);

    function setUp() public {
        vm.chainId(31337);
        usdc = new MockUSDC();
        escrow = new Escrow(admin, arbitrator, pauser, address(usdc), address(0));

        handler = new EscrowHandler(escrow, usdc, client, beneficiary, arbitrator);
        targetContract(address(handler));
    }

    /// @notice Invariant 1: Total contract USDC balance must match tracked totalEscrowBalance
    function invariant_BalanceMatchesTrackedTotal() public view {
        assertEq(
            usdc.balanceOf(address(escrow)),
            escrow.totalEscrowBalance(),
            "Contract USDC balance must equal totalEscrowBalance"
        );
    }

    /// @notice Invariant 2: Financial conservation: total funded == total released + total refunded + active escrow balance
    function invariant_FinancialConservation() public view {
        uint256 funded = handler.ghost_totalFunded();
        uint256 released = handler.ghost_totalReleased();
        uint256 refunded = handler.ghost_totalRefunded();
        uint256 inEscrow = escrow.totalEscrowBalance();

        assertEq(
            funded,
            released + refunded + inEscrow,
            "Conservation violated: totalFunded != released + refunded + inEscrow"
        );
    }
}
