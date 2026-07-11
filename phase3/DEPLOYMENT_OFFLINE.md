# Nautilus 内网离线部署手册（Windows 局域网）

> 适用分支：`dev/private-network-port`（私有链 + 华币 HUA 结算 + LLM 统一网关，数据库 **MySQL**）。
>
> **目标网络环境**：目标机**无法访问公网**；内网已有 **MySQL / Redis / 私有化大模型（OpenAI 兼容）** 可用；
> **Python 的 pip、前端的 npm 依赖能正常下载**（内网镜像源），但**其他软件（Python 解释器、Node.js、geth 二进制等）都无法通过公网获取**，必须提前从有网机/开发机离线拷贝。

---

## 一、整体架构与部署策略

### 组件与端口

| 组件 | 说明 | 端口 | 来源 |
|---|---|---|---|
| MySQL | 数据库 `nautilus_private` | 3306 | **内网已有**，仅需建库 |
| Redis | 缓存 / 事件总线（用 DB 3） | 6379 | **内网已有**，仅需配置 |
| 私有化大模型 | OpenAI 兼容协议的 LLM 网关 | 由内网提供 | **内网已有**，仅需配置 |
| 私有链（geth） | Clique POA，chainId `13370`，5 签名节点 | RPC 8545（node1）、8547–8550（node2–5） | **离线拷贝** geth.exe + datadir |
| 后端（FastAPI） | 核心服务，venv 运行 | 8000 | 代码 + pip 装依赖 |
| 前端（React/Vite） | Web 界面 | 3000 | 代码 + npm 装依赖 |

### 两种部署策略（**强烈推荐"整体迁移"**）

| 策略 | 私有链 | 合约 | 数据库 | 适用 |
|---|---|---|---|---|
| **A. 整体迁移（推荐）** | 离线拷贝开发机整个 `C:\nautilus-privatechain`（含已部署合约与余额） | 无需重新部署 | 从开发机 `mysqldump` 导出后导入 | 绝大多数情况 |
| B. 全新部署 | 用 `genesis.json` 全新 `geth init` | 用 Hardhat 重新部署（**需 solc，离线困难**，见附录 A） | 空库，后端自动建表 | 无法拿到开发机 datadir 时 |

> **为什么推荐整体迁移**：合约部署依赖 Hardhat 编译，首次编译会联网拉取 `solc` 编译器，离线环境极易失败。
> 直接拷贝已部署好合约与余额的 datadir，可让 `.env` 里现成的合约地址直接可用，**完全绕开合约编译与部署**。
> 同时把数据库一并迁移，能保证**链上余额与链下钱包/账号数据一致**。

> ⚠️ **关键前提：`WALLET_MASTER_KEY` 必须与导出数据时一致**。托管钱包的私钥在数据库里是用
> `WALLET_MASTER_KEY`（AES-256-GCM）加密存储的。若迁移了数据库数据却换了这把主密钥，**所有托管钱包私钥将无法解密**，
> 转账/结算全部失败。整体迁移时，`.env` 的 `WALLET_MASTER_KEY` 保持原值不变。

---

## 二、离线物料准备清单（在开发机 / 有网机上打包）

打包成一个 U 盘 / 内网文件共享，拷到目标机。

### 必须离线拷贝（目标机无法下载）

| 物料 | 从哪里取 | 备注 |
|---|---|---|
| **项目代码** | `C:\code\Nautilus`（整个仓库，或 `git bundle`） | 含 `phase3/backend`、`phase3/website`、`phase3/contracts` |
| **Python 3.11 安装包** | python.org 下载 `python-3.11.x-amd64.exe` | 后端 venv 需要；系统若已装 3.11 可跳过 |
| **Node.js LTS 安装包** | nodejs.org 下载 `node-vXX-x64.msi`（建议 v18/20 LTS） | 前端 + 合约工具需要 |
| **私有链整套** | 开发机整个 `C:\nautilus-privatechain` 目录 | **含 `bin\geth.exe` + 5 个节点 datadir + `genesis.json` + `start-*.ps1` + `password.txt`**。geth 二进制无法内网下载，datadir 保留了已部署合约与余额 |
| **数据库导出**（策略 A） | 开发机执行 `mysqldump`（见 §四） | `nautilus_private.sql` |

