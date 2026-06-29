# CLAUDE.md - Nautilus Project Configuration

> 这个文件会被所有Claude Code会话自动加载

## 项目信息

**项目名称**: Nautilus - AI Agent生态系统
**目标**: 让AI Agent真正"活"起来 - 能赚钱、能进化、能繁衍
**当前阶段**: 内网私有化（`dev/private-network-port`）—— 私有链 + 华币结算 + LLM 统一网关

> 架构与本机运行方式以仓库根 `CLAUDE.md` 为准；本文件部分历史内容（Base/USDC/
> PostgreSQL）已被私有化改造取代，下文技术栈/区块链/命令已更新为私有网络口径。

## 核心规则（必须遵守）

### 1. 响应风格
- 简洁直接，不重复
- 不使用emoji（除非明确要求）
- 不输出代码，直接用工具编辑
- 完成后简短确认，不解释

### 2. 并行执行
- 独立任务必须在单个消息中并行调用
- 不等待不必要的中间结果
- 最大化并行效率

### 3. 工具优先级
- Read > cat
- Edit > sed
- Write > echo
- Glob > find
- Grep > grep
- 只在必要时使用Bash（git、npm、系统命令）

### 4. 任务管理
- 复杂任务（≥3步）使用TaskCreate
- 开始工作前标记in_progress
- 完成后立即标记completed

## 技术栈

### Backend
- Python 3.11+, FastAPI
- SQLAlchemy, Alembic（私有化部署的库由 `create_all` 管理，非 Alembic）
- 数据库：私有网络用 SQLite（`nautilus_private.db`）；公链模式可用 PostgreSQL/Redis
- Web3.py, eth-account

### Frontend
- React 19, TypeScript, Vite（目录为 `phase3/website`）
- TailwindCSS, MetaMask

### Blockchain（由 `BLOCKCHAIN_NETWORK` 选择）
- 私有网络（当前）：原生 geth Clique POA，chainId `13370`，RPC `:8545`
  - 华币 HUA（18 位精度，结算币）：`0x5FbDB2315678afecb367f032d93F642f64180aa3`
  - NAU（PoUW 代币）：`0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512`
  - 结算走 `blockchain/web3_config.py`（`get_web3_config`）；`get_blockchain_service`
    是遗留 best-effort 层，`/health` 的 chain_id 来自它、非私链
- 公链（历史）：Base Chain (8453)，USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`

## 质量标准

### 代码质量
- 测试覆盖率 ≥ 80%
- 代码评分 ≥ 9.0/10
- 无CRITICAL/HIGH问题

### 性能目标
- 单用户P95 < 500ms
- 并发P95 < 1000ms
- 吞吐量 > 10 req/s
- 缓存命中率 > 60%

## 协作模式

### 三对话框系统
- **Dialog A**: 开发执行（团队模式）
- **Dialog B**: 架构协调（战略决策）
- **Dialog C**: 质量保证（Phase审查）

### 工作流程
1. 创建任务
2. 并行执行
3. 持续审查
4. Phase验收

## 关键约定

### 钱包认证
```python
# 必须使用encode_defunct
from eth_account.messages import encode_defunct
message_hash = encode_defunct(text=message)
recovered = w3.eth.account.recover_message(message_hash, signature)

# 地址小写比较
if recovered.lower() != address.lower():
    raise Exception("Invalid signature")
```

### API响应格式
```python
{
    "success": true,
    "data": {},
    "error": null,
    "meta": {}
}
```

### 错误处理
```python
raise HTTPException(
    status_code=400,
    detail={
        "error": {
            "code": "ERROR_CODE",
            "message": "User friendly message",
            "details": {}
        }
    }
)
```

## 快速命令

```bash
# Backend（私有网络模式：用 venv，CWD=phase3/backend，无 --reload，改动需重启）
C:/nautilus-venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
C:/nautilus-venv/Scripts/python.exe -m pytest tests/                 # 全量
C:/nautilus-venv/Scripts/python.exe tests/e2e_task_lifecycle.py      # 端到端：发布→抢单→实现→评审→奖励
# 存量 SQLite 库结构变更用独立脚本（非 alembic），改前备份 .db：
#   C:/nautilus-venv/Scripts/python.exe migrate_wei_columns.py

# 私链（原生 geth，5 签名节点）
powershell C:\nautilus-privatechain\start-geth.ps1     # node1（RPC :8545）
powershell C:\nautilus-privatechain\start-nodes.ps1    # node2~5，启动后需手动 addPeer 互联

# Frontend
cd website && npm run dev
```

## 重要文档

- `WEEK2_IMPLEMENTATION_PLAN.md` - Week 2计划
- `SURVIVAL_MECHANISM_DESIGN.md` - 生存机制设计
- `API_CONTRACT_SPEC.md` - API规范
- `CLAUDE_CODE_ENHANCEMENT_PLAN.md` - Claude Code强化

## 当前状态

**内网私有化改造**：私有链（geth POA / chainId 13370）+ 华币结算 + LLM 统一网关已落地。
核心任务生命周期（发布→抢单→实现→评审→链上华币奖励）已端到端验证，回归脚本见
`backend/tests/e2e_task_lifecycle.py`。

## 联系方式

- Dialog A: 开发执行
- Dialog B: 架构协调
- Dialog C: 质量保证

---

**详细规则**: 参见 `~/.claude/rules/` 目录
