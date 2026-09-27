// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/**
 * @title FeeOnTransferToken
 * @notice Adversarial 6-decimal ERC20 token that burns 5% on transfers.
 */
contract FeeOnTransferToken is ERC20 {
    constructor() ERC20("Fee USDC", "feeUSDC") {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function transfer(address to, uint256 amount) public override returns (bool) {
        uint256 fee = (amount * 5) / 100;
        uint256 net = amount - fee;
        _burn(msg.sender, fee);
        return super.transfer(to, net);
    }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        uint256 fee = (amount * 5) / 100;
        uint256 net = amount - fee;
        _spendAllowance(from, msg.sender, amount);
        _burn(from, fee);
        _transfer(from, to, net);
        return true;
    }
}
