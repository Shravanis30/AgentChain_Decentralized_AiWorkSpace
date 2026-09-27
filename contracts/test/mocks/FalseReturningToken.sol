// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/**
 * @title FalseReturningToken
 * @notice Adversarial 6-decimal ERC20 token that returns false instead of reverting on transfer.
 */
contract FalseReturningToken is ERC20 {
    bool public shouldFailTransfer;
    bool public shouldFailTransferFrom;

    constructor() ERC20("False Returning USDC", "fUSDC") {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function setFailureConfig(bool failTransfer, bool failTransferFrom) external {
        shouldFailTransfer = failTransfer;
        shouldFailTransferFrom = failTransferFrom;
    }

    function transfer(address to, uint256 amount) public override returns (bool) {
        if (shouldFailTransfer) {
            return false;
        }
        return super.transfer(to, amount);
    }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        if (shouldFailTransferFrom) {
            return false;
        }
        return super.transferFrom(from, to, amount);
    }
}
