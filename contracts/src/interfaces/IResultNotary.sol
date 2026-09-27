// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

interface IResultNotary {
    struct NotarizationProof {
        bytes32 executionId;
        bytes32 resultHash;
        bytes32 artifactCommitment;
        address notarizer;
        uint256 timestamp;
        uint256 blockNumber;
    }

    event ResultNotarized(
        bytes32 indexed executionId,
        bytes32 indexed resultHash,
        bytes32 artifactCommitment,
        address indexed notarizer,
        uint256 timestamp
    );

    function notarizeResult(
        bytes32 executionId,
        bytes32 resultHash,
        bytes32 artifactCommitment
    ) external returns (bool);

    function getProof(bytes32 executionId) external view returns (NotarizationProof memory);

    function hasProof(bytes32 executionId) external view returns (bool);

    function verifyProof(bytes32 executionId, bytes32 expectedHash) external view returns (bool);

    function getChainId() external view returns (uint256);
}
