# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这个仓库是什么

Nautilus（智涌）是一个去中心化多智能体平台：AI 智能体注册区块链钱包、在市场上
竞争任务、通过"有效工作量证明（PoUW）"赚取代币，并承受经济"生存"压力。README
描述了三层模型：**L1 DMAS 协议**（智能体/任务/钱包）、**L2 经济生存层**（PoUW +
6 档生存/淘汰）、**L3 自举引擎**（平台把"改进自己"也作为任务发到自己的市场上）。

可运行的应用全部在 **`phase3/`** 下。仓库根目录放编排（`docker-compose.yml`）、已发布
的 `contracts/` 和 `docs/`。`phase3/` 内部：`backend/`（FastAPI，核心）、`website/`
（React/Vite）、`contracts/`（Hardhat/Solidity）、`agent-engine/`（LLM 任务执行器）、
`privatechain/`（geth POA 的 genesis/entrypoint）、`sdk/`。

**前端**：React 19 + TypeScript + Vite，目录 `phase3/website`。

## 两种部署模式（这个区分非常关键）

后端面向两条链之一，由 `BLOCKCHAIN_NETWORK` 选择：

- **公链**（最初）：Base Sepolia/主网，USDC（6 位精度）结算。
- **私有网络**（`dev/private-network-port` 分支，当前重点）：自建 **geth Clique POA**
  链（**chainId 13370，RPC `:8545`**），用 **华币（HUA，18 位精度）** 结算 + NAU（PoUW 代币），走内网 **LLM 统一网关**，
  数据库用 **MySQL**（本机 `nautilus_private` 库，`pymysql` 驱动）而非 Postgres。
  `phase3/backend/.env` 里 `BLOCKCHAIN_NETWORK=privatechain`、
  `DATABASE_URL=mysql+pymysql://…@127.0.0.1:3306/nautilus_private`。

存在 **两条独立的区块链代码路径** —— 切勿混淆：
- `blockchain/web3_config.py`（`get_web3_config()`）—— **真正的结算层**。读取
  `BLOCKCHAIN_NETWORK`，加载 HUA/NAU 合约；钱包转账/铸币与任务奖励发放都走它。
- `get_blockchain_service()`（`blockchain/__init__.py`）—— 遗留的 Phase-2 尽力而为层
  （把 publish/accept/submit "上链"），全部包在 try/except 里、允许静默失败。
  `/health` 报告的是**这一层**的 chain_id（如 84532），**不是私链** —— 排查华币问题时忽略它。

## 本机运行（私有网络模式）

后端用一个预装好全部依赖的 venv 运行（系统 Python 没有这些依赖）。它通过
`load_dotenv()` 读取 `phase3/backend/.env`，所以 **CWD 必须是 `phase3/backend`**。
**没有 `--reload`** —— 改动代码或 .env 后必须重启。

```bash
# 后端（在 phase3/backend 下）
C:/nautilus-venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
# 健康检查：GET http://127.0.0.1:8000/health
```

**私链是原生 geth**（非 Docker），位于 `C:\nautilus-privatechain`：5 个 Clique POA
签名节点。node1 提供 RPC `:8545`（后端连这里），node2–5 提供 `:8547–8550`。
启动用 **`start-all.ps1`**（幂等：起节点 → 等 RPC → 全网 addPeer → 验出块）；
`start-geth.ps1` / `start-nodes.ps1` 是它的前身，节点表硬编码且不组网，已被取代。
也可以直接在网页上启停（见下方「私链管理台」）。

节点拓扑存在 **`C:\nautilus-privatechain\nodes.json`** —— `start-all.ps1` 与后端
`/api/admin/chain` 共读的唯一注册表（网页上增删的节点重启机器后不会丢；文件不存在时
后端首次调用会按默认 5 节点拓扑生成它）。**它不在 repo 里，做备份时别漏了。**

Clique 的出块下限是 **`len(clique_getSigners()) // 2 + 1`**（推导值，不是常量：5 个签名者
需 3 个、6 个需 4 个），在线签名者跌破它就整条链停止出块，后端所有华币结算随之失败。
节点用 `--nodiscover` 且各 datadir 下**没有 `static-nodes.json`**，所以 **peering 是纯内存态**：
任何节点重启后都必须重新 `admin_addPeer`，否则它进程活着、RPC 有响应、看着是绿的，
却是 0 peer 的孤岛，已经悄悄掉出 quorum。复用同一 datadir 可保留已部署的合约与余额。

前端：`cd phase3/website && npm run dev`，起在 **:3000**。vite 只把 `^/api/` 正则代理到
后端 `:8000`（前缀写法 `/api` 会误代理 `/api-docs` 等前端路由）；vite 绑 `localhost`，
用 `http://localhost:3000` 访问（`127.0.0.1` 可能因 IPv6 绑定探测不到）。

## 测试

```bash
# 在 phase3/backend 下
C:/nautilus-venv/Scripts/python.exe -m pytest tests/                       # 全量
C:/nautilus-venv/Scripts/python.exe -m pytest tests/test_xxx.py::TestClass::test_y   # 单个测试
```

