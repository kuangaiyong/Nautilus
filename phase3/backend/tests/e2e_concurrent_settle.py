"""E2E 并发结算抢占回归：两个独立 DB 连接经 Barrier 同时对同一 SUBMITTED 任务调用
complete_task，验证 with_for_update 行锁抢占 → 恰一个成功结算(OK)、另一个 4xx 被拒，
不重复 pay_hua。防的是「cron 自动评审结算」与「发布者手动『完成』」并发导致的华币双扣
（曾使任务 #67 被 pay_hua 两次，链上出现两笔同额转账）。

需真实后端环境（MySQL 行锁 + 真实链结算），直接运行，不走 pytest —— SQLite 无行锁、
无法复现跨连接竞争：
    C:/nautilus-venv/Scripts/python.exe tests/e2e_concurrent_settle.py

断言不看总余额 Δ（后台 cron 并行清理其他任务会污染余额），只看确定性事实：
一个线程 OK、另一个 4xx，且任务最终 COMPLETED 且有唯一 complete_tx。
"""
import sys, os, asyncio, threading, time
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BACKEND); sys.path.insert(0, BACKEND)
from dotenv import load_dotenv; load_dotenv(os.path.join(BACKEND, ".env"))
import main  # noqa
from datetime import datetime, timezone
from utils.database import SessionLocal
from models.database import Task, TaskReview, TaskStatus, TaskType, User, Agent
from api.tasks import complete_task
from fastapi import HTTPException

setup = SessionLocal()
pub = setup.query(User).filter(User.username == "alice").first()
executor = setup.query(Agent).filter(Agent.owner.isnot(None),
                                     Agent.owner != pub.wallet_address).first()
task = Task(task_id=f"conc_{int(time.time())}", publisher=pub.wallet_address,
            description="并发结算抢占回归任务", reward=2 * 10**18,
            task_type=TaskType.TEST_CASE_DESIGN, status=TaskStatus.SUBMITTED,
            agent=executor.owner, result="TC-01 正常 TC-02 异常 TC-03 边界",
            timeout=3600, created_at=datetime.now(timezone.utc),
            submitted_at=datetime.now(timezone.utc))
setup.add(task); setup.commit(); setup.refresh(task)
tid = task.id
# 预置 3 条通过评审，跳过评审阶段、聚焦 pay 抢占
for a in setup.query(Agent).filter(Agent.owner.isnot(None),
                                   Agent.owner != pub.wallet_address,
                                   Agent.owner != executor.owner).limit(3).all():
    setup.add(TaskReview(task_id=tid, reviewer_agent_id=a.agent_id, score=4.0,
                         correctness=4.0, completeness=4.0, standards=4.0,
                         comment="ok", created_at=datetime.utcnow()))
setup.commit()
setup.close()

results = {}
barrier = threading.Barrier(2)


def worker(label):
    barrier.wait()  # 两线程同时进入 complete_task
    db = SessionLocal()
    u = db.query(User).filter(User.username == "alice").first()
    try:
        asyncio.run(complete_task(task_id=tid, current_user=u, db=db))
        results[label] = "OK"
    except HTTPException as e:
        results[label] = f"HTTP{e.status_code}"
    except Exception as e:
        results[label] = f"ERR:{type(e).__name__}:{str(e)[:70]}"
    finally:
        db.close()


t1 = threading.Thread(target=worker, args=("A",))
t2 = threading.Thread(target=worker, args=("B",))
t1.start(); t2.start(); t1.join(); t2.join()
print("concurrent results =", results)

db2 = SessionLocal()
final = db2.query(Task).filter(Task.id == tid).first()
completed = final.status == TaskStatus.COMPLETED and bool(final.blockchain_complete_tx)
db2.close()

vals = sorted(results.values())
one_ok = len([v for v in vals if v == "OK"]) == 1
one_4xx = len([v for v in vals if v.startswith("HTTP4")]) == 1
ok = one_ok and one_4xx and completed
print(f"task={tid} completed={completed} complete_tx={(final.blockchain_complete_tx or '')[:20]}")
print("CONCURRENT_SETTLE_PASS" if ok else f"CONCURRENT_SETTLE_FAIL results={results} completed={completed}")
sys.exit(0 if ok else 1)