### 目标机可内网下载（无需打包，但建议备好镜像源地址）

- **Python 依赖**：`pip install -r requirements.txt`（用内网 PyPI 镜像 `-i`）
- **前端依赖**：`npm install`（用内网 npm registry）

> 如果内网 pip/npm 也不稳，可在开发机预先离线打包：
> - Python：`pip download -r requirements.txt -d wheelhouse\`，拷 `wheelhouse\` 后 `pip install --no-index --find-links=wheelhouse -r requirements.txt`
> - 前端：直接拷开发机 `phase3\website\node_modules`（跨机同为 Windows x64 可用）

### 依赖体积提醒（重要）

`requirements.txt` 含 **torch / transformers / sentence-transformers / chromadb**（合计数 GB），它们只服务于
`services/memory_hierarchy.py` 的**向量记忆子系统**（依赖 pgvector，MySQL 环境下本就是旁路、不在核心任务流程）。
若内网带宽 / 磁盘紧张或不需要该功能，可在 `requirements.txt` 里**注释掉这 4 个包**再安装，核心功能
（认证 / 任务生命周期 / 钱包结算 / 生存 / cron）不受影响。
> 注意：**不要**改用 `requirements-minimal.txt`——它虽去掉了 ML 包，但同时漏掉了核心必需的
> `APScheduler`（cron 闭环）和 `bip32utils`（HD 托管钱包），会导致启动缺依赖。

---

## 三、目标机基础环境

### 1. 安装 Python 3.11 与 Node.js

```powershell
# 双击安装包，Python 务必勾选 "Add python.exe to PATH"
# 安装后验证
python --version      # 期望 3.11.x
node --version        # 期望 v18/20 LTS
npm --version
```

### 2. 确认内网 MySQL / Redis / LLM 可达

```powershell
# MySQL（把 HOST/USER/PASS 换成内网实际值）
# 若目标机没装 mysql 客户端，可用后端 venv 装好后用 python 测（见 §六.5）
mysql -h <MYSQL_HOST> -u <USER> -p -e "SELECT VERSION();"

# Redis（换成内网 redis-cli 路径与地址）
redis-cli -h <REDIS_HOST> -p 6379 ping        # 期望 PONG

# 私有化大模型（OpenAI 兼容，换成内网网关地址与 key）
curl <LLM_BASE_URL>/v1/models -H "Authorization: Bearer <LLM_API_KEY>"
```

> **记录好三项内网连接信息**（MySQL 主机/端口/账号/密码、Redis 主机/端口/密码、LLM 的 base_url/api_key/model），
> 第六步配置 `.env` 时要用。

---

## 四、数据库准备

### 建库（两种策略都要做）

```sql
-- 用内网 MySQL 账号登录后执行
CREATE DATABASE IF NOT EXISTS nautilus_private CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- 可选：跑自动化测试才需要
CREATE DATABASE IF NOT EXISTS nautilus_test    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 策略 A（整体迁移）：导入开发机数据

```powershell
# 开发机导出（含表结构 + 数据）
mysqldump -uroot -p --databases nautilus_private --default-character-set=utf8mb4 > nautilus_private.sql

# 目标机导入（换成内网连接参数）
mysql -h <MYSQL_HOST> -u <USER> -p nautilus_private < nautilus_private.sql
```

### 策略 B（全新）：留空即可

后端首次启动会自动 `create_all` 建表（`utils/database.py` → `init_db`，`WeiInt` 列在 MySQL 上会正确建为 `VARCHAR(80)`）。
建表后需用 §八 的脚本初始化演示账号。

---

## 五、私有链部署

### 1. 放置私链目录

把开发机整个 `C:\nautilus-privatechain` 拷到目标机**相同路径** `C:\nautilus-privatechain`。

> **强烈建议保持同路径 `C:\nautilus-privatechain`**：`start-all.ps1` 内部把该路径与各节点 datadir、`geth.exe`、
> `password.txt` 硬编码在脚本里。若目标机必须换路径，需同步修改 `start-all.ps1` / `start-geth.ps1` /
> `start-nodes.ps1` 里的 `$root` 变量。

