---
name: nautilus-chain-ops
description: Nautilus 的任务生命周期完整流程（发布→抢单→实现→评审→链上华币奖励，含每步的 API 与状态流转）与私链管理台（管理员专用的 geth 节点管理、签名者、余额发放）。改 api/tasks.py、chain_admin.py、services/chain_manager.py，或排查任务卡在某个状态、链上交易异常时读这份。
---

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

## 私链管理台（管理员专用，`api/chain_admin.py` + `services/chain_manager.py`）

网页 `/admin/chain`（前端 `RequireAdmin` 守卫 + 菜单条件渲染）让管理员启停整条链、
启停/增删单个节点、用 clique 投票选入/罢免签名者、看链与各节点实时状态。后端挂在
`/api/admin/chain`，每个端点都过 `get_current_admin_user`。进程由 Python 直接起停
（`subprocess.Popen` + psutil 按**监听端口反查 PID**，故不持久化 PID，后端重启会自动
认领在跑的 geth，命令行起的节点也一样能管）；组网与签名者投票走 geth 的 HTTP RPC
（`clique`/`admin`/`miner` 本就在每个节点的 `--http.api` 里）。

四条硬约束（改这块前必读 `services/chain_manager.py` 头注释）：

- **任何 start/restart 之后必须重跑 `mesh_peers()`** —— 见上文「peering 是纯内存态」。
  状态接口的 `isolated` 字段就是用来暴露这种"绿着骗人"的节点的。
- **enode 必须用 `admin_nodeInfo().enode` 原文**，不能从 `.id` 拼 —— `.id` 不是 enode
  公钥，拼出来的 enode 会让 `addPeer` 返回 true 却永远连不上。
- **node1 禁止单独停止/删除**（`RPC_ENTRY_NODE` 409）—— 它是 `.env` 里 `PRIVATE_RPC`
  指的那个节点。停掉它链还在出块（剩 4 个签名者 ≥ 下限 3），但后端所有链上写入立刻全挂。
  只有「停止整条链」（需 `{"confirm":"STOP"}`）能带走它。
- **跌破出块下限一律硬拒**（`QUORUM_WOULD_BREAK` 409），不给 force 逃生口。

变更类端点返回 `202 + op_id`（起链要 30~45 秒，clique 投票要等出块），前端轮询
`GET /operations/{op_id}` 看进度与日志；护栏在拿到单写锁后**同步**跑完，所以护栏不通过
是当场 409，不会先给一个注定失败的 op。`/status` 与 `/operations` 两个只读端点在
`main.py` 里被 `limiter.exempt()` 从全局 `default_limits`（200/hour）豁免 —— slowapi 的
middleware 对所有路由无条件套用 default_limits（路由级 `@limiter.limit` **覆盖不掉**，
见 `slowapi/extension.py:614-633`），不豁免的话管理台秒级轮询十分钟就会把管理员自己 429 掉。


## 私链上的固定地址（本机 geth Clique POA，chainId 13370，RPC :8545）

| 合约 | 地址 | 说明 |
|---|---|---|
| 华币 HUA | `0x5FbDB2315678afecb367f032d93F642f641...` | 18 位精度，结算币 |
| NAU | `0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512` | PoUW 代币 |

结算一律走 `blockchain/web3_config.py::get_web3_config`。
`get_blockchain_service()` 是遗留的 best-effort 层，`/health` 报的 chain_id 来自它、**不是私链**。
