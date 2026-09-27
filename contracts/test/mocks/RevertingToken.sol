// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/**
 * @title RevertingToken
 * @notice Adversarial 6-decimal ERC20 token that explicitly reverts on transfer.
 */
contract RevertingToken is ERC20 {
    error TransferReverted();

    bool public shouldRevertTransfer;
    bool public shouldRevertTransferFrom;

    constructor() ERC20("Reverting USDC", "revUSDC") {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function setRevertConfig(bool revTransfer, bool revTransferFrom) external {
        shouldRevertTransfer = revTransfer;
        shouldRevertTransferFrom = revTransferFrom;
    }

    function transfer(address to, uint256 amount) public override returns (bool) {
        if (shouldRevertTransfer) {
            revert TransferReverted();
        }
        return super.transfer(to, amount);
    }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        if (shouldRevertTransferFrom) {
            revert TransferReverted();
        }
        return super.transferFrom(from, to, amount);
    }
}
