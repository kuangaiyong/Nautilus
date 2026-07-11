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

## 两种部署模式（这个区分非常关键）

后端面向两条链之一，由 `BLOCKCHAIN_NETWORK` 选择：

- **公链**（最初）：Base Sepolia/主网，USDC（6 位精度）结算。
- **私有网络**（`dev/private-network-port` 分支，当前重点）：自建 **geth Clique POA**
  链，用 **华币（HUA，18 位精度）** 结算 + NAU（PoUW 代币），走内网 **LLM 统一网关**，
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
签名节点。node1 提供 RPC `:8545`（后端连这里），node2–5 提供 `:8547–8550`。用
`start-geth.ps1`（node1）+ `start-nodes.ps1`（node2–5）启动。Clique 需要
**≥3 个签名者在线且互联**，否则出块停滞（"signed recently, must wait for others"）；
节点用 `--nodiscover`，所以启动后要手动互联（`admin_nodeInfo` → 对 node1
`admin_addPeer`）。复用同一 datadir 可保留已部署的合约与余额。

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
C:/nautilus-venv/Scripts/python.exe tests/setup_test_accounts.py  # 准备 alice/bob + 管理员，充值华币
```
`pytest.ini` 设了 `asyncio_mode=auto` 和 `testpaths=tests`。**pytest 单测连独立的 MySQL
`nautilus_test` 库**（`tests/testdb.py` 统一配置，`TEST_DATABASE_URL` 可覆盖），与运行库
`nautilus_private` 物理隔离；各测试 fixture 用 `drop_all+create_all` 保证隔离（MySQL 物理库
不像 SQLite `:memory:` 自动销毁）。`conftest.py` 有全局连接钩子 `SET FOREIGN_KEY_CHECKS=0`
（仅 pytest 期生效，生产不加载 conftest），因大量历史测试依赖 SQLite 不强制外键的宽松行为；
其 `collect_ignore` 忽略了 16 个 import 已删除 `services.*_service` 的僵尸测试（属历史欠账）。
演示账号：`alice`/`bob`（密码 `Test@12345`）、`verify_demo`（管理员，`Verify@12345`）。

## 核心流程：任务生命周期（`api/tasks.py`）

这是平台的心脏，也是绝大多数改动会触及的路径。**任务类型已全面软件工程化**：
`TaskType` 仅含 8 个 SE 全生命周期类型（需求分析/架构设计/代码开发/代码评审/测试用例
设计/自动化测试/部署运维/技术文档），旧通用类型（CODE/DATA/…）已删除（存量数据由
`migrate_se_only_task_types.py` 映射）。所有任务都走 SE PoUW 流程：

1. **发布（唯一入口）** —— `POST /api/tasks`（发布者 JWT）。奖励以 **wei** 传入。发布者
   首次使用时自动获得托管钱包（`ensure_user_wallet`）。**其余历史创建端点
   （academic/submit、marketplace/tasks/submit、labeling/jobs[/upload]、
   simulation/submit|batch、hub/bounties）一律 410 Gone**；前端唯一发布页 `/tasks/create`。
2. **竞价/抢单** —— 自动路径：cron `se_marketplace` 每分钟驱动自主竞价
   （`services/se_pouw_flow.py` 的 auto_bid + award，60s 竞价窗，声誉+专长加权择优）。
   手动兜底：`POST /api/tasks/{id}/accept` 先到先得；反作弊拦截自交易。
   SE 任务**不投**自动执行器队列（task_matcher 亦排除 SE 任务），由中标智能体自行交付。
3. **实现/提交** —— `POST /api/tasks/{id}/submit`（被指派的智能体）→ SUBMITTED。
4. **专家评审 + 奖励** —— `POST /api/tasks/{id}/complete`（仅发布者可触发）。结算前先经
   **3 个对口专长智能体 LLM 评审**（均分 ≥3/5 放行；LLM 不可用 503 fail-closed 可重试；
   不达标判 FAILED 不付款）。通过后先 `pay_hua_from_custodial` 链上结算华币（发布者托管
   私钥签 HUA 转账给 `agent.owner`，gasPrice 0），再按类型给中标者**铸 NAU**
   （`TASK_TYPE_REWARDS`），随后记录生存收入/成本。

支撑子系统：`services/wallet.py`（托管 HD 钱包、AES-GCM 私钥加密、
`pay_hua_from_custodial`）、`services/survival_service.py`（按智能体记录收入/成本/评分
→ 生存等级 → 淘汰）、`agent_executor.py` + `agent-engine/`（被抢任务的 LLM 自动执行）。
鉴权：`utils/auth.py` 的 JWT（`sub` = 用户名）或 API key；`get_current_user_or_agent`
通过 `agent.owner == user.wallet_address` 把用户 token 解析为 `(user, agent)`。
`main.py` 挂载约 48 个路由；tasks/agents/wallets/auth/survival 是核心。

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

## 过时文档提醒

`phase3/CLAUDE.md` 早于私有网络改造，仍在描述 Base 链 / USDC / PostgreSQL+Redis / SQLite，
均已过时。涉及链/结算/数据库细节时，以运行中的 `.env`、`web3_config.py` 和 `utils/database.py`
为准（后者已彻底移除 SQLite 兜底，`DATABASE_URL` 未设即 `RuntimeError`）。用户全局
`~/.claude/CLAUDE.md` 定义了强制的分支/验证/评审/发布工作流 —— 必须遵循。
