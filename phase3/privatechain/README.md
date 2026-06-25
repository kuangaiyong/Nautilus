# Nautilus 私有以太坊链（geth Clique POA）

内网/单机运行的以太坊私链，替代 Base 公链。用 Docker 部署，由 `docker-compose.private.yml` 中的 `geth` 服务启动。

## 组成

| 文件 | 作用 |
|---|---|
| `genesis.template.json` | 创世模板。`__EXTRADATA__` / `__SIGNER__` 占位符在首次启动时由 entrypoint 注入 |
| `entrypoint.sh` | 首次启动导入/生成签名账户、渲染并 `geth init`，之后以该账户出块 |
| `password.txt` | keystore 解锁密码（**测试用，生产请替换**） |

## 关键参数

- **chainId**: `13370`（与 `genesis.template.json`、后端 `web3_config.py`、`hardhat.config.js` 三处一致）
- **共识**: Clique POA，出块周期 5s
- **gas**: `baseFeePerGas=0` + `--miner.gasprice 0`，签名账户预分配 1,000,000 ETH，交易实质免费
- **RPC**: HTTP `:8545`、WS `:8546`，仅绑内网

## 签名 / 部署账户

私链需要一个 POA 签名账户，它同时用作合约部署账户（`DEPLOYER_PRIVATE_KEY`）和后端服务端签名账户（`BLOCKCHAIN_PRIVATE_KEY`）。两种方式：

1. **自动生成（默认）**：不设 `SIGNER_PRIVATE_KEY`，首次启动自动生成账户，地址写入数据卷 `signer.addr`。
   - 取出私钥：`docker exec nautilus-geth cat /data/keystore/<file>`，或预先用方式 2 指定。
2. **指定私钥（推荐用于联调）**：在 `.env.private` 设 `SIGNER_PRIVATE_KEY=0x...`，三处账户用同一把，部署与签名直接复用。

## 安全须知

- `password.txt` 与任何测试私钥**仅限内网/测试**。生产部署前必须更换密码、改用公司自管私钥，并将其移出版本库（已在 `.gitignore` 忽略 keystore 与私钥）。
- 私链 RPC 不得暴露到公网；`--http.vhosts` 等放开仅因纯内网环境。

## 验证

```bash
# 链 ID
curl -s -X POST http://localhost:8545 -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}'
# => {"result":"0x343a"}  (0x343a = 13370)

# 出块高度（多次调用应递增）
curl -s -X POST http://localhost:8545 -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'
```
