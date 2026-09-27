// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {AgentRegistry} from "../src/AgentRegistry.sol";
import {Escrow} from "../src/Escrow.sol";
import {RevenueDistributor} from "../src/RevenueDistributor.sol";
import {ResultNotary} from "../src/ResultNotary.sol";
import {ReputationRegistry} from "../src/ReputationRegistry.sol";
import {MockUSDC} from "../test/mocks/MockUSDC.sol";

/**
 * @title DeployScript
 * @notice Production-grade deployment script for AgentChain smart contracts with strict fail-closed validation.
 * @dev Enforces chain ID, deployer signer validation, and role address verification before broadcasting transactions.
 */
contract DeployScript is Script {
    // Official verified Circle USDC addresses
    address public constant BASE_MAINNET_USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address public constant BASE_SEPOLIA_USDC = 0x036CbD53842c5426634e7929541eC2318f3dCF7e;

    uint256 public constant CHAIN_ID_LOCAL = 31337;
    uint256 public constant CHAIN_ID_BASE_SEPOLIA = 84532;
    uint256 public constant CHAIN_ID_BASE_MAINNET = 8453;

    error UnsupportedChainId(uint256 chainId);
    error InvalidUsdcAddressForNetwork(uint256 chainId, address provided, address expected);
    error ZeroAddressConfigured(string field);
    error MainnetDeploymentBlocked();
    error AnvilAccountBlockedOnLiveNetwork(address account);
    error ExplicitDeployerKeyRequired();
    error ExplicitRoleConfigurationRequired(string roleName);

    uint256 public constant ANVIL_DEFAULT_PRIVATE_KEY = uint256(0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80);
    address public constant ANVIL_DEFAULT_ADDRESS = 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266;

    struct DeploymentRoles {
        address deployer;
        address admin;
        address arbitrator;
        address pauser;
        address notarizer;
        address stakerRecipient;
        address daoRecipient;
        address reputationOracle;
    }

    struct DeploymentConfig {
        uint256 chainId;
        uint256 deployerPrivateKey;
        DeploymentRoles roles;
        address usdc;
    }

    function _getDeployerPrivateKey(uint256 chainId) internal view returns (uint256) {
        if (!vm.envExists("DEPLOYER_PRIVATE_KEY")) {
            if (chainId == CHAIN_ID_LOCAL) {
                return ANVIL_DEFAULT_PRIVATE_KEY;
            }
            revert ExplicitDeployerKeyRequired();
        }
        uint256 pk = vm.envUint("DEPLOYER_PRIVATE_KEY");
        if (pk == 0) {
            if (chainId == CHAIN_ID_LOCAL) {
                return ANVIL_DEFAULT_PRIVATE_KEY;
            }
            revert ExplicitDeployerKeyRequired();
        }
        return pk;
    }

    function _getRoleAddress(string memory envName, address fallbackAddr, bool required) internal view returns (address) {
        if (!vm.envExists(envName)) {
            if (required) revert ExplicitRoleConfigurationRequired(envName);
            return fallbackAddr;
        }
        address addr = vm.envAddress(envName);
        if (addr == address(0)) {
            if (required) revert ExplicitRoleConfigurationRequired(envName);
            return fallbackAddr;
        }
        return addr;
    }

    function loadConfigFromEnv() public view returns (DeploymentConfig memory config) {
        config.chainId = block.chainid;
        if (config.chainId == CHAIN_ID_BASE_MAINNET) {
            revert MainnetDeploymentBlocked();
        }

        config.deployerPrivateKey = _getDeployerPrivateKey(config.chainId);
        config.roles.deployer = vm.addr(config.deployerPrivateKey);

        if (config.chainId == CHAIN_ID_LOCAL) {
            config.roles.admin = _getRoleAddress("ADMIN_ADDRESS", config.roles.deployer, false);
            config.roles.arbitrator = _getRoleAddress("ARBITRATOR_ADDRESS", config.roles.deployer, false);
            config.roles.pauser = _getRoleAddress("PAUSER_ADDRESS", config.roles.deployer, false);
            config.roles.notarizer = _getRoleAddress("NOTARIZER_ADDRESS", config.roles.deployer, false);
            config.roles.stakerRecipient = _getRoleAddress("STAKER_RECIPIENT", address(0x51a0000000000000000000000000000000000001), false);
            config.roles.daoRecipient = _getRoleAddress("DAO_RECIPIENT", address(0xda00000000000000000000000000000000000002), false);
            config.roles.reputationOracle = _getRoleAddress("REPUTATION_ORACLE_ADDRESS", config.roles.deployer, false);
            config.usdc = vm.envOr("USDC_ADDRESS", address(0));
        } else if (config.chainId == CHAIN_ID_BASE_SEPOLIA) {
            config.roles.admin = _getRoleAddress("ADMIN_ADDRESS", address(0), true);
            config.roles.arbitrator = _getRoleAddress("ARBITRATOR_ADDRESS", address(0), true);
            config.roles.pauser = _getRoleAddress("PAUSER_ADDRESS", address(0), true);
            config.roles.notarizer = _getRoleAddress("NOTARIZER_ADDRESS", address(0), true);
            config.roles.stakerRecipient = _getRoleAddress("STAKER_RECIPIENT", address(0), true);
            config.roles.daoRecipient = _getRoleAddress("DAO_RECIPIENT", address(0), true);
            config.roles.reputationOracle = _getRoleAddress("REPUTATION_ORACLE_ADDRESS", address(0), true);
            config.usdc = vm.envOr("USDC_ADDRESS", BASE_SEPOLIA_USDC);
        } else {
            revert UnsupportedChainId(config.chainId);
        }
    }

    function deployWithConfig(DeploymentConfig memory config) public returns (
        AgentRegistry registry,
        Escrow escrow,
        RevenueDistributor distributor,
        ResultNotary notary,
        ReputationRegistry reputationRegistry,
        address usdc
    ) {
        if (config.chainId == CHAIN_ID_BASE_MAINNET) {
            revert MainnetDeploymentBlocked();
        }

        if (config.deployerPrivateKey == 0) {
            revert ExplicitDeployerKeyRequired();
        }
        config.roles.deployer = vm.addr(config.deployerPrivateKey);
        if (config.roles.deployer == address(0)) revert ZeroAddressConfigured("deployer");

        if (config.chainId == CHAIN_ID_LOCAL) {
            if (config.roles.admin == address(0)) config.roles.admin = config.roles.deployer;
            if (config.roles.arbitrator == address(0)) config.roles.arbitrator = config.roles.deployer;
            if (config.roles.pauser == address(0)) config.roles.pauser = config.roles.deployer;
            if (config.roles.notarizer == address(0)) config.roles.notarizer = config.roles.deployer;
            if (config.roles.stakerRecipient == address(0)) config.roles.stakerRecipient = address(0x51a0000000000000000000000000000000000001);
            if (config.roles.daoRecipient == address(0)) config.roles.daoRecipient = address(0xda00000000000000000000000000000000000002);
            if (config.roles.reputationOracle == address(0)) config.roles.reputationOracle = config.roles.deployer;
            usdc = config.usdc;
        } else if (config.chainId == CHAIN_ID_BASE_SEPOLIA) {
            if (config.deployerPrivateKey == ANVIL_DEFAULT_PRIVATE_KEY || config.roles.deployer == ANVIL_DEFAULT_ADDRESS) {
                revert AnvilAccountBlockedOnLiveNetwork(ANVIL_DEFAULT_ADDRESS);
            }

            if (config.roles.admin == address(0)) revert ExplicitRoleConfigurationRequired("ADMIN_ADDRESS");
            if (config.roles.arbitrator == address(0)) revert ExplicitRoleConfigurationRequired("ARBITRATOR_ADDRESS");
            if (config.roles.pauser == address(0)) revert ExplicitRoleConfigurationRequired("PAUSER_ADDRESS");
            if (config.roles.notarizer == address(0)) revert ExplicitRoleConfigurationRequired("NOTARIZER_ADDRESS");
            if (config.roles.stakerRecipient == address(0)) revert ExplicitRoleConfigurationRequired("STAKER_RECIPIENT");
            if (config.roles.daoRecipient == address(0)) revert ExplicitRoleConfigurationRequired("DAO_RECIPIENT");
            if (config.roles.reputationOracle == address(0)) revert ExplicitRoleConfigurationRequired("REPUTATION_ORACLE_ADDRESS");

            if (config.usdc != BASE_SEPOLIA_USDC) {
                revert InvalidUsdcAddressForNetwork(config.chainId, config.usdc, BASE_SEPOLIA_USDC);
            }
            usdc = BASE_SEPOLIA_USDC;
        } else {
            revert UnsupportedChainId(config.chainId);
        }

        console2.log("Deployer:", config.roles.deployer);
        console2.log("Admin:", config.roles.admin);
        console2.log("Arbitrator:", config.roles.arbitrator);
        console2.log("Pauser:", config.roles.pauser);
        console2.log("Staker Recipient:", config.roles.stakerRecipient);
        console2.log("DAO Recipient:", config.roles.daoRecipient);
        console2.log("Reputation Oracle:", config.roles.reputationOracle);

        vm.startBroadcast(config.deployerPrivateKey);

        // If on local Anvil and no USDC configured, deploy MockUSDC
        if (usdc == address(0)) {
            MockUSDC mock = new MockUSDC();
            usdc = address(mock);
            console2.log("Deployed MockUSDC at:", usdc);
        } else {
            console2.log("Using USDC at:", usdc);
        }

        registry = new AgentRegistry(config.roles.admin);
        console2.log("AgentRegistry deployed at:", address(registry));

        distributor = new RevenueDistributor(config.roles.admin, address(0), usdc, config.roles.stakerRecipient, config.roles.daoRecipient);
        console2.log("RevenueDistributor deployed at:", address(distributor));

        escrow = new Escrow(config.roles.admin, config.roles.arbitrator, config.roles.pauser, usdc, address(distributor));
        console2.log("Escrow deployed at:", address(escrow));

        distributor.setEscrowContract(address(escrow));
        console2.log("Bound Escrow to RevenueDistributor");

        notary = new ResultNotary(config.roles.admin, config.roles.notarizer);
        console2.log("ResultNotary deployed at:", address(notary));

        reputationRegistry = new ReputationRegistry(config.roles.admin, address(notary), config.roles.reputationOracle);
        console2.log("ReputationRegistry deployed at:", address(reputationRegistry));

        vm.stopBroadcast();
    }

    function run() external returns (
        AgentRegistry registry,
        Escrow escrow,
        RevenueDistributor distributor,
        ResultNotary notary,
        ReputationRegistry reputationRegistry,
        address usdc
    ) {
        DeploymentConfig memory config = loadConfigFromEnv();
        return deployWithConfig(config);
    }
}