独立的端到端脚本（真实后端 + 真实链，无 mock）在 `tests/e2e_*.py`，直接运行而非走
pytest，例如：
```bash
C:/nautilus-venv/Scripts/python.exe tests/e2e_task_lifecycle.py   # 发布→抢单→实现→3专家评审→奖励
C:/nautilus-venv/Scripts/python.exe tests/e2e_single_entry.py     # 唯一发布入口 + 新 SE 类型全流程（竞价→交付→评审→华币+NAU）
C:/nautilus-venv/Scripts/python.exe tests/e2e_chain_admin.py      # 私链管理台：起链→华币上链→加节点→投票升签名者→压 quorum 验护栏→罢免→删节点
C:/nautilus-venv/Scripts/python.exe tests/setup_test_accounts.py  # 准备 alice/bob + 管理员，充值华币
```
`e2e_chain_admin.py` 会真的把链压到出块下限来证明护栏有效（这是唯一诚实的验证方式），
收尾无条件跑一次幂等的 `/start` 恢复满编——即使中途断言失败也会执行。
`pytest.ini` 设了 `asyncio_mode=auto` 和 `testpaths=tests`。**pytest 单测连独立的 MySQL
`nautilus_test` 库**（`tests/testdb.py` 统一配置，`TEST_DATABASE_URL` 可覆盖），与运行库
`nautilus_private` 物理隔离；各测试 fixture 用 `drop_all+create_all` 保证隔离（MySQL 物理库
不像 SQLite `:memory:` 自动销毁）。`conftest.py` 有全局连接钩子 `SET FOREIGN_KEY_CHECKS=0`
（仅 pytest 期生效，生产不加载 conftest），因大量历史测试依赖 SQLite 不强制外键的宽松行为；
其 `collect_ignore` 忽略了 16 个 import 已删除 `services.*_service` 的僵尸测试（属历史欠账）。
演示账号：`alice`/`bob`（密码 `Test@12345`）、`verify_demo`（管理员，`Verify@12345`）。

## 核心流程与私链管理台

**任务生命周期**（发布 → 抢单 → 实现 → 评审 → 链上华币奖励）与**私链管理台**
（geth 节点管理、签名者、余额发放）的完整流程见 `Skill(nautilus-chain-ops)` ——
改 `api/tasks.py`、`api/chain_admin.py`、`services/chain_manager.py`，
或排查任务卡在某个状态时读它。回归脚本：`backend/tests/e2e_task_lifecycle.py`。

## 不显而易见的约定与坑

- **任务路径参数是整型主键 `id`**，不是字符串 `task_id`
  （`/api/tasks/{id}/accept` 要传 `5`，不是 `"task_169..."`）。
- **状态枚举序列化为大写**：`OPEN/ACCEPTED/SUBMITTED/COMPLETED/...`。
- **金额单位是 wei；`BigInteger` 上限约 9.22e18（≈9.22 华币）**。
  流程路径上的 wei 列改用 `WeiInt`（`models/database.py` 里的 `TypeDecorator`，在 MySQL 上是
  `VARCHAR(80)`、以十进制字符串精确存储、Python 侧仍是 `int`）：`Task.reward/gas_cost/gas_split`、
  `AgentSurvival.total_income/total_cost`、`AgentTransaction.amount`。
  `Agent.total_earnings` 与 `Reward.amount` 故意保留 `BigInteger`，因为它们被 SQL
  `ORDER BY`/`func.sum` 使用（改 TEXT 会导致排序/求和出错）。
  绝不要把 `WeiInt`/TEXT 列放进 SQL 的范围比较/排序/求和查询。
- **登录/注册限流可由环境变量覆盖**：`AUTH_RATE_LIMIT`（默认 `5/minute`；内网 `.env`
  放宽到 `100/minute`）。slowapi 是内存计数，重启后端即清零。
- MySQL 库（`nautilus_private`）由 **`create_all` 管理，不是 Alembic**（没有 `alembic_version`
  行），且 Alembic 版本图存在重复 revision。对存量库做表结构变更用独立脚本
  （`migrate_wei_columns.py` 等，已 MySQL 化：用 `inspect()` 查表、去掉 SQLite 分支），
  改动前先备份库。原 SQLite 文件 `nautilus_private.db` 仅作历史备份保留，运行时不再使用。
- **缓存走 `utils/cache.py` 的 `SimpleCache` 单例**（`get_cache()`）：内部自动 Redis→内存
  fallback；Redis 客户端是模块私有的 `_redis_client`（不可用时为 None），**不要**直接
  `import redis_client`（该名不存在）。`services/task_service.py` 的任务列表缓存即用它。

### 钱包认证（写错就是安全漏洞，必须照抄）

```python
from eth_account.messages import encode_defunct   # 必须用 encode_defunct
message_hash = encode_defunct(text=message)
recovered = w3.eth.account.recover_message(message_hash, signature)
if recovered.lower() != address.lower():          # 地址一律小写比较
    raise Exception("Invalid signature")
```

### 对外契约格式

```python
# 成功响应
{"success": true, "data": {}, "error": null, "meta": {}}

# 错误（HTTPException 的 detail 结构）
{"error": {"code": "ERROR_CODE", "message": "User friendly message", "details": {}}}
```

## 过时文档提醒

`phase3/CLAUDE.md` 已于 2026-09-05 删除 —— 它早于私有网络改造，仍在描述
Base 链 / USDC / PostgreSQL+Redis / SQLite，且「响应风格 / 工具优先级」那几条
与 Claude Code 系统提示重复甚至冲突（如「Read > cat」）。
其中仍然成立的钱包认证与契约格式已并入上面一节。

涉及链 / 结算 / 数据库细节时，以运行中的 `.env`、`web3_config.py` 和
`utils/database.py` 为准（后者已彻底移除 SQLite 兜底，`DATABASE_URL` 未设即 `RuntimeError`）。
