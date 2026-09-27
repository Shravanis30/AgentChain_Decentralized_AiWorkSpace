// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, console2} from "forge-std/Test.sol";
import {ResultNotary} from "../src/ResultNotary.sol";
import {IResultNotary} from "../src/interfaces/IResultNotary.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";

contract ResultNotaryTest is Test {
    ResultNotary public notary;

    address public admin = address(0xAD01);
    address public notarizer = address(0xBE01);
    address public attacker = address(0xDEAD);

    event ResultNotarized(
        bytes32 indexed executionId,
        bytes32 indexed resultHash,
        bytes32 artifactCommitment,
        address indexed notarizer,
        uint256 timestamp
    );

    function setUp() public {
        vm.prank(admin);
        notary = new ResultNotary(admin, notarizer);
    }

    // 1. Constructor tests
    function test_ConstructorZeroAdminReverts() public {
        vm.expectRevert(ResultNotary.ZeroAdminAddress.selector);
        new ResultNotary(address(0), notarizer);
    }

    function test_ConstructorWithoutInitialNotarizer() public {
        ResultNotary n = new ResultNotary(admin, address(0));
        assertTrue(n.hasRole(n.DEFAULT_ADMIN_ROLE(), admin));
        assertFalse(n.hasRole(n.NOTARIZER_ROLE(), notarizer));
    }

    // 2. Valid Notarization
    function test_ValidNotarization() public {
        bytes32 execId = keccak256("execution-1");
        bytes32 resultHash = keccak256("canonical-result-1");
        bytes32 artifactCommitment = keccak256("artifact-1");

        assertFalse(notary.hasProof(execId));

        vm.expectEmit(true, true, false, true);
        emit ResultNotarized(execId, resultHash, artifactCommitment, notarizer, block.timestamp);

        vm.prank(notarizer);
        bool success = notary.notarizeResult(execId, resultHash, artifactCommitment);
        assertTrue(success);

        assertTrue(notary.hasProof(execId));
        IResultNotary.NotarizationProof memory proof = notary.getProof(execId);
        assertEq(proof.executionId, execId);
        assertEq(proof.resultHash, resultHash);
        assertEq(proof.artifactCommitment, artifactCommitment);
        assertEq(proof.notarizer, notarizer);
        assertEq(proof.timestamp, block.timestamp);
        assertEq(proof.blockNumber, block.number);

        assertTrue(notary.verifyProof(execId, resultHash));
        assertFalse(notary.verifyProof(execId, keccak256("other-hash")));
    }

    // 3. Replay Protection - Duplicate Execution ID
    function test_RevertDuplicateNotarization_SameHash() public {
        bytes32 execId = keccak256("execution-replay-1");
        bytes32 resultHash = keccak256("canonical-result");
        bytes32 artifactCommitment = keccak256("artifact");

        vm.prank(notarizer);
        notary.notarizeResult(execId, resultHash, artifactCommitment);

        // Second attempt with exact same hash must revert
        vm.prank(notarizer);
        vm.expectRevert(abi.encodeWithSelector(ResultNotary.AlreadyNotarized.selector, execId));
        notary.notarizeResult(execId, resultHash, artifactCommitment);
    }

    function test_RevertDuplicateNotarization_ConflictingHash() public {
        bytes32 execId = keccak256("execution-replay-2");
        bytes32 resultHash1 = keccak256("canonical-result-1");
        bytes32 resultHash2 = keccak256("canonical-result-2");

        vm.prank(notarizer);
        notary.notarizeResult(execId, resultHash1, bytes32(0));

        // Malicious attempt to overwrite with different hash must revert
        vm.prank(notarizer);
        vm.expectRevert(abi.encodeWithSelector(ResultNotary.AlreadyNotarized.selector, execId));
        notary.notarizeResult(execId, resultHash2, bytes32(0));

        // Original proof remains immutable
        assertEq(notary.getProof(execId).resultHash, resultHash1);
    }

    // 4. Zero value validation
    function test_RevertZeroExecutionId() public {
        vm.prank(notarizer);
        vm.expectRevert(ResultNotary.ZeroExecutionId.selector);
        notary.notarizeResult(bytes32(0), keccak256("hash"), bytes32(0));
    }

    function test_RevertZeroResultHash() public {
        vm.prank(notarizer);
        vm.expectRevert(ResultNotary.ZeroResultHash.selector);
        notary.notarizeResult(keccak256("exec"), bytes32(0), bytes32(0));
    }

    // 5. Authorization Controls
    function test_RevertUnauthorizedCaller() public {
        bytes32 execId = keccak256("exec-unauth");
        bytes32 resultHash = keccak256("hash-unauth");
        bytes32 role = notary.NOTARIZER_ROLE();

        vm.expectRevert(
            abi.encodeWithSelector(
                IAccessControl.AccessControlUnauthorizedAccount.selector,
                attacker,
                role
            )
        );
        vm.prank(attacker);
        notary.notarizeResult(execId, resultHash, bytes32(0));
    }

    function test_AdminCanGrantAndRevokeNotarizer() public {
        address newNotarizer = address(0x9999);
        bytes32 execId = keccak256("exec-new-new-notarizer");
        bytes32 resultHash = keccak256("hash-new-notarizer");
        bytes32 role = notary.NOTARIZER_ROLE();

        // Attacker cannot grant
        vm.expectRevert();
        vm.prank(attacker);
        notary.grantRole(role, newNotarizer);

        // Admin grants
        vm.prank(admin);
        notary.grantRole(role, newNotarizer);

        // New notarizer can notarize
        vm.prank(newNotarizer);
        assertTrue(notary.notarizeResult(execId, resultHash, bytes32(0)));

        // Admin revokes
        vm.prank(admin);
        notary.revokeRole(role, newNotarizer);

        // Revoked notarizer now fails
        bytes32 execId2 = keccak256("exec-revoked");
        vm.expectRevert();
        vm.prank(newNotarizer);
        notary.notarizeResult(execId2, resultHash, bytes32(0));
    }

    // 6. Read / Verification Views
    function test_RevertGetProofNotFound() public {
        bytes32 nonExistent = keccak256("non-existent");
        vm.expectRevert(abi.encodeWithSelector(ResultNotary.NotarizationNotFound.selector, nonExistent));
        notary.getProof(nonExistent);
    }

    function test_VerifyProofUnnotarizedReturnsFalse() public view {
        bytes32 nonExistent = keccak256("non-existent-verify");
        assertFalse(notary.verifyProof(nonExistent, keccak256("any-hash")));
    }

    // 7. Cross-Execution Separation
    function test_CrossExecutionSameHashAllowed() public {
        // Two distinct executions producing the identical canonical hash
        bytes32 execA = keccak256("exec-A");
        bytes32 execB = keccak256("exec-B");
        bytes32 sameHash = keccak256("common-deterministic-output");

        vm.startPrank(notarizer);
        assertTrue(notary.notarizeResult(execA, sameHash, bytes32(0)));
        assertTrue(notary.notarizeResult(execB, sameHash, bytes32(0)));
        vm.stopPrank();

        assertTrue(notary.verifyProof(execA, sameHash));
        assertTrue(notary.verifyProof(execB, sameHash));
    }

    function test_CrossExecutionIsolation() public {
        bytes32 execA = keccak256("exec-isolation-A");
        bytes32 execB = keccak256("exec-isolation-B");
        bytes32 hashA = keccak256("hash-A");
        bytes32 hashB = keccak256("hash-B");

        vm.startPrank(notarizer);
        notary.notarizeResult(execA, hashA, bytes32(0));
        notary.notarizeResult(execB, hashB, bytes32(0));
        vm.stopPrank();

        // Cross-binding checks
        assertFalse(notary.verifyProof(execA, hashB));
        assertFalse(notary.verifyProof(execB, hashA));
    }

    // 8. Domain Separation / Chain ID
    function test_GetChainId() public view {
        assertEq(notary.getChainId(), block.chainid);
    }

    // 9. Fuzz Testing (10,000 runs configured in foundry.toml)
    function testFuzz_NotarizeValidProof(
        bytes32 execId,
        bytes32 resultHash,
        bytes32 artifactCommitment
    ) public {
        vm.assume(execId != bytes32(0));
        vm.assume(resultHash != bytes32(0));

        vm.prank(notarizer);
        bool ok = notary.notarizeResult(execId, resultHash, artifactCommitment);
        assertTrue(ok);

        assertTrue(notary.hasProof(execId));
        assertTrue(notary.verifyProof(execId, resultHash));

        IResultNotary.NotarizationProof memory proof = notary.getProof(execId);
        assertEq(proof.executionId, execId);
        assertEq(proof.resultHash, resultHash);
        assertEq(proof.artifactCommitment, artifactCommitment);
        assertEq(proof.notarizer, notarizer);
    }

    function testFuzz_ReplayProtection(
        bytes32 execId,
        bytes32 resultHash1,
        bytes32 resultHash2,
        bytes32 artifactCommitment
    ) public {
        vm.assume(execId != bytes32(0));
        vm.assume(resultHash1 != bytes32(0));
        vm.assume(resultHash2 != bytes32(0));

        vm.prank(notarizer);
        notary.notarizeResult(execId, resultHash1, artifactCommitment);

        // Second attempt MUST revert regardless of hash
        vm.prank(notarizer);
        vm.expectRevert(abi.encodeWithSelector(ResultNotary.AlreadyNotarized.selector, execId));
        notary.notarizeResult(execId, resultHash2, artifactCommitment);

        // First hash preserved
        assertEq(notary.getProof(execId).resultHash, resultHash1);
    }

    function testFuzz_VerifyProofIntegrity(
        bytes32 execId,
        bytes32 validHash,
        bytes32 queryHash
    ) public {
        vm.assume(execId != bytes32(0));
        vm.assume(validHash != bytes32(0));

        vm.prank(notarizer);
        notary.notarizeResult(execId, validHash, bytes32(0));

        if (queryHash == validHash) {
            assertTrue(notary.verifyProof(execId, queryHash));
        } else {
            assertFalse(notary.verifyProof(execId, queryHash));
        }
    }
}
