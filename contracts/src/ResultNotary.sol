// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {IResultNotary} from "./interfaces/IResultNotary.sol";

/**
 * @title ResultNotary
 * @notice Cryptographic result notarization and immutable on-chain proof registry for AgentChain.
 * @dev Records SHA-256 output hashes and artifact commitments keyed by executionId.
 *      Enforces single notarization per executionId (fail-closed replay protection).
 *      No token custody or financial balances.
 */
contract ResultNotary is AccessControl, IResultNotary {
    bytes32 public constant NOTARIZER_ROLE = keccak256("NOTARIZER_ROLE");

    // Mapping from executionId to notarization proof
    mapping(bytes32 => NotarizationProof) private _proofs;

    // Custom errors
    error ZeroAdminAddress();
    error ZeroExecutionId();
    error ZeroResultHash();
    error AlreadyNotarized(bytes32 executionId);
    error NotarizationNotFound(bytes32 executionId);

    constructor(address admin, address initialNotarizer) {
        if (admin == address(0)) revert ZeroAdminAddress();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        if (initialNotarizer != address(0)) {
            _grantRole(NOTARIZER_ROLE, initialNotarizer);
        }
    }

    /**
     * @notice Records an immutable result notarization for an execution.
     * @param executionId Unique 32-byte execution identifier.
     * @param resultHash Cryptographic SHA-256 hash of canonical execution result.
     * @param artifactCommitment Optional cryptographic commitment/hash of off-chain artifact or metadata.
     */
    function notarizeResult(
        bytes32 executionId,
        bytes32 resultHash,
        bytes32 artifactCommitment
    ) external onlyRole(NOTARIZER_ROLE) returns (bool) {
        if (executionId == bytes32(0)) revert ZeroExecutionId();
        if (resultHash == bytes32(0)) revert ZeroResultHash();
        if (_proofs[executionId].timestamp != 0) revert AlreadyNotarized(executionId);

        _proofs[executionId] = NotarizationProof({
            executionId: executionId,
            resultHash: resultHash,
            artifactCommitment: artifactCommitment,
            notarizer: msg.sender,
            timestamp: block.timestamp,
            blockNumber: block.number
        });

        emit ResultNotarized(
            executionId,
            resultHash,
            artifactCommitment,
            msg.sender,
            block.timestamp
        );

        return true;
    }

    /**
     * @notice Retrieves the immutable proof for an execution. Reverts if not notarized.
     * @param executionId Unique 32-byte execution identifier.
     */
    function getProof(bytes32 executionId) external view returns (NotarizationProof memory) {
        NotarizationProof memory proof = _proofs[executionId];
        if (proof.timestamp == 0) revert NotarizationNotFound(executionId);
        return proof;
    }

    /**
     * @notice Checks whether an execution has been notarized.
     * @param executionId Unique 32-byte execution identifier.
     */
    function hasProof(bytes32 executionId) external view returns (bool) {
        return _proofs[executionId].timestamp != 0;
    }

    /**
     * @notice Verifies if a given result hash matches the on-chain notarized proof.
     * @param executionId Unique 32-byte execution identifier.
     * @param expectedHash Expected SHA-256 result hash.
     */
    function verifyProof(bytes32 executionId, bytes32 expectedHash) external view returns (bool) {
        NotarizationProof memory proof = _proofs[executionId];
        if (proof.timestamp == 0) return false;
        return proof.resultHash == expectedHash;
    }

    /**
     * @notice Returns chain ID for explicit domain verification.
     */
    function getChainId() external view returns (uint256) {
        return block.chainid;
    }
}
