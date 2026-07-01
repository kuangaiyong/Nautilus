"""链上可信追踪 SE 专项 E2E（AWARD 派单 + REVIEW 专家评审，方案 A-ii，无 mock）：

  se_pub 发布 SE 任务（CODE_DEVELOPMENT）
    -> 自主智能体真实竞价（auto_bid）-> 择优派单（award_se_task）—— AWARD 存证
    -> 中标智能体提交结果
    -> 3 位专家智能体真实 LLM 评审（run_expert_reviews）—— REVIEW 存证 ×3
  每条评审由评审专家的托管钱包亲自向 TaskAuditTrail 存证（A-ii，msg.sender=专家）。

  校验：GET /api/audit/{id} 拉链上事件 vs 源表(TaskReview)重算哈希，AWARD + 每条 REVIEW
        一致且主体验证；篡改某条 review 的 score -> 该 REVIEW 报被篡改（verified=False）。

真实 DB + 真实链 + 真实 LLM 网关。运行：
  C:/nautilus-venv/Scripts/python.exe tests/e2e_audit_trail_se.py
前置：后端在 127.0.0.1:8000、私链、AUDIT_TRAIL_ADDRESS 已配、LLM 网关可用
"""
import os
import sys
import time

import requests
from jose import jwt
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

import services.audit_trail as at  # noqa: E402  （末尾 shutdown 后台线程池）
import models.agent_survival  # noqa: E402,F401
from utils.database import SessionLocal  # noqa: E402
from models.database import Task, TaskType, TaskStatus, TaskReview, Agent  # noqa: E402

API = os.getenv("E2E_API", "http://127.0.0.1:8000")
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALG = os.getenv("JWT_ALGORITHM", "HS256")

PUB = "se_audit_pub"
BIDDERS = [f"se_audit_{i}" for i in range(1, 6)]  # 5 个竞价/评审智能体
PASSWORD = "Test@12345"

fail = []


def tok(u): return jwt.encode({"sub": u, "exp": int(time.time()) + 3600}, JWT_SECRET, algorithm=JWT_ALG)
def auth(u): return {"Authorization": f"Bearer {tok(u)}"}
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
    return r.json()["address"].lower()


def ensure_user(u):
    requests.post(f"{API}/api/auth/register",
                  json={"username": u, "email": f"{u}@nautilus-corp.com", "password": PASSWORD})


def ensure_agent(u):
    requests.post(f"{API}/api/agents", headers=auth(u),
                  json={"name": f"SEAudit_{u}", "description": "SE 审计智能体",
                        "specialties": ["Python", "CODE_DEVELOPMENT", "FastAPI"]})


def audit_api(tid):
    r = requests.get(f"{API}/api/audit/{tid}"); r.raise_for_status()
    return r.json()


