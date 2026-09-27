// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/**
 * @title IRevenueDistributor
 * @notice Interface for the AgentChain Revenue Distributor contract.
 * @dev Performs atomic 85/10/5 economic distribution on settlement release.
 */
interface IRevenueDistributor {
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

    function distribute(
        bytes32 escrowId,
        bytes32 referenceId,
        uint256 grossAmount,
        address developerRecipient
    ) external returns (bytes32 distributionId);

    function paymentToken() external view returns (address);
    function stakerRecipient() external view returns (address);
    function daoRecipient() external view returns (address);
    function escrowContract() external view returns (address);
    function distributionVersion() external view returns (uint256);
}
