/**
 * 部署 TaskAuditTrail（任务生命周期可信追踪存证合约）到内网私有链。
 *
 * Usage:
 *   npx hardhat run scripts/deploy_audit_trail.js --network privatechain
 *
 * Required env vars (contracts/.env):
 *   DEPLOYER_PRIVATE_KEY  部署账户私钥（与私链签名账户一致）
 *   PRIVATE_RPC           私链 RPC（默认 http://127.0.0.1:8545）
 *   PRIVATE_CHAIN_ID      私链 chainId（默认 13370）
 *
 * 输出：把地址追加到 deployed_audit_trail.json（供后端 .env 填 AUDIT_TRAIL_ADDRESS）。
 */
const { ethers } = require("hardhat");
const fs = require("fs");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("部署账户:", deployer.address);

  const TaskAuditTrail = await ethers.getContractFactory("TaskAuditTrail");
  const audit = await TaskAuditTrail.deploy();
  await audit.waitForDeployment();
  const addr = await audit.getAddress();
  console.log("TaskAuditTrail:", addr);

  const out = { AUDIT_TRAIL_ADDRESS: addr };
  fs.writeFileSync("deployed_audit_trail.json", JSON.stringify(out, null, 2));
  console.log("\n已写入 deployed_audit_trail.json：", JSON.stringify(out));
  console.log("请把 AUDIT_TRAIL_ADDRESS 写入 phase3/backend/.env 后重启后端。");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
