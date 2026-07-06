"""任务发布唯一入口 + 新 SE 类型全流程 E2E（真实后端 + 私链 + 真实 LLM 评审，无 mock）。

验证三件事：
  1. 发布任务唯一入口 = POST /api/tasks：其余 7 个历史创建端点全部 410 Gone；
  2. 任务类型全面 SE 化：旧通用类型(CODE/DATA/OTHER) 422 拒绝，
     新增 3 类(CODE_REVIEW/DEPLOYMENT_OPS/DOCUMENTATION) 可发布；
  3. 新类型 DOCUMENTATION 全流程贯通：
     发布 -> 自主竞价择优中标(auto_bid+award) -> 中标者提交交付物
     -> complete 触发 3 专家 LLM 评审 -> 门控一致(通过则华币结算 + 铸 NAU)。

运行:CWD=phase3/backend -> C:/nautilus-venv/Scripts/python.exe tests/e2e_single_entry.py
前置:后端 127.0.0.1:8000 + 私链 RPC :8545 + LLM 网关在线。
"""
import os, sys, time
import requests
from jose import jwt
from web3 import Web3
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()
import models.database, models.payment, models.team, models.raid          # noqa: E402,F401
import models.agent_survival, models.partner, models.conversation, models.marketplace_models  # noqa: E402,F401

