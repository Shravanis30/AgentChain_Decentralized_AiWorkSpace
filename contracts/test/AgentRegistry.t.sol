// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, console2} from "forge-std/Test.sol";
import {AgentRegistry} from "../src/AgentRegistry.sol";

contract AgentRegistryTest is Test {
    AgentRegistry public registry;
    address public owner = address(0x1);
    address public operator = address(0x2);
    address public stranger = address(0x3);

    event AgentRegistered(uint256 indexed agentId, address indexed operator, string metadataURI, uint256 timestamp);

    function setUp() public {
        registry = new AgentRegistry(owner);
    }

    function test_RegisterAgent() public {
        vm.startPrank(operator);
        string memory uri = "ipfs://QmTestMetadata123";

        vm.expectEmit(true, true, false, true);
        emit AgentRegistered(1, operator, uri, block.timestamp);

        uint256 agentId = registry.registerAgent(uri);
        assertEq(agentId, 1, "First agentId should be 1");
        assertEq(registry.totalAgents(), 1, "Total agents should be 1");

        (address regOperator, string memory regUri,, bool isActive,) = registry.agents(agentId);
        assertEq(regOperator, operator, "Operator mismatch");
        assertEq(regUri, uri, "URI mismatch");
        assertTrue(isActive, "Agent should be active by default");

        uint256[] memory opAgents = registry.getOperatorAgents(operator);
        assertEq(opAgents.length, 1, "Operator should have 1 agent");
        assertEq(opAgents[0], 1, "Operator agentId should be 1");
        vm.stopPrank();
    }

    function test_RevertOnEmptyURI() public {
        vm.prank(operator);
        vm.expectRevert(AgentRegistry.EmptyMetadataURI.selector);
        registry.registerAgent("");
    }

    function test_SetAgentStatus() public {
        vm.prank(operator);
        uint256 agentId = registry.registerAgent("ipfs://QmTest");

        // Stranger cannot update status
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(AgentRegistry.UnauthorizedOperator.selector, agentId, stranger));
        registry.setAgentStatus(agentId, false);

        // Operator can update status
        vm.prank(operator);
        registry.setAgentStatus(agentId, false);
        (,,, bool isActive,) = registry.agents(agentId);
        assertFalse(isActive, "Agent should be deactivated");
    }
}