def audit_count(tid, action):
    return db_all("SELECT COUNT(*) c FROM audit_logs WHERE task_id=:t AND action=:a "
                  "AND status IN ('confirmed','sent')", {"t": tid, "a": action})[0]["c"]


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
    db = SessionLocal()
    tid = None
    try:
        step("0. 准备发布者 + 5 个自主竞价/评审智能体（均有托管钱包，存证需 actor 私钥）")
        ensure_user(PUB)
        pub_addr = me(PUB)
        for u in BIDDERS:
            ensure_user(u)
            ensure_agent(u)
        bidder_addrs = {u: me(u) for u in BIDDERS}
        # 开启自主竞价 + 抬高声誉，确保这批（有托管钱包的）智能体被优先选为竞价者/评审专家，
        # 从而存证时 actor 私钥可解密（select_reviewers 从全库按 专长+声誉 降序选）。
        for _a in bidder_addrs.values():
            db_exec("UPDATE agents SET autonomy_enabled=1, reputation_score=999 WHERE lower(owner)=:a", {"a": _a})
        info(f"publisher={pub_addr}  bidders={list(bidder_addrs.values())}")

        step("1. se_pub 发布 SE 任务（HTTP）-> PUBLISH 存证")
        r = requests.post(f"{API}/api/tasks", headers=auth(PUB), json={
            "description": "实现 add(a,b) 返回两数之和，并附 assert 单测",
            "input_data": "def add(a, b): ...", "expected_output": "通过断言的实现",
            "reward": 10 ** 18, "task_type": "CODE_DEVELOPMENT", "timeout": 86400})
        need(r.status_code == 201, f"发布 SE 任务 HTTP {r.status_code}")
        tid = r.json()["id"]
        info(f"task.id={tid}")

        step("2. 自主竞价 + 择优派单 -> AWARD 存证")
        from services.se_pouw_flow import auto_bid_open_se_tasks, award_se_task, run_expert_reviews
        db.expire_all()  # 让 ORM 读到上面直连提交的 autonomy/reputation 最新值
        n_bid = auto_bid_open_se_tasks(db)
        info(f"auto_bid 新增投标 {n_bid}")
        win_id = award_se_task(db, tid)
        need(bool(win_id), f"择优派单成功 winner_agent_id={win_id}")
        db.expire_all()
        trow = db.query(Task).filter(Task.id == tid).first()
        win_addr = (trow.agent or "").lower()
        info(f"中标智能体 owner={win_addr}")
        poll(lambda: audit_count(tid, "AWARD"), lambda c: c >= 1, timeout=60)
        need(audit_count(tid, "AWARD") >= 1, "AWARD 存证已上链")

        step("3. 中标智能体提交结果（SE 不走自动执行器）")
        trow.status = TaskStatus.SUBMITTED
        trow.result = "def add(a, b):\n    return a + b\n\nassert add(1, 2) == 3\n"
        db.commit()
        info("已置 SUBMITTED + result")

        step("4. 3 专家真实 LLM 评审 -> REVIEW 存证 ×3")
        res = run_expert_reviews(db, tid)
        info(f"评审结果 complete={res.get('complete')} passed={res.get('passed')} n={res.get('n')} avg={res.get('avg')}")
        need(res.get("complete") is True and res.get("n") >= 3,
             f"评审完成且有效评审≥3（LLM 网关须可用）：n={res.get('n')}")
        poll(lambda: audit_count(tid, "REVIEW"), lambda c: c >= 3, timeout=90)
        need(audit_count(tid, "REVIEW") >= 3, "REVIEW 存证 ×3 已上链")

        step("5. GET /api/audit/{id}：AWARD + REVIEW 一致性 + 主体核验")
        v = poll(lambda: audit_api(tid), lambda x: x["onchain_records"] >= 4, timeout=90)
        info(f"onchain_records={v['onchain_records']} verified={v['verified']} tampered={v['tampered_actions']}")
        recs = v["records"]
        review_recs = [x for x in recs if x["action"] == "REVIEW"]
        award_recs = [x for x in recs if x["action"] == "AWARD"]
        need(len(award_recs) >= 1, f"AWARD 已上链: {len(award_recs)}")
        need(len(review_recs) >= 3, f"REVIEW 已上链 ×{len(review_recs)}")
        need(all(x["content_match"] for x in review_recs), "每条 REVIEW content_match=True")
        need(all(x["actor_verified"] for x in review_recs), "每条 REVIEW actor_verified=True（评审专家不可抵赖）")
        need(award_recs and award_recs[0]["actor"] == win_addr, "AWARD 主体=中标智能体 owner")
        need(v["verified"] is True, "整体 verified=True")

        step("6. 篡改某条 review 的 score -> 校验接口检测该 REVIEW 被篡改")
        one = db_all("SELECT id, score FROM task_reviews WHERE task_id=:t ORDER BY id LIMIT 1", {"t": tid})[0]
        db_exec("UPDATE task_reviews SET score=:s WHERE id=:i",
                {"s": (1.0 if float(one["score"]) != 1.0 else 2.0), "i": one["id"]})
        v2 = audit_api(tid)
        info(f"篡改后: verified={v2['verified']} tampered={v2['tampered_actions']}")
        need(v2["verified"] is False, "篡改评审后整体 verified=False")
        need("REVIEW" in v2["tampered_actions"], "被改评审的 REVIEW 标记为篡改")
        # 恢复
        db_exec("UPDATE task_reviews SET score=:s WHERE id=:i", {"s": one["score"], "i": one["id"]})
        need(audit_api(tid)["verified"] is True, "恢复后整体 verified=True")

    finally:
        # 等后台存证线程收尾，再清理本次测试的 DB 行（链上记录不可删，无害）
        try:
            at._bg_executor.shutdown(wait=True)
        except Exception:
            pass
        if tid is not None:
            db.query(TaskReview).filter(TaskReview.task_id == tid).delete()
            from models.database import DmasTaskBid, AuditLog
            db.query(DmasTaskBid).filter(DmasTaskBid.task_id == tid).delete()
            db.query(AuditLog).filter(AuditLog.task_id == tid).delete()
            db.query(Task).filter(Task.id == tid).delete()
            db.commit()
        db.close()

    step("结果汇总")
    if fail:
        print(f"  SE_AUDIT_FAIL  失败 {len(fail)} 项：")
        for f in fail:
            print(f"    - {f}")
        sys.exit(1)
    print("  SE_AUDIT_PASS  SE 可信追踪端到端通过（竞价派单 AWARD + 3 专家 REVIEW 全存证 + 评审防篡改）")
    sys.exit(0)


if __name__ == "__main__":
    main()
