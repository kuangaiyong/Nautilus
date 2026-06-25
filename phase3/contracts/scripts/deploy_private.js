/**
 * 部署 Nautilus 全套合约到内网私有链，使用华币(HUA)作为结算稳定币。
 *
 * Usage:
 *   npx hardhat run scripts/deploy_private.js --network privatechain
 *
 * Required env vars (.env.private):
 *   DEPLOYER_PRIVATE_KEY  部署账户私钥（与私链签名账户一致）
 *   PRIVATE_RPC           私链 RPC（默认 http://127.0.0.1:8545）
 *   PRIVATE_CHAIN_ID      私链 chainId（默认 13370）
 *
 * 输出：deployed_private.json（含全部合约地址，供后端 .env.private 填写）
 */
const { ethers } = require("hardhat");
const fs = require("fs");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("部署账户:", deployer.address);
  console.log("余额:", ethers.formatEther(await ethers.provider.getBalance(deployer.address)), "ETH");

  // 1. 华币（结算稳定币，替代 USDC/USDT）
  const HuaCoin = await ethers.getContractFactory("HuaCoin");
  const hua = await HuaCoin.deploy();
  await hua.waitForDeployment();
  const huaAddr = await hua.getAddress();
  console.log("HuaCoin (HUA):", huaAddr);

  // 2. NAU（PoUW 激励代币）
  const NautilusToken = await ethers.getContractFactory("NautilusToken");
  const nau = await NautilusToken.deploy();
  await nau.waitForDeployment();
  const nauAddr = await nau.getAddress();
  console.log("NautilusToken (NAU):", nauAddr);

  // 3. RewardContract（5% 平台手续费，部署者初始收取）
  const RewardContract = await ethers.getContractFactory("RewardContract");
  const reward = await RewardContract.deploy(500, deployer.address);
  await reward.waitForDeployment();
  const rewardAddr = await reward.getAddress();
  console.log("RewardContract:", rewardAddr);

  // 4. TaskContract
  const TaskContract = await ethers.getContractFactory("TaskContract");
  const task = await TaskContract.deploy();
  await task.waitForDeployment();
  const taskAddr = await task.getAddress();
  console.log("TaskContract:", taskAddr);

  // 5. IdentityContract
  const IdentityContract = await ethers.getContractFactory("IdentityContract");
  const identity = await IdentityContract.deploy();
  await identity.waitForDeployment();
  const identityAddr = await identity.getAddress();
  console.log("IdentityContract:", identityAddr);

  // 6. WalletRegistry
  const WalletRegistry = await ethers.getContractFactory("WalletRegistry");
  const walletReg = await WalletRegistry.deploy();
  await walletReg.waitForDeployment();
  const walletRegAddr = await walletReg.getAddress();
  console.log("WalletRegistry:", walletRegAddr);

  // 7. 接线：华币作为唯一结算币加入白名单，部署者作验证引擎
  await (await task.setRewardContract(rewardAddr)).wait();
  await (await task.setVerificationEngine(deployer.address)).wait();
  await (await task.addSupportedToken(huaAddr)).wait();
  await (await reward.setTaskContract(taskAddr)).wait();
  await (await reward.addSupportedToken(huaAddr)).wait();
  console.log("接线完成：华币已加入 Task/Reward 白名单");

  const out = {
    BLOCKCHAIN_NETWORK: "privatechain",
    HUA_TOKEN_ADDRESS: huaAddr,
    NAU_TOKEN_ADDRESS: nauAddr,
    REWARD_CONTRACT_ADDRESS: rewardAddr,
    TASK_CONTRACT_ADDRESS: taskAddr,
    IDENTITY_CONTRACT_ADDRESS: identityAddr,
    WALLET_REGISTRY_ADDRESS: walletRegAddr,
  };
  console.log("\n=== 部署完成 ===");
  console.log(JSON.stringify(out, null, 2));
  fs.writeFileSync("deployed_private.json", JSON.stringify(out, null, 2));
  console.log("已写入 deployed_private.json");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