### 2. 启动 5 节点并校验出块

```powershell
powershell -ExecutionPolicy Bypass -File C:\nautilus-privatechain\start-all.ps1
```

脚本会：启动 5 个节点 → 等 RPC 就绪 → 用真实 `enode` 全互联 addPeer → 校验出块。
看到 **`[OK] private chain is producing blocks`** 且 `node1 peers=4  block: N -> N+1` 即成功。脚本**幂等**，可重复运行。

### 3. 验证

```powershell
# 块高（应持续增长）
Invoke-RestMethod -Uri http://127.0.0.1:8545 -Method Post -ContentType application/json `
  -Body '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'
```

> Clique 需 **≥3 个签名者在线且互联**才连续出块；节点用 `--nodiscover`，必须由脚本 addPeer 互联。
> 私链参数（`genesis.json`）：chainId `13370`、出块 5s、`gasPrice 0`、5 个签名者写在 `extraData` 里，
> node1 签名者 `0xf39f…2266` 预分配了 gas 余额。

---

## 六、后端部署

### 1. 创建 venv（独立于系统 Python）

```powershell
python -m venv C:\nautilus-venv
```

### 2. 安装依赖（内网 pip 源）

```powershell
cd C:\code\Nautilus\phase3\backend
C:\nautilus-venv\Scripts\python.exe -m pip install --upgrade pip
# 用内网 PyPI 镜像；如公司镜像为 https://pypi.company.local/simple
C:\nautilus-venv\Scripts\python.exe -m pip install -r requirements.txt -i <内网PyPI镜像URL>
```

> 若走离线 wheelhouse：`... pip install --no-index --find-links=C:\wheelhouse -r requirements.txt`
> `pymysql`、`cryptography`、`web3`、`APScheduler`、`bip32utils` 必须装上（前两个是 MySQL 连接与私钥加密所需）。

### 3. 配置 `backend\.env`

在 `C:\code\Nautilus\phase3\backend\.env` 创建/编辑（该文件被 `.gitignore` 忽略、含机密，不入库）。
**以开发机 `.env` 为模板**，只改下列"内网对接项"，**密钥类保持迁移一致或全新生成**：

```ini
ENVIRONMENT=production
DEBUG=false
TESTING=false

# —— 改为内网 MySQL ——（driver 必须是 mysql+pymysql）
DATABASE_URL=mysql+pymysql://<USER>:<PASS>@<MYSQL_HOST>:3306/nautilus_private?charset=utf8mb4

# —— 改为内网 Redis ——（?protocol=2 见下方"坑"）
REDIS_HOST=<REDIS_HOST>
REDIS_PORT=6379
REDIS_DB=3
REDIS_PASSWORD=<可空>
REDIS_URL=redis://<REDIS_HOST>:6379/3?protocol=2

# —— 改为内网私有化大模型（OpenAI 兼容）——
LLM_BASE_URL=<内网LLM网关，如 http://llm.company.local/v1 或不带 /v1 视网关而定>
LLM_API_KEY=<内网key>
LLM_MODEL=<内网模型名>

# —— 私有链（与 §五拷贝的 datadir 匹配，整体迁移时保持不变）——
BLOCKCHAIN_NETWORK=privatechain
PRIVATE_RPC=http://127.0.0.1:8545
PRIVATE_CHAIN_ID=13370
HUA_TOKEN_ADDRESS=0x5FbDB2315678afecb367f032d93F642f64180aa3
NAU_TOKEN_ADDRESS=0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512
REWARD_CONTRACT_ADDRESS=0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0
TASK_CONTRACT_ADDRESS=0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9
IDENTITY_CONTRACT_ADDRESS=0xDc64a140Aa3E981100a9becA4E685f962f0cF6C9
WALLET_REGISTRY_ADDRESS=0x5FC8d32690cc91D4c39d9d3abcBD16989F875707
AUDIT_TRAIL_ADDRESS=0x04C89607413713Ec9775E14b954286519d836FEf
SIGNER_PRIVATE_KEY=<部署者/签名私钥，整体迁移保持不变>
BLOCKCHAIN_PRIVATE_KEY=<同上>
DEPLOYER_PRIVATE_KEY=<同上>

