// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, console2} from "forge-std/Test.sol";
import {ReputationRegistry} from "../src/ReputationRegistry.sol";
import {IReputationRegistry} from "../src/interfaces/IReputationRegistry.sol";
import {ResultNotary} from "../src/ResultNotary.sol";
import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";

contract ReputationRegistryTest is Test {
    ResultNotary public notary;
    ReputationRegistry public registry;

    address public admin = address(0xAD01);
    address public oracle = address(0x08A);
    address public notarizer = address(0xBEEF);
    address public attacker = address(0xBAD);

    bytes32 public constant AGENT_ID = bytes32(uint256(0xAAA1));
    bytes32 public constant EXEC_ID = bytes32(uint256(0xEEE1));
    bytes32 public constant VERSION_ID = bytes32(uint256(0x100));
    bytes32 public constant RESULT_HASH = keccak256("canonical_result_data");
    bytes32 public constant EVIDENCE_HASH = keccak256("evidence_audit_trail");

    event ReputationEventRegistered(
        bytes32 indexed agentId,
        bytes32 indexed executionId,
        bytes32 agentVersionId,
        IReputationRegistry.OutcomeType outcomeType,
        bytes32 resultHash,
        bytes32 evidenceHash,
        address indexed reporter,
        uint256 timestamp
    );

    function setUp() public {
        notary = new ResultNotary(admin, notarizer);
        registry = new ReputationRegistry(admin, address(notary), oracle);
    }

    function _notarize(bytes32 execId, bytes32 resHash) internal {
        vm.prank(notarizer);
        notary.notarizeResult(execId, resHash, bytes32(0));
    }

    // ------------------------------------------------------------------------
    // CONSTRUCTOR & INITIALIZATION
    // ------------------------------------------------------------------------

    function test_ConstructorZeroAdminReverts() public {
        vm.expectRevert(ReputationRegistry.ZeroAdminAddress.selector);
        new ReputationRegistry(address(0), address(notary), oracle);
    }

    function test_ConstructorZeroNotaryReverts() public {
        vm.expectRevert(ReputationRegistry.ZeroResultNotaryAddress.selector);
        new ReputationRegistry(admin, address(0), oracle);
    }

    function test_ConstructorRolesAndPointers() public view {
        assertEq(address(registry.resultNotary()), address(notary));
        assertTrue(registry.hasRole(registry.DEFAULT_ADMIN_ROLE(), admin));
        assertTrue(registry.hasRole(registry.REPUTATION_ORACLE_ROLE(), oracle));
        assertFalse(registry.hasRole(registry.REPUTATION_ORACLE_ROLE(), attacker));
        assertEq(registry.getChainId(), block.chainid);
    }

    // ------------------------------------------------------------------------
    // ROLE ACCESS CONTROL
    // ------------------------------------------------------------------------

    function test_AdminCanGrantAndRevokeOracle() public {
        address newOracle = address(0x777);
        bytes32 role = registry.REPUTATION_ORACLE_ROLE();
        assertFalse(registry.hasRole(role, newOracle));

        vm.prank(admin);
        registry.grantRole(role, newOracle);
        assertTrue(registry.hasRole(role, newOracle));

        vm.prank(admin);
        registry.revokeRole(role, newOracle);
        assertFalse(registry.hasRole(role, newOracle));
    }

    function test_RevertUnauthorizedCaller() public {
        _notarize(EXEC_ID, RESULT_HASH);
        bytes32 role = registry.REPUTATION_ORACLE_ROLE();

        vm.expectRevert(
            abi.encodeWithSelector(
                IAccessControl.AccessControlUnauthorizedAccount.selector,
                attacker,
                role
            )
        );
        vm.prank(attacker);
        registry.registerReputationEvent(
            AGENT_ID,
            EXEC_ID,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            RESULT_HASH,
            EVIDENCE_HASH
        );
    }

    // ------------------------------------------------------------------------
    // PARAMETER VALIDATIONS
    // ------------------------------------------------------------------------

    function test_RevertZeroAgentId() public {
        vm.prank(oracle);
        vm.expectRevert(ReputationRegistry.ZeroAgentId.selector);
        registry.registerReputationEvent(
            bytes32(0),
            EXEC_ID,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_FAILURE,
            bytes32(0),
            EVIDENCE_HASH
        );
    }

    function test_RevertZeroExecutionId() public {
        vm.prank(oracle);
        vm.expectRevert(ReputationRegistry.ZeroExecutionId.selector);
        registry.registerReputationEvent(
            AGENT_ID,
            bytes32(0),
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_FAILURE,
            bytes32(0),
            EVIDENCE_HASH
        );
    }

    function test_RevertZeroAgentVersionId() public {
        vm.prank(oracle);
        vm.expectRevert(ReputationRegistry.ZeroAgentVersionId.selector);
        registry.registerReputationEvent(
            AGENT_ID,
            EXEC_ID,
            bytes32(0),
            IReputationRegistry.OutcomeType.VERIFIED_FAILURE,
            bytes32(0),
            EVIDENCE_HASH
        );
    }

    function test_RevertInvalidOutcomeType() public {
        vm.prank(oracle);
        vm.expectRevert(ReputationRegistry.InvalidOutcomeType.selector);
        registry.registerReputationEvent(
            AGENT_ID,
            EXEC_ID,
            VERSION_ID,
            IReputationRegistry.OutcomeType.NONE,
            bytes32(0),
            EVIDENCE_HASH
        );
    }

    // ------------------------------------------------------------------------
    // RESULTNOTARY BINDING & VERIFIED_SUCCESS
    // ------------------------------------------------------------------------

    function test_RevertUnnotarizedVerifiedSuccess() public {
        // Not notarized in ResultNotary
        vm.prank(oracle);
        vm.expectRevert(
            abi.encodeWithSelector(ReputationRegistry.ResultNotNotarized.selector, EXEC_ID, RESULT_HASH)
        );
        registry.registerReputationEvent(
            AGENT_ID,
            EXEC_ID,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            RESULT_HASH,
            EVIDENCE_HASH
        );
    }

    function test_RevertTamperedResultHashForVerifiedSuccess() public {
        _notarize(EXEC_ID, RESULT_HASH);
        bytes32 tamperedHash = keccak256("tampered_hash");

        vm.prank(oracle);
        vm.expectRevert(
            abi.encodeWithSelector(ReputationRegistry.ResultNotNotarized.selector, EXEC_ID, tamperedHash)
        );
        registry.registerReputationEvent(
            AGENT_ID,
            EXEC_ID,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            tamperedHash,
            EVIDENCE_HASH
        );
    }

    function test_ValidVerifiedSuccessRegistration() public {
        _notarize(EXEC_ID, RESULT_HASH);

        vm.expectEmit(true, true, false, true);
        emit ReputationEventRegistered(
            AGENT_ID,
            EXEC_ID,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            RESULT_HASH,
            EVIDENCE_HASH,
            oracle,
            block.timestamp
        );

        vm.prank(oracle);
        bool ok = registry.registerReputationEvent(
            AGENT_ID,
            EXEC_ID,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            RESULT_HASH,
            EVIDENCE_HASH
        );
        assertTrue(ok);

        assertTrue(registry.hasRecord(EXEC_ID));
        IReputationRegistry.ReputationRecord memory rec = registry.getRecord(EXEC_ID);
        assertEq(rec.agentId, AGENT_ID);
        assertEq(rec.executionId, EXEC_ID);
        assertEq(rec.agentVersionId, VERSION_ID);
        assertEq(uint8(rec.outcomeType), uint8(IReputationRegistry.OutcomeType.VERIFIED_SUCCESS));
        assertEq(rec.resultHash, RESULT_HASH);
        assertEq(rec.evidenceHash, EVIDENCE_HASH);
        assertEq(rec.reporter, oracle);
        assertEq(rec.timestamp, block.timestamp);
        assertEq(rec.blockNumber, block.number);
    }

    // ------------------------------------------------------------------------
    // NON-SUCCESS OUTCOMES (FAILURE, TIMEOUT, CANCELLATION)
    // ------------------------------------------------------------------------

    function test_ValidNonSuccessOutcomes() public {
        bytes32 execFail = bytes32(uint256(0xF1));
        bytes32 execTimeout = bytes32(uint256(0xF2));
        bytes32 execCancel = bytes32(uint256(0xF3));

        // 1. Failure without result hash
        vm.prank(oracle);
        registry.registerReputationEvent(
            AGENT_ID,
            execFail,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_FAILURE,
            bytes32(0),
            EVIDENCE_HASH
        );
        assertTrue(registry.hasRecord(execFail));

        // 2. Timeout without result hash
        vm.prank(oracle);
        registry.registerReputationEvent(
            AGENT_ID,
            execTimeout,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_TIMEOUT,
            bytes32(0),
            EVIDENCE_HASH
        );
        assertTrue(registry.hasRecord(execTimeout));

        // 3. Cancellation without result hash
        vm.prank(oracle);
        registry.registerReputationEvent(
            AGENT_ID,
            execCancel,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_CANCELLATION,
            bytes32(0),
            EVIDENCE_HASH
        );
        assertTrue(registry.hasRecord(execCancel));
    }

    // ------------------------------------------------------------------------
    // ON-CHAIN REPLAY PROTECTION
    // ------------------------------------------------------------------------

    function test_RevertDuplicateRegistrationSameParameters() public {
        _notarize(EXEC_ID, RESULT_HASH);

        vm.prank(oracle);
        registry.registerReputationEvent(
            AGENT_ID,
            EXEC_ID,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            RESULT_HASH,
            EVIDENCE_HASH
        );

        vm.prank(oracle);
        vm.expectRevert(
            abi.encodeWithSelector(ReputationRegistry.AlreadyRegistered.selector, EXEC_ID)
        );
        registry.registerReputationEvent(
            AGENT_ID,
            EXEC_ID,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            RESULT_HASH,
            EVIDENCE_HASH
        );
    }

    function test_RevertConflictingDuplicateRegistration() public {
        _notarize(EXEC_ID, RESULT_HASH);

        vm.prank(oracle);
        registry.registerReputationEvent(
            AGENT_ID,
            EXEC_ID,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            RESULT_HASH,
            EVIDENCE_HASH
        );

        // Attempt to register same execution as FAILURE
        vm.prank(oracle);
        vm.expectRevert(
            abi.encodeWithSelector(ReputationRegistry.AlreadyRegistered.selector, EXEC_ID)
        );
        registry.registerReputationEvent(
            AGENT_ID,
            EXEC_ID,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_FAILURE,
            bytes32(0),
            EVIDENCE_HASH
        );
    }

    function test_CrossExecutionIsolation() public {
        bytes32 exec1 = bytes32(uint256(0x1111));
        bytes32 exec2 = bytes32(uint256(0x2222));
        bytes32 hash1 = keccak256("hash1");
        bytes32 hash2 = keccak256("hash2");

        _notarize(exec1, hash1);
        _notarize(exec2, hash2);

        vm.prank(oracle);
        registry.registerReputationEvent(
            AGENT_ID,
            exec1,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            hash1,
            EVIDENCE_HASH
        );

        vm.prank(oracle);
        registry.registerReputationEvent(
            AGENT_ID,
            exec2,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            hash2,
            EVIDENCE_HASH
        );

        assertEq(registry.getRecord(exec1).resultHash, hash1);
        assertEq(registry.getRecord(exec2).resultHash, hash2);
    }

    function test_RevertGetRecordNotFound() public {
        bytes32 unknownExec = bytes32(uint256(0x9999));
        vm.expectRevert(
            abi.encodeWithSelector(ReputationRegistry.RecordNotFound.selector, unknownExec)
        );
        registry.getRecord(unknownExec);
    }

    // ------------------------------------------------------------------------
    // PROPERTY-BASED FUZZ TESTS (10,000 RUNS EACH)
    // ------------------------------------------------------------------------

    function testFuzz_RegisterValidReputationEvent(
        bytes32 agentId,
        bytes32 executionId,
        bytes32 versionId,
        bytes32 resultHash,
        bytes32 evidenceHash
    ) public {
        vm.assume(agentId != bytes32(0));
        vm.assume(executionId != bytes32(0));
        vm.assume(versionId != bytes32(0));
        vm.assume(resultHash != bytes32(0));

        // Pre-notarize in ResultNotary
        vm.prank(notarizer);
        notary.notarizeResult(executionId, resultHash, bytes32(0));

        vm.prank(oracle);
        bool success = registry.registerReputationEvent(
            agentId,
            executionId,
            versionId,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            resultHash,
            evidenceHash
        );
        assertTrue(success);

        assertTrue(registry.hasRecord(executionId));
        IReputationRegistry.ReputationRecord memory rec = registry.getRecord(executionId);
        assertEq(rec.agentId, agentId);
        assertEq(rec.executionId, executionId);
        assertEq(rec.resultHash, resultHash);
    }

    function testFuzz_ReplayProtection(
        bytes32 agentId,
        bytes32 executionId,
        bytes32 versionId,
        bytes32 resultHash
    ) public {
        vm.assume(agentId != bytes32(0));
        vm.assume(executionId != bytes32(0));
        vm.assume(versionId != bytes32(0));
        vm.assume(resultHash != bytes32(0));

        vm.prank(notarizer);
        notary.notarizeResult(executionId, resultHash, bytes32(0));

        vm.prank(oracle);
        registry.registerReputationEvent(
            agentId,
            executionId,
            versionId,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            resultHash,
            bytes32(0)
        );

        // Second registration must strictly revert
        vm.prank(oracle);
        vm.expectRevert(
            abi.encodeWithSelector(ReputationRegistry.AlreadyRegistered.selector, executionId)
        );
        registry.registerReputationEvent(
            agentId,
            executionId,
            versionId,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            resultHash,
            bytes32(0)
        );
    }

    function testFuzz_ResultNotaryBindingIntegrity(
        bytes32 agentId,
        bytes32 executionId,
        bytes32 unnotarizedHash
    ) public {
        vm.assume(agentId != bytes32(0));
        vm.assume(executionId != bytes32(0));
        vm.assume(unnotarizedHash != bytes32(0));

        // Never notarized in ResultNotary -> MUST revert
        vm.prank(oracle);
        vm.expectRevert(
            abi.encodeWithSelector(ReputationRegistry.ResultNotNotarized.selector, executionId, unnotarizedHash)
        );
        registry.registerReputationEvent(
            agentId,
            executionId,
            VERSION_ID,
            IReputationRegistry.OutcomeType.VERIFIED_SUCCESS,
            unnotarizedHash,
            EVIDENCE_HASH
        );
    }
}
