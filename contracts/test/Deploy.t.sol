// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test, console2} from "forge-std/Test.sol";
import {DeployScript} from "../script/Deploy.s.sol";
import {AgentRegistry} from "../src/AgentRegistry.sol";
import {Escrow} from "../src/Escrow.sol";
import {RevenueDistributor} from "../src/RevenueDistributor.sol";
import {ResultNotary} from "../src/ResultNotary.sol";
import {ReputationRegistry} from "../src/ReputationRegistry.sol";
import {MockUSDC} from "../test/mocks/MockUSDC.sol";

contract DeployScriptTest is Test {
    DeployScript public deployScript;

    address internal constant BASE_SEPOLIA_USDC = 0x036CbD53842c5426634e7929541eC2318f3dCF7e;
    address internal constant ANVIL_DEFAULT_ADDRESS = 0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266;
    uint256 internal constant ANVIL_DEFAULT_PRIVATE_KEY = uint256(0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80);

    address internal validDeployer = address(0x473888C859F88D3De7b5A20986805b4dC3178189);
    uint256 internal validDeployerKey = uint256(0x703f4e6283731359f0f06c02ee4753a30f6424098e6b45aca799567b664c755d);

    function setUp() public {
        deployScript = new DeployScript();
    }

    function _getValidSepoliaConfig() internal view returns (DeployScript.DeploymentConfig memory config) {
        config.chainId = 84532;
        config.deployerPrivateKey = validDeployerKey;
        config.roles.deployer = validDeployer;
        config.roles.admin = validDeployer;
        config.roles.arbitrator = validDeployer;
        config.roles.pauser = validDeployer;
        config.roles.notarizer = validDeployer;
        config.roles.stakerRecipient = address(0x51a0000000000000000000000000000000000001);
        config.roles.daoRecipient = address(0xda00000000000000000000000000000000000002);
        config.roles.reputationOracle = validDeployer;
        config.usdc = BASE_SEPOLIA_USDC;
    }

    function test_DeployLocal_SuccessWithDeterministicDefaults() public {
        DeployScript.DeploymentConfig memory config;
        config.chainId = 31337;
        config.deployerPrivateKey = ANVIL_DEFAULT_PRIVATE_KEY;
        config.usdc = address(0);

        (
            AgentRegistry registry,
            Escrow escrow,
            RevenueDistributor distributor,
            ResultNotary notary,
            ReputationRegistry reputationRegistry,
            address usdc
        ) = deployScript.deployWithConfig(config);

        assertTrue(address(registry) != address(0));
        assertTrue(address(escrow) != address(0));
        assertTrue(address(distributor) != address(0));
        assertTrue(address(notary) != address(0));
        assertTrue(address(reputationRegistry) != address(0));
        assertTrue(usdc != address(0));
    }

    function test_DeployMainnet_RevertsMainnetDeploymentBlocked() public {
        DeployScript.DeploymentConfig memory config;
        config.chainId = 8453;
        config.deployerPrivateKey = validDeployerKey;

        vm.expectRevert(DeployScript.MainnetDeploymentBlocked.selector);
        deployScript.deployWithConfig(config);
    }

    function test_DeploySepolia_RevertsWhenAnvilDefaultKeyProvided() public {
        DeployScript.DeploymentConfig memory config = _getValidSepoliaConfig();
        config.deployerPrivateKey = ANVIL_DEFAULT_PRIVATE_KEY;

        vm.expectRevert(abi.encodeWithSelector(DeployScript.AnvilAccountBlockedOnLiveNetwork.selector, ANVIL_DEFAULT_ADDRESS));
        deployScript.deployWithConfig(config);
    }

    function test_DeploySepolia_RevertsWhenDeployerKeyMissing() public {
        DeployScript.DeploymentConfig memory config = _getValidSepoliaConfig();
        config.deployerPrivateKey = 0;

        vm.expectRevert(DeployScript.ExplicitDeployerKeyRequired.selector);
        deployScript.deployWithConfig(config);
    }

    function test_DeploySepolia_RevertsWhenRoleAddressMissing() public {
        DeployScript.DeploymentConfig memory config = _getValidSepoliaConfig();
        config.roles.admin = address(0);

        vm.expectRevert(abi.encodeWithSelector(DeployScript.ExplicitRoleConfigurationRequired.selector, "ADMIN_ADDRESS"));
        deployScript.deployWithConfig(config);
    }

    function test_DeploySepolia_RevertsWhenUsdcInvalid() public {
        DeployScript.DeploymentConfig memory config = _getValidSepoliaConfig();
        config.usdc = address(0x1234);

        vm.expectRevert(abi.encodeWithSelector(DeployScript.InvalidUsdcAddressForNetwork.selector, 84532, address(0x1234), BASE_SEPOLIA_USDC));
        deployScript.deployWithConfig(config);
    }

    function test_DeploySepolia_SucceedsWithExplicitConfiguration() public {
        DeployScript.DeploymentConfig memory config = _getValidSepoliaConfig();

        MockUSDC mock = new MockUSDC();
        vm.etch(BASE_SEPOLIA_USDC, address(mock).code);

        (
            AgentRegistry registry,
            Escrow escrow,
            RevenueDistributor distributor,
            ResultNotary notary,
            ReputationRegistry reputationRegistry,
            address usdc
        ) = deployScript.deployWithConfig(config);

        assertTrue(address(registry) != address(0));
        assertTrue(address(escrow) != address(0));
        assertTrue(address(distributor) != address(0));
        assertTrue(address(notary) != address(0));
        assertTrue(address(reputationRegistry) != address(0));
        assertEq(usdc, BASE_SEPOLIA_USDC);
    }
}