# —— 机密：整体迁移【必须保持与导出数据时一致】，全新部署可重新生成 ——
WALLET_MASTER_KEY=<AES-256 主密钥，见下方说明>
JWT_SECRET=<≥32 位随机串>
CSRF_SECRET_KEY=<随机串>

# —— 运行开关 ——
AUTONOMOUS_LOOP_ENABLED=false
BLOCKCHAIN_EVENT_LISTENER_ENABLED=false
DISABLE_WEB_SEARCH=true
AUTH_RATE_LIMIT=100/minute
# 前端访问地址（见 §七 CORS）
CORS_ORIGINS=http://localhost:3000
```

> **`WALLET_MASTER_KEY` / `JWT_SECRET` 从哪来**：
> - 整体迁移：直接沿用开发机 `.env` 的原值（尤其 `WALLET_MASTER_KEY`，否则托管钱包解密失败）。
> - 全新部署：可新生成。`WALLET_MASTER_KEY` 用 `C:\nautilus-venv\Scripts\python.exe -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"`；
>   `JWT_SECRET`/`CSRF_SECRET_KEY` 用任意 ≥32 位随机串。

### 4. 启动后端

```powershell
# CWD 必须是 backend（load_dotenv 读本目录 .env）；无 --reload，改代码/改 .env 必须重启
cd C:\code\Nautilus\phase3\backend
C:\nautilus-venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

### 5. 健康检查与读链自检

```powershell
# 后端健康（另开一个 PowerShell）
Invoke-RestMethod http://127.0.0.1:8000/health
# 期望 status=healthy、checks.database.status=healthy

# 后端 ↔ 私链 真实连通（读私链，chainId 应为 13370）
cd C:\code\Nautilus\phase3\backend
C:\nautilus-venv\Scripts\python.exe -c "from dotenv import load_dotenv; load_dotenv(); import models.agent_survival; from blockchain.web3_config import get_web3_config; c=get_web3_config(); print('connected', c.w3.is_connected(), '| chainId', c.chain_id, '| block', c.w3.eth.block_number)"
```

> `/health` 里出现的 `chain_id: 84532` 是**遗留 best-effort 层**，**不是**私链，排查结算问题时忽略它——
> 真实结算走 `web3_config` → 私链 `chainId 13370`（以上一条读链自检为准）。

---

## 七、前端部署

### 1. 安装依赖

```powershell
cd C:\code\Nautilus\phase3\website
npm install --registry <内网npm registry URL>
# 或直接拷开发机的 node_modules（同为 Windows x64）
```

### 2. 启动（二选一）

**方式 A：开发模式（简单，适合内部小范围使用）**

```powershell
npm run dev        # 起在 :3000，Vite 把 ^/api/ 代理到后端 :8000
```
浏览器打开 **`http://localhost:3000`**。

**方式 B：生产构建（更稳）**

```powershell
npm run build      # 产出 dist/（若 TS 报错阻塞，可用 npm run build:nocheck）
# 用任意静态服务器托管 dist/，例如：
C:\nautilus-venv\Scripts\python.exe -m http.server 3000 --directory dist
```
> 方式 B 下前端不再有 Vite 代理，需让静态服务器/反向代理把 `/api` 转发到 `:8000`，或让前端直连后端地址。
> 内部快速验证建议先用**方式 A**。

### 3. CORS（务必注意）

后端只放行 `.env` 的 `CORS_ORIGINS` 里精确列出的来源。**用哪个地址开页面，就把哪个地址配进去**：
- 本机访问：`http://localhost:3000`（默认已配）。
- 局域网其他机器访问：把 `CORS_ORIGINS=http://<本机内网IP>:3000` 加进去并**重启后端**，同时前端用 `npm run dev -- --host` 暴露到 `0.0.0.0`。
- 否则页面能打开但登录/任务列表等带凭证请求会被浏览器 CORS 拦截（表现为"打得开、点啥都没反应"）。

---

## 八、初始化演示账号与端到端验证

### 全新部署：建演示账号 + 铸华币

```powershell
cd C:\code\Nautilus\phase3\backend
# 注册 alice/bob + verify_demo(管理员)、发币、转账、发布任务，全链路真实验证
C:\nautilus-venv\Scripts\python.exe tests\setup_test_accounts.py
```
演示账号：`alice` / `bob`（密码 `Test@12345`）、`verify_demo`（管理员，`Verify@12345`）。

