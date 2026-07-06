"""链上可信追踪端到端测试（方案 A-ii，私有链，无 mock）：

  alice 发布 CODE_DEVELOPMENT 任务（任务类型已全面 SE 化）
    -> 智能体抢单（先到先得；SE 任务不走自动撮合/自动执行器）—— ACCEPT 存证
    -> 中标智能体自行提交交付物 —— SUBMIT 存证
    -> 发布者 alice 完成 -> 3 专家评审 -> 链上华币结算 —— COMPLETE 存证
  每个动作由动作主体的托管钱包亲自向 TaskAuditTrail 合约存证（A-ii，msg.sender=主体）。
  存证走后台线程异步落链，E2E 轮询等 confirmed。

  校验：GET /api/audit/{id} 拉链上事件 vs 源表重算哈希，全部一致、主体全部验证；
        篡改 MySQL 的 task.result -> 校验接口报 SUBMIT 被篡改（verified=False）；恢复后重新一致。

动作经真实后端 HTTP API；存证/校验直连私链 TaskAuditTrail + 读 MySQL 核验。非业务 mock。

运行：  C:/nautilus-venv/Scripts/python.exe tests/e2e_audit_trail.py
前置：  后端在 127.0.0.1:8000、私链在 PRIVATE_RPC、AUDIT_TRAIL_ADDRESS 已配、verify_demo 管理员
"""
import os
import sys
import time

import requests
from jose import jwt
from web3 import Web3
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

API = os.getenv("E2E_API", "http://127.0.0.1:8000")
RPC = os.getenv("PRIVATE_RPC", "http://127.0.0.1:8545")
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALG = os.getenv("JWT_ALGORITHM", "HS256")
ONE = 10 ** 18

PUBLISHER = "alice"
OWNERS = ["e2e_dev_a", "e2e_dev_b"]
PASSWORD = "Test@12345"
ADMIN = "verify_demo"
REWARD_HUA = 100

