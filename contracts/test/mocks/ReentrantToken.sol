// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {Escrow} from "../../src/Escrow.sol";

/**
 * @title ReentrantToken
 * @notice Adversarial ERC20 token that attempts to re-enter various Escrow contract entrypoints during transfers.
 */
contract ReentrantToken is ERC20 {
    enum AttackTarget {
        None,
        Release,
        Refund,
        ResolveDispute,
        Fund,
        CreateAndFund
    }

    address public targetEscrow;
    bytes32 public attackEscrowId;
    AttackTarget public transferAttackTarget;
    AttackTarget public transferFromAttackTarget;

    constructor() ERC20("Reentrant USDC", "rUSDC") {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function setAttackConfig(
        address _targetEscrow,
        bytes32 _escrowId,
        AttackTarget _transferAttack,
        AttackTarget _transferFromAttack
    ) external {
        targetEscrow = _targetEscrow;
        attackEscrowId = _escrowId;
        transferAttackTarget = _transferAttack;
        transferFromAttackTarget = _transferFromAttack;
    }

    function transfer(address to, uint256 amount) public override returns (bool) {
        if (transferAttackTarget != AttackTarget.None && targetEscrow != address(0)) {
            AttackTarget target = transferAttackTarget;
            transferAttackTarget = AttackTarget.None; // Prevent infinite loop in test runner

            if (target == AttackTarget.Release) {
                Escrow(targetEscrow).releaseEscrow(attackEscrowId);
            } else if (target == AttackTarget.Refund) {
                Escrow(targetEscrow).refundEscrow(attackEscrowId);
            } else if (target == AttackTarget.ResolveDispute) {
                Escrow(targetEscrow).resolveDispute(attackEscrowId, amount / 2, amount / 2);
            }
        }
        return super.transfer(to, amount);
    }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        if (transferFromAttackTarget != AttackTarget.None && targetEscrow != address(0)) {
            AttackTarget target = transferFromAttackTarget;
            transferFromAttackTarget = AttackTarget.None;

            if (target == AttackTarget.Fund) {
                Escrow(targetEscrow).fundEscrow(attackEscrowId);
            } else if (target == AttackTarget.CreateAndFund) {
                Escrow(targetEscrow)
                    .createAndFundEscrow(
                        keccak256("reentrant-task"), address(0xB1), 100 * 1e6, block.timestamp + 1 days, 999
                    );
            }
        }
        return super.transferFrom(from, to, amount);
    }
}