> 整体迁移：账号随数据库一起来了，无需此步（但仍需确认发布方账号有 HUA 余额）。

### 核心功能端到端验证（真实链、无 mock）

```powershell
cd C:\code\Nautilus\phase3\backend
# 发布→抢单→实现→3专家LLM评审→链上华币结算→生存收入入账
C:\nautilus-venv\Scripts\python.exe tests\e2e_task_lifecycle.py
```
末尾出现 **`E2E_PASS`** 即核心链路打通。随后可在前端用演示账号登录，手工走一遍发布/竞价/评审/钱包。

---

## 九、日常启停与开机自启

### 启动顺序（固定）

**① MySQL / Redis（内网已有，通常自启）→ ② 私链 → ③ 后端 → ④ 前端**

```powershell
# ② 私链
powershell -ExecutionPolicy Bypass -File C:\nautilus-privatechain\start-all.ps1
# ③ 后端（单开一个窗口，前台运行）
cd C:\code\Nautilus\phase3\backend; C:\nautilus-venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
# ④ 前端（再单开一个窗口）
cd C:\code\Nautilus\phase3\website; npm run dev
```

### 停止

```powershell
Get-Process geth -ErrorAction SilentlyContinue | Stop-Process -Force                                   # 私链（干净停机不损坏 datadir）
Get-NetTCPConnection -LocalPort 8000 -State Listen -EA SilentlyContinue | %{ Stop-Process -Id $_.OwningProcess -Force }  # 后端
Get-NetTCPConnection -LocalPort 3000 -State Listen -EA SilentlyContinue | %{ Stop-Process -Id $_.OwningProcess -Force }  # 前端
```

### 开机自启（可选）

后端/前端是前台进程，若要免登录自启，可用 **NSSM** 把 uvicorn、`npm run dev`、以及 `start-all.ps1` 注册成 Windows 服务，
或用**任务计划程序**建"开机触发"任务（注意设置好工作目录 = 各自的 CWD）。私链也可同法托管，但要保证其在后端之前启动。

---

## 十、常见问题排查

| 现象 | 原因 / 处理 |
|---|---|
| 私链块高不增长（日志 `signed recently, must wait for others`） | 在线签名者 < 3 或未互联。重跑 `start-all.ps1`（幂等，会补互联）。确认 5 个节点进程都在。 |
| 后端启动报 `DATABASE_URL 未设置` | `.env` 没配 `DATABASE_URL` 或 CWD 不是 `backend`。`utils/database.py` 已移除 SQLite 兜底，MySQL 连接串必填。 |
| 后端连不上 MySQL | 检查 `DATABASE_URL` 主机/账号/密码、驱动是否为 `mysql+pymysql`、内网 MySQL 是否放行该客户端 IP。 |
| Redis 报 `unknown command 'HELLO'` | 内网 Redis 是旧版（只支持 RESP2），`REDIS_URL` 末尾必须带 **`?protocol=2`**（禁用 redis-py 的 RESP3 握手）。 |
| 托管钱包转账/结算失败、私钥解密报错 | 迁移了数据库但 `WALLET_MASTER_KEY` 与导出时不一致。必须用原主密钥。 |
| 自己写脚本读私链报 `extraData is 97 bytes` | Clique POA 的坑。web3 需注入 `ExtraDataToPOAMiddleware`（`w3.middleware_onion.inject(..., layer=0)`）。后端本体已处理。 |
| 前端能打开但登录/接口无反应 | CORS。用访问用的确切 Origin 配 `CORS_ORIGINS` 并重启后端（见 §七.3）。 |
| LLM 评审报 503 / 超时 | `LLM_BASE_URL`/`KEY`/`MODEL` 配错或内网网关不通。任务 `complete` 前有 3 专家 LLM 评审，LLM 不可用会 fail-closed。用 §三 的 curl 先验证网关。 |
| pip 装 torch 极慢 / 失败 | 见 §二"依赖体积提醒"，可注释掉 torch/transformers/sentence-transformers/chromadb 四个包（向量记忆旁路，不影响核心）。 |
| 改了代码/`.env` 不生效 | 后端**无 `--reload`**，必须重启 uvicorn 进程。 |

