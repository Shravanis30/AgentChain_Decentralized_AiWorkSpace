// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {IResultNotary} from "./IResultNotary.sol";

/**
 * @title IReputationRegistry
 * @notice Interface for the AgentChain verified reputation evidence registry.
 * @dev Cryptographically bound to ResultNotary. Holds zero funds.
 */
interface IReputationRegistry {
    enum OutcomeType {
        NONE,                  // 0: Unspecified / invalid
        VERIFIED_SUCCESS,      // 1: Succeeded execution matching ResultNotary proof
        VERIFIED_FAILURE,      // 2: Terminal execution failure
        VERIFIED_TIMEOUT,      // 3: Execution timed out before completion
        VERIFIED_CANCELLATION  // 4: Execution cancelled by client or system
    }

    struct ReputationRecord {
        bytes32 agentId;
        bytes32 executionId;
        bytes32 agentVersionId;
        OutcomeType outcomeType;
        bytes32 resultHash;
        bytes32 evidenceHash;
        address reporter;
        uint64 timestamp;
        uint64 blockNumber;
    }

    event ReputationEventRegistered(
        bytes32 indexed agentId,
        bytes32 indexed executionId,
        bytes32 agentVersionId,
        OutcomeType outcomeType,
        bytes32 resultHash,
        bytes32 evidenceHash,
        address indexed reporter,
        uint256 timestamp
    );

    function registerReputationEvent(
        bytes32 agentId,
        bytes32 executionId,
        bytes32 agentVersionId,
        OutcomeType outcomeType,
        bytes32 resultHash,
        bytes32 evidenceHash
    ) external returns (bool);

    function getRecord(bytes32 executionId) external view returns (ReputationRecord memory);

    function hasRecord(bytes32 executionId) external view returns (bool);

    function resultNotary() external view returns (IResultNotary);

    function getChainId() external view returns (uint256);
}
