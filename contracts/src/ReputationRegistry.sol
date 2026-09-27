// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {IResultNotary} from "./interfaces/IResultNotary.sol";
import {IReputationRegistry} from "./interfaces/IReputationRegistry.sol";

/**
 * @title ReputationRegistry
 * @notice Cryptographically verified reputation evidence ledger for AgentChain.
 * @dev Cryptographically bound to ResultNotary. Records objective execution outcomes.
 *      Holds ZERO funds, no subjective scores, no staking, and no tokens.
 */
contract ReputationRegistry is AccessControl, IReputationRegistry {
    bytes32 public constant REPUTATION_ORACLE_ROLE = keccak256("REPUTATION_ORACLE_ROLE");

    // Authoritative ResultNotary contract
    IResultNotary public immutable override resultNotary;

    // Mapping from executionId to reputation evidence record (single record per execution)
    mapping(bytes32 => ReputationRecord) private _records;

    // Custom errors
    error ZeroAdminAddress();
    error ZeroResultNotaryAddress();
    error ZeroAgentId();
    error ZeroExecutionId();
    error ZeroAgentVersionId();
    error InvalidOutcomeType();
    error ResultNotNotarized(bytes32 executionId, bytes32 resultHash);
    error AlreadyRegistered(bytes32 executionId);
    error RecordNotFound(bytes32 executionId);

    constructor(address admin, address resultNotaryAddress, address initialOracle) {
        if (admin == address(0)) revert ZeroAdminAddress();
        if (resultNotaryAddress == address(0)) revert ZeroResultNotaryAddress();

        resultNotary = IResultNotary(resultNotaryAddress);
        _grantRole(DEFAULT_ADMIN_ROLE, admin);

        if (initialOracle != address(0)) {
            _grantRole(REPUTATION_ORACLE_ROLE, initialOracle);
        }
    }

    /**
     * @notice Registers verified reputation evidence for an execution outcome.
     * @param agentId Unique 32-byte agent identifier.
     * @param executionId Unique 32-byte execution identifier.
     * @param agentVersionId Unique 32-byte agent version identifier.
     * @param outcomeType Objective outcome type classification.
     * @param resultHash Canonical result hash (required for VERIFIED_SUCCESS, must match ResultNotary).
     * @param evidenceHash Cryptographic hash of off-chain diagnostic/audit evidence payload.
     */
    function registerReputationEvent(
        bytes32 agentId,
        bytes32 executionId,
        bytes32 agentVersionId,
        OutcomeType outcomeType,
        bytes32 resultHash,
        bytes32 evidenceHash
    ) external onlyRole(REPUTATION_ORACLE_ROLE) returns (bool) {
        if (agentId == bytes32(0)) revert ZeroAgentId();
        if (executionId == bytes32(0)) revert ZeroExecutionId();
        if (agentVersionId == bytes32(0)) revert ZeroAgentVersionId();
        if (outcomeType == OutcomeType.NONE) revert InvalidOutcomeType();

        // Replay Protection: each execution outcome may only be registered once
        if (_records[executionId].timestamp != 0) {
            revert AlreadyRegistered(executionId);
        }

        // Cryptographic binding to ResultNotary
        if (outcomeType == OutcomeType.VERIFIED_SUCCESS) {
            if (resultHash == bytes32(0) || !resultNotary.verifyProof(executionId, resultHash)) {
                revert ResultNotNotarized(executionId, resultHash);
            }
        } else if (resultHash != bytes32(0)) {
            // If a non-success outcome supplies a result hash, it must still be validly notarized
            if (!resultNotary.verifyProof(executionId, resultHash)) {
                revert ResultNotNotarized(executionId, resultHash);
            }
        }

        _records[executionId] = ReputationRecord({
            agentId: agentId,
            executionId: executionId,
            agentVersionId: agentVersionId,
            outcomeType: outcomeType,
            resultHash: resultHash,
            evidenceHash: evidenceHash,
            reporter: msg.sender,
            // forge-lint: disable-next-line(unsafe-typecast)
            timestamp: uint64(block.timestamp),
            // forge-lint: disable-next-line(unsafe-typecast)
            blockNumber: uint64(block.number)
        });

        emit ReputationEventRegistered(
            agentId,
            executionId,
            agentVersionId,
            outcomeType,
            resultHash,
            evidenceHash,
            msg.sender,
            block.timestamp
        );

        return true;
    }

    /**
     * @notice Retrieves the reputation record for an execution. Reverts if not found.
     * @param executionId Unique 32-byte execution identifier.
     */
    function getRecord(bytes32 executionId) external view override returns (ReputationRecord memory) {
        ReputationRecord memory record = _records[executionId];
        if (record.timestamp == 0) revert RecordNotFound(executionId);
        return record;
    }

    /**
     * @notice Checks whether a reputation record exists for an execution.
     * @param executionId Unique 32-byte execution identifier.
     */
    function hasRecord(bytes32 executionId) external view override returns (bool) {
        return _records[executionId].timestamp != 0;
    }

    /**
     * @notice Returns chain ID for explicit domain separation.
     */
    function getChainId() external view override returns (uint256) {
        return block.chainid;
    }
}
