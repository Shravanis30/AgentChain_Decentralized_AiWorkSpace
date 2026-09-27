// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Ownable2Step, Ownable} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

/**
 * @title AgentRegistry
 * @notice Foundation contract for registering and managing autonomous AI agents on AgentChain.
 */
contract AgentRegistry is Ownable2Step, Pausable {
    struct AgentProfile {
        address operator;
        string metadataURI;
        uint256 stakedAmount;
        bool isActive;
        uint256 registeredAt;
    }

    uint256 public totalAgents;
    mapping(uint256 => AgentProfile) public agents;
    mapping(address => uint256[]) private _operatorAgents;

    event AgentRegistered(uint256 indexed agentId, address indexed operator, string metadataURI, uint256 timestamp);
    event AgentStatusUpdated(uint256 indexed agentId, bool isActive);

    error AgentNotFound(uint256 agentId);
    error UnauthorizedOperator(uint256 agentId, address caller);
    error EmptyMetadataURI();

    constructor(address initialOwner) Ownable(initialOwner) {}

    /**
     * @notice Registers a new autonomous agent under the caller's operator address.
     * @param metadataURI Off-chain metadata URI (IPFS / HTTPS) describing agent capabilities.
     * @return agentId The newly assigned unique agent identifier.
     */
    function registerAgent(string calldata metadataURI) external whenNotPaused returns (uint256 agentId) {
        if (bytes(metadataURI).length == 0) revert EmptyMetadataURI();

        unchecked {
            totalAgents++;
        }
        agentId = totalAgents;

        agents[agentId] = AgentProfile({
            operator: msg.sender,
            metadataURI: metadataURI,
            stakedAmount: 0,
            isActive: true,
            registeredAt: block.timestamp
        });

        _operatorAgents[msg.sender].push(agentId);

        emit AgentRegistered(agentId, msg.sender, metadataURI, block.timestamp);
    }

    /**
     * @notice Updates the active status of an agent.
     */
    function setAgentStatus(uint256 agentId, bool isActive) external {
        AgentProfile storage profile = agents[agentId];
        if (profile.operator == address(0)) revert AgentNotFound(agentId);
        if (profile.operator != msg.sender && owner() != msg.sender) {
            revert UnauthorizedOperator(agentId, msg.sender);
        }

        profile.isActive = isActive;
        emit AgentStatusUpdated(agentId, isActive);
    }

    /**
     * @notice Returns all agent IDs registered by a specific operator.
     */
    function getOperatorAgents(address operator) external view returns (uint256[] memory) {
        return _operatorAgents[operator];
    }

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }
}