HUA = Web3.to_checksum_address(os.getenv("HUA_TOKEN_ADDRESS"))
w3 = Web3(Web3.HTTPProvider(RPC))
BAL_ABI = [{"inputs": [{"name": "a", "type": "address"}], "name": "balanceOf",
            "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}]
hua = w3.eth.contract(address=HUA, abi=BAL_ABI)

fail = []


def tok(u): return jwt.encode({"sub": u, "exp": int(time.time()) + 3600}, JWT_SECRET, algorithm=JWT_ALG)
def auth(u): return {"Authorization": f"Bearer {tok(u)}"}
def bal(a): return hua.functions.balanceOf(Web3.to_checksum_address(a)).call() / ONE
def step(m): print(f"\n=== {m} ===")
def info(m): print(f"  - {m}")


def need(cond, m):
    print(f"  [{'OK' if cond else 'FAIL'}] {m}")
    if not cond:
        fail.append(m)


def db_all(q, params=None):
    from sqlalchemy import text
    from utils.database import engine
    with engine.connect() as cx:
        return cx.execute(text(q), params or {}).mappings().all()


def db_exec(q, params):
    from sqlalchemy import text
    from utils.database import engine
    with engine.begin() as cx:
        cx.execute(text(q), params)


def me(u):
    r = requests.get(f"{API}/api/wallets/me", headers=auth(u)); r.raise_for_status()
    return r.json()


def ensure_user(u):
    return requests.post(f"{API}/api/auth/register",
                         json={"username": u, "email": f"{u}@nautilus-corp.com", "password": PASSWORD}).status_code


def ensure_agent(u, name, specialties):
    requests.post(f"{API}/api/agents", headers=auth(u),
                  json={"name": name, "description": "E2E 审计智能体", "specialties": specialties})


def task_row(tid):
    r = db_all("SELECT status, agent, result FROM tasks WHERE id=:t", {"t": tid})
    return dict(r[0]) if r else {}


def recorded_actions(tid):
    # 已上链的动作（confirmed=已确认；sent=已广播待确认）；校验接口以链上事件为最终权威
    return {r["action"] for r in db_all(
        "SELECT action FROM audit_logs WHERE task_id=:t AND status IN ('confirmed','sent')", {"t": tid})}


def audit_api(tid):
    r = requests.get(f"{API}/api/audit/{tid}"); r.raise_for_status()
    return r.json()


def poll(fn, ok, timeout=120, interval=3):
    end = time.time() + timeout
    r = fn()
    while time.time() < end:
        if ok(r):
            return r
        time.sleep(interval)
        r = fn()
    return r


def main():
    step("0. 参与者准备（发布者 alice + 抢单智能体）+ 充值")
    for u in OWNERS:
        ensure_user(u)
        ensure_agent(u, f"AuditBot_{u}", ["Python", "FastAPI"])
    pub = me(PUBLISHER)
    pub_addr = pub["address"].lower()
    info(f"alice={pub_addr} hua={bal(pub_addr)}")
    if bal(pub_addr) < REWARD_HUA:
        rm = requests.post(f"{API}/api/wallets/mint", headers=auth(ADMIN),
                           json={"target": PUBLISHER, "amount": REWARD_HUA * 5})
        rm.raise_for_status()
        w3.eth.wait_for_transaction_receipt("0x" + rm.json()["tx_hash"].lstrip("0x"), timeout=30)
    need(bal(pub_addr) >= REWARD_HUA, f"alice 华币余额 {bal(pub_addr)} ≥ {REWARD_HUA}")

    # ---- 1. 发布 ----
    step("1. alice 发布 CODE 任务")
    r = requests.post(f"{API}/api/tasks", headers=auth(PUBLISHER), json={
        "description": "实现 reverse(s) 并附 pytest 单测", "input_data": "def reverse(s: str) -> str",
        "expected_output": "通过单测的实现", "reward": REWARD_HUA * ONE, "task_type": "CODE_DEVELOPMENT", "timeout": 86400})
    need(r.status_code == 201, f"创建任务 HTTP {r.status_code}")
    TID = r.json()["id"]
    info(f"task.id={TID}")

    # ---- 2. 抢单（先到先得；SE 任务不会被自动撮合抢先）----
    step("2. 智能体抢单 -> ACCEPT 存证")
    codes = {u: requests.post(f"{API}/api/tasks/{TID}/accept", headers=auth(u)).status_code for u in OWNERS}
    winners = [u for u, c in codes.items() if c == 200]
    need(len(winners) == 1, f"恰好一个智能体抢到任务 codes={codes}")
    WIN_USER = winners[0]
    t2 = task_row(TID)
    need(t2.get("status") == "ACCEPTED", f"任务已被抢: status={t2.get('status')}")
    win_addr = (t2.get("agent") or "").lower()
    info(f"中标智能体 owner={win_addr} (user={WIN_USER})")
    poll(lambda: recorded_actions(TID), lambda s: "ACCEPT" in s, timeout=60)
    need("ACCEPT" in recorded_actions(TID), "ACCEPT 存证 confirmed")

    # ---- 3. 中标智能体自行提交（SE 任务不走自动执行器）----
    step("3. 中标智能体提交交付物 -> SUBMIT 存证")
    deliverable = ("def reverse(s: str) -> str:\n    return s[::-1]\n\n"
                   "# pytest 单测\nassert reverse('abc') == 'cba'\nassert reverse('') == ''")
    rs = requests.post(f"{API}/api/tasks/{TID}/submit", headers=auth(WIN_USER), json={"result": deliverable})
    need(rs.status_code == 200, f"提交交付物 HTTP {rs.status_code}")
    need(task_row(TID).get("status") == "SUBMITTED", "任务状态 = SUBMITTED")
    poll(lambda: recorded_actions(TID), lambda s: "SUBMIT" in s, timeout=60)
    need("SUBMIT" in recorded_actions(TID), "SUBMIT 存证 confirmed")

    # ---- 4. 完成（3 专家评审门控）+ 结算 ----
    step("4. alice 完成 -> 3 专家评审 -> COMPLETE 存证 + 华币结算")
    a0, w0 = bal(pub_addr), bal(win_addr)
    rc = requests.post(f"{API}/api/tasks/{TID}/complete", headers=auth(PUBLISHER), timeout=180)
    need(rc.status_code == 200, f"完成 HTTP {rc.status_code} {'' if rc.status_code==200 else rc.text[:160]}")
    poll(lambda: recorded_actions(TID), lambda s: "COMPLETE" in s, timeout=90)
    need("COMPLETE" in recorded_actions(TID), "COMPLETE 存证已上链（confirmed/sent）")
    a1, w1 = bal(pub_addr), bal(win_addr)
    need(abs((a0 - a1) - REWARD_HUA) < 1e-9, f"alice 扣 {REWARD_HUA} 华币 ({a0}->{a1})")
    need(abs((w1 - w0) - REWARD_HUA) < 1e-9, f"中标者收 {REWARD_HUA} 华币 ({w0}->{w1})")

    # ---- 5. 校验接口：链上事件 vs 源表重算 + 主体核验 ----
    step("5. GET /api/audit/{id}：全链条一致性 + 主体核验")
    v = poll(lambda: audit_api(TID), lambda x: x["onchain_records"] >= 4, timeout=60)
    info(f"onchain_records={v['onchain_records']} verified={v['verified']} tampered={v['tampered_actions']}")
    acts = {rec["action"] for rec in v["records"]}
    need({"PUBLISH", "ACCEPT", "SUBMIT", "COMPLETE"}.issubset(acts), f"四类动作均已上链: {sorted(acts)}")
    need(v["verified"] is True, "整体 verified=True（无篡改）")
    need(all(rec["content_match"] for rec in v["records"]), "每条 content_match=True")
    need(all(rec["actor_verified"] for rec in v["records"]), "每条 actor_verified=True（主体不可抵赖）")
    ba = {rec["action"]: rec for rec in v["records"]}
    need(ba["PUBLISH"]["actor"] == pub_addr, "PUBLISH 主体=发布者钱包")
    need(ba["SUBMIT"]["actor"] == win_addr, "SUBMIT 主体=中标智能体 owner")
    need(ba["COMPLETE"]["actor"] == pub_addr, "COMPLETE 主体=发布者钱包")

    # ---- 6. 防篡改 ----
    step("6. 篡改 MySQL 中的 task.result -> 校验接口检测")
    orig = task_row(TID)["result"]
    db_exec("UPDATE tasks SET result=:r WHERE id=:t", {"r": "TAMPERED-" + (orig or ""), "t": TID})
    v2 = audit_api(TID)
    info(f"篡改后: verified={v2['verified']} tampered={v2['tampered_actions']}")
    need(v2["verified"] is False, "篡改后整体 verified=False")
    need("SUBMIT" in v2["tampered_actions"], "SUBMIT 被标记为篡改（重算哈希 ≠ 链上）")
    need(rec_ok(v2, "PUBLISH") and rec_ok(v2, "COMPLETE"), "未篡改字段的动作（PUBLISH/COMPLETE）仍一致")

    # ---- 7. 恢复 ----
    step("7. 恢复原值 -> 校验重新一致")
    db_exec("UPDATE tasks SET result=:r WHERE id=:t", {"r": orig, "t": TID})
    need(audit_api(TID)["verified"] is True, "恢复后整体 verified=True")

    step("结果汇总")
    if fail:
        print(f"  AUDIT_TRAIL_FAIL  失败 {len(fail)} 项：")
        for f in fail:
            print(f"    - {f}")
        sys.exit(1)
    print("  AUDIT_TRAIL_PASS  链上可信追踪端到端验证通过"
          "（发布/抢单/执行提交/完成全存证 + 主体不可抵赖 + 防篡改检测）")
    sys.exit(0)


def rec_ok(v, action):
    return any(rec["action"] == action and rec["content_match"] for rec in v["records"])


if __name__ == "__main__":
    main()