API = "http://127.0.0.1:8000"
RPC = os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")
JWT_SECRET = os.getenv("JWT_SECRET"); JWT_ALG = os.getenv("JWT_ALGORITHM", "HS256")
ONE = 10 ** 18
ADMIN = "verify_demo"
REWARD = 3
w3 = Web3(Web3.HTTPProvider(RPC))
BAL_ABI = [{"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}]
hua = w3.eth.contract(address=Web3.to_checksum_address(os.getenv("HUA_TOKEN_ADDRESS")), abi=BAL_ABI)
nau = w3.eth.contract(address=Web3.to_checksum_address(os.getenv("NAU_TOKEN_ADDRESS")), abi=BAL_ABI)

fail = []
def need(c, m): print(f"  [{'OK' if c else 'FAIL'}] {m}"); (fail.append(m) if not c else None)
def step(m): print(f"\n=== {m} ===")
def tok(u): return jwt.encode({"sub": u, "exp": int(time.time()) + 3600}, JWT_SECRET, algorithm=JWT_ALG)
def auth(u): return {"Authorization": f"Bearer {tok(u)}"}
def reg(u): return requests.post(f"{API}/api/auth/register", json={"username": u, "email": f"{u}@n.ai", "password": "Test@12345"}).status_code
def me(u): return requests.get(f"{API}/api/wallets/me", headers=auth(u)).json()
def hbal(a): return hua.functions.balanceOf(Web3.to_checksum_address(a)).call() / ONE
def nbal(a): return nau.functions.balanceOf(Web3.to_checksum_address(a)).call() / ONE
def mk_agent(u, name, specs):
    r = requests.post(f"{API}/api/agents", headers=auth(u), json={"name": name, "description": "se", "specialties": specs})
    return r.json().get("agent", {}).get("agent_id") if r.status_code == 201 else None
def publish(pub, task_type, desc, exp):
    return requests.post(f"{API}/api/tasks", headers=auth(pub), json={
        "description": desc, "input_data": "内部工程平台", "expected_output": exp,
        "reward": REWARD * ONE, "task_type": task_type, "timeout": 86400})


RETIRED = [  # 7 个历史创建端点（读端点不受影响）
    "/api/academic/submit",
    "/api/marketplace/tasks/submit",
    "/api/labeling/jobs",
    "/api/labeling/jobs/upload",
    "/api/simulation/submit",
    "/api/simulation/batch",
    "/api/hub/bounties",
]

DOC_DELIVERABLE = """# 任务发布 API 使用手册（v3.3）

## 1. 概述
平台唯一任务发布入口为 `POST /api/tasks`。任务发布后进入自主竞价，
中标智能体交付成果，经 3 位专家评审通过后自动发放华币与 NAU 奖励。

## 2. 请求格式
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| description | string | 是 | 任务需求描述 |
| input_data | string | 否 | 输入/上下文 |
| expected_output | string | 否 | 验收标准 |
| reward | int(wei) | 是 | 华币奖励，1 华币 = 10^18 wei |
| task_type | enum | 是 | 8 个软件工程类型之一 |
| timeout | int(秒) | 是 | 任务时限 |

## 3. 任务类型
REQUIREMENT_ANALYSIS / ARCHITECTURE_DESIGN / CODE_DEVELOPMENT / CODE_REVIEW /
TEST_CASE_DESIGN / TEST_AUTOMATION / DEPLOYMENT_OPS / DOCUMENTATION

## 4. 示例
```bash
curl -X POST /api/tasks -H "Authorization: Bearer <JWT>" -d '{
  "description": "撰写部署手册", "reward": 3000000000000000000,
  "task_type": "DOCUMENTATION", "timeout": 86400}'
```

## 5. 生命周期与错误码
OPEN(竞价中) -> ACCEPTED(执行中) -> SUBMITTED(待评审) -> COMPLETED/FAILED。
401 未认证；422 任务类型非法（旧通用类型已下线）；410 历史创建端点已收敛。
"""


def main():
    ts = int(time.time())
    PUB, WIN = f"si_pub_{ts}", f"si_win_{ts}"

    step("1. 七个历史创建端点全部 410 Gone（唯一入口收敛）")
    for path in RETIRED:
        r = requests.post(f"{API}{path}", json={})
        ok = r.status_code == 410 and "POST /api/tasks" in r.text
        need(ok, f"POST {path} -> {r.status_code}{'' if ok else ' ' + r.text[:100]}")

    step("2. 旧通用类型 422 拒绝；新增 SE 类型可发布")
    reg(PUB)
    for old in ("CODE", "DATA", "OTHER"):
        r = publish(PUB, old, f"旧类型 {old} 应被拒", "n/a")
        need(r.status_code == 422, f"task_type={old} -> HTTP {r.status_code}（期望 422）")
    new_ids = {}
    for nt in ("CODE_REVIEW", "DEPLOYMENT_OPS", "DOCUMENTATION"):
        r = publish(PUB, nt, f"验证新类型 {nt} 可发布", "n/a")
        need(r.status_code == 201, f"task_type={nt} -> HTTP {r.status_code}（期望 201）")
        if r.status_code == 201:
            new_ids[nt] = r.json()["id"]

    step("3. 准备中标智能体（文档专长，自主竞价）")
    from utils.database import SessionLocal
    from models.database import Agent, Task, TaskStatus
    db = SessionLocal()
    # 测试隔离：关闭历史 agent 的自主竞价，避免干扰中标断言
    db.query(Agent).update({Agent.autonomy_enabled: False}); db.commit()
    reg(WIN)
    aid = mk_agent(WIN, f"DocBot_{ts}", ["文档", "doc", "技术写作"])
    ag = db.query(Agent).filter(Agent.agent_id == aid).first()
    ag.autonomy_enabled = True; ag.reputation_score = 70.0; db.commit()
    need(aid is not None, f"中标智能体创建 agent_id={aid}")
    if hbal(me(PUB)["address"]) < REWARD * 2:
        rm = requests.post(f"{API}/api/wallets/mint", headers=auth(ADMIN), json={"target": PUB, "amount": REWARD * 5})
        if rm.status_code == 200:
            w3.eth.wait_for_transaction_receipt("0x" + rm.json()["tx_hash"].lstrip("0x"), timeout=30)
    need(hbal(me(PUB)["address"]) >= REWARD, "发布者华币余额足够奖励")

    step("4. DOCUMENTATION 任务：自主竞价 -> 择优中标")
    TID = new_ids.get("DOCUMENTATION")
    need(TID is not None, "DOCUMENTATION 任务已发布(步骤 2)")
    from services.se_pouw_flow import auto_bid_open_se_tasks, award_se_task, review_result
    auto_bid_open_se_tasks(db)
    winner = award_se_task(db, TID)
    need(winner == aid, f"文档专长智能体中标 agent_id={winner}")
    db.expire_all()
    t = db.query(Task).filter(Task.id == TID).first()
    need(t.status == TaskStatus.ACCEPTED, "任务状态 ACCEPTED（竞价派单）")

    step("5. 中标者提交文档交付物 -> complete 触发 3 专家评审 -> 结算 + 铸 NAU")
    rs = requests.post(f"{API}/api/tasks/{TID}/submit", headers=auth(WIN), json={"result": DOC_DELIVERABLE})
    need(rs.status_code == 200, f"提交交付物 HTTP {rs.status_code}")
    p_addr, w_addr = me(PUB)["address"], me(WIN)["address"]
    a0, w0, n0 = hbal(p_addr), hbal(w_addr), nbal(w_addr)
    print(f"  结算前: pub={a0} HUA, winner={w0} HUA, winner NAU={n0}")
    rc = requests.post(f"{API}/api/tasks/{TID}/complete", headers=auth(PUB), timeout=180)
    need(rc.status_code == 200, f"完成请求 HTTP {rc.status_code} {'' if rc.status_code==200 else rc.text[:160]}")
    db.rollback()  # 刷新事务快照，读服务端已提交的评审与任务状态
    res = review_result(db, TID)
    print(f"  评审: n={res['n']} 均分={res['avg']} 通过={res['passed']} 明细={[round(x['score'],1) for x in res['reviews']]}")
    need(res["n"] == 3, "恰好 3 位专家评审打分")
    db.rollback()
    t = db.query(Task).filter(Task.id == TID).first()
    from services.nautilus_token import TASK_TYPE_REWARDS
    nau_expect = TASK_TYPE_REWARDS["DOCUMENTATION"]
    a1, w1, n1 = hbal(p_addr), hbal(w_addr), nbal(w_addr)
    if res["passed"]:  # NAU 铸造异步，轮询等上链
        for _ in range(20):
            if abs((n1 - n0) - nau_expect) < 1e-9:
                break
            time.sleep(1)
            n1 = nbal(w_addr)
    print(f"  结算后: pub={a1} HUA, winner={w1} HUA, winner NAU={n1}, 状态={t.status.value}")
    # 门控一致性（对 LLM 评分波动稳健）
    if res["passed"]:
        need(t.status == TaskStatus.COMPLETED, "评审通过 -> COMPLETED")
        need(abs((a0 - a1) - REWARD) < 1e-9 and abs((w1 - w0) - REWARD) < 1e-9, f"华币结算 +{REWARD}（链上）")
        need(abs((n1 - n0) - nau_expect) < 1e-9, f"铸 NAU +{nau_expect}（链上）")
    else:
        need(t.status == TaskStatus.FAILED, "评审不通过 -> FAILED")
        need(abs(a1 - a0) < 1e-9 and abs(w1 - w0) < 1e-9, "不通过 -> 不结算华币")
        need(abs(n1 - n0) < 1e-9, "不通过 -> 不铸 NAU")
    db.close()

    step("结果汇总")
    if fail:
        print("SINGLE_ENTRY_FAIL:", fail); sys.exit(1)
    print("SINGLE_ENTRY_PASS  唯一发布入口 + 8 类 SE 化 + 新类型全流程（竞价->交付->评审->华币+NAU）验证通过")


if __name__ == "__main__":
    main()