---

## 附录 A：全新部署私链 + 合约（无法拷 datadir 时）

> 仅在拿不到开发机 datadir 时使用。**难点：合约编译需 `solc`，Hardhat 首次编译会联网下载，离线需预置**。
> 可行做法：在开发机（有网）预先执行一次 `npx hardhat compile` 让 `solc` 缓存到 `~/.cache/hardhat-nodejs/`（Windows 为
> `%USERPROFILE%\.cache\hardhat-nodejs\` 或 `AppData`），把该缓存目录连同 `phase3\contracts\node_modules`、`artifacts` 一起离线拷到目标机。

```powershell
# 1) 用 genesis 初始化 5 个节点的 datadir（各 datadir 目录、password.txt 参照 start-all.ps1 的节点定义）
C:\nautilus-privatechain\bin\geth.exe --datadir C:\nautilus-privatechain\data  init C:\nautilus-privatechain\genesis.json
# node2~5 同理 init 各自 datadir
# 2) 启动并互联
powershell -ExecutionPolicy Bypass -File C:\nautilus-privatechain\start-all.ps1
# 3) 部署合约（需上面预置好的 solc 缓存与 node_modules）
cd C:\code\Nautilus\phase3\contracts
npx hardhat run scripts\deploy_private.js --network privatechain      # 华币/NAU 等；另见 deploy_nau.js / deploy_wallet_registry.js
# 4) 把部署输出的各合约地址回填到 backend\.env（HUA/NAU/REWARD/TASK/IDENTITY/WALLET_REGISTRY/AUDIT_TRAIL）
```

`hardhat.config.js` 里 `privatechain` 网络应指向 `http://127.0.0.1:8545`、chainId `13370`、`gasPrice 0`。
部署账号用 `DEPLOYER_PRIVATE_KEY`（genesis 预分配余额的 `0xf39f…2266` 对应私钥，即 `.env` 里那把）。

---

## 附录 B：`backend\.env` 配置项速查

| 分组 | 键 | 内网部署时 |
|---|---|---|
| 数据库 | `DATABASE_URL` | **改**为内网 MySQL（`mysql+pymysql://…/nautilus_private?charset=utf8mb4`） |
| 缓存 | `REDIS_HOST/PORT/DB/PASSWORD`、`REDIS_URL` | **改**为内网 Redis，`REDIS_URL` 带 `?protocol=2`，`REDIS_DB=3` |
| LLM | `LLM_BASE_URL/API_KEY/MODEL` | **改**为内网私有化大模型（OpenAI 兼容） |
| 链 | `BLOCKCHAIN_NETWORK=privatechain`、`PRIVATE_RPC`、`PRIVATE_CHAIN_ID=13370` | 保持 |
| 合约地址 | `HUA/NAU/REWARD/TASK/IDENTITY/WALLET_REGISTRY/AUDIT_TRAIL_ADDRESS` | 整体迁移保持；全新部署回填新地址 |
| 链私钥 | `SIGNER/BLOCKCHAIN/DEPLOYER_PRIVATE_KEY` | 整体迁移保持 |
| 机密 | `WALLET_MASTER_KEY` | 整体迁移**必须保持不变**；全新可生成 |
| 机密 | `JWT_SECRET`、`CSRF_SECRET_KEY` | 整体迁移沿用或换（换 JWT 会使旧登录态失效）；全新生成 |
| 开关 | `AUTONOMOUS_LOOP_ENABLED`、`BLOCKCHAIN_EVENT_LISTENER_ENABLED`、`DISABLE_WEB_SEARCH` | 按需（默认关，减少后台噪声） |
| 限流 | `AUTH_RATE_LIMIT` | 内网可放宽（如 `100/minute`） |
| CORS | `CORS_ORIGINS` | 配前端实际访问 Origin |

---

**启动顺序一句话**：内网 MySQL/Redis/LLM 就绪 → `start-all.ps1` 起私链并确认出块 → venv 起后端 `/health` 绿 →
`npm run dev` 起前端 → `http://localhost:3000` 登录验证。
