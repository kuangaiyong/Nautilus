"""
端到端测试 - Tasks API

使用内存SQLite数据库测试所有Tasks API端点
"""
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.database import Base, User, Task, Agent, TaskType, TaskStatus
from utils.database import get_db
from api.tasks import router as tasks_router
from api.auth import router as auth_router
from utils.auth import hash_password


# 创建测试引擎
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """覆盖数据库依赖"""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="module")
def setup_database():
    """设置测试数据库"""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(setup_database):
    """创建测试客户端"""
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(tasks_router, prefix="/api/tasks")
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c

    # 清理数据库
    db = TestingSessionLocal()
    try:
        from models.agent_survival import AgentSurvival
        db.query(Task).delete()
        db.query(AgentSurvival).delete()
        db.query(Agent).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def auth_token(client):
    """创建用户并返回认证token"""
    # 尝试注册新用户
    response = client.post("/api/auth/register", json={
        "username": "testuser",
        "email": "test@example.com",
        "password": "password123",
        "wallet_address": "0x1234567890123456789012345678901234567890"
    })

    # 如果注册成功，返回token
    if response.status_code == 201:
        return response.json()["access_token"]

    # 如果用户已存在或遇到速率限制，尝试登录
    login_response = client.post("/api/auth/login", json={
        "username": "testuser",
        "password": "password123"
    })

    # 如果登录成功，返回token
    if login_response.status_code == 200:
        return login_response.json()["access_token"]

    # 如果登录也失败（可能是速率限制），直接创建token
    from utils.auth import create_access_token
    return create_access_token(data={"sub": "testuser"})


@pytest.fixture
def agent_with_token(client):
    """创建agent用户并返回token"""
    # 注册用户
    response = client.post("/api/auth/register", json={
        "username": "agentuser",
        "email": "agent@example.com",
        "password": "password123",
        "wallet_address": "0x1234567890123456789012345678901234567891"
    })
    token = response.json()["access_token"]

    # 创建agent
    db = TestingSessionLocal()
    user = db.query(User).filter(User.username == "agentuser").first()
    wallet_address = user.wallet_address  # 在关闭会话前获取值

    agent = Agent(
        agent_id=1,
        owner=wallet_address,
        name="TestAgent",
        reputation=100,
        completed_tasks=0,
        failed_tasks=0,
        total_earnings=0
    )
    db.add(agent)
    db.commit()
    db.close()

    return token, wallet_address


# ============================================================================
# Tasks API E2E Tests
# ============================================================================

class TestTasksE2E:
    """Tasks API端到端测试"""

    def test_create_task_success(self, client, auth_token):
        """测试成功创建任务"""
        response = client.post(
            "/api/tasks",
            json={
                "description": "Test task",
                "reward": 1000,
                "task_type": "CODE_DEVELOPMENT",
                "timeout": 3600
            },
            headers={"Authorization": f"Bearer {auth_token}"}
        )

        assert response.status_code == 201
        data = response.json()
        assert data["description"] == "Test task"
        assert data["reward"] == 1000
        assert data["status"] == "Open"

    def test_create_task_unauthorized(self, client):
        """测试未授权创建任务"""
        response = client.post(
            "/api/tasks",
            json={
                "description": "Test task",
                "reward": 1000,
                "task_type": "CODE_DEVELOPMENT",
                "timeout": 3600
            }
        )
        assert response.status_code == 401

    def test_list_tasks(self, client, auth_token):
        """测试列出任务"""
        # 创建几个任务
        for i in range(3):
            client.post(
                "/api/tasks",
                json={
                    "description": f"Task {i}",
                    "reward": 1000 + i * 100,
                    "task_type": "CODE_DEVELOPMENT",
                    "timeout": 3600
                },
                headers={"Authorization": f"Bearer {auth_token}"}
            )

        # 列出任务
        response = client.get("/api/tasks")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 3

    def test_list_tasks_with_filters(self, client, auth_token):
        """测试带过滤器的任务列表"""
        # 创建不同类型的任务
        client.post(
            "/api/tasks",
            json={
                "description": "Code task",
                "reward": 1000,
                "task_type": "CODE_DEVELOPMENT",
                "timeout": 3600
            },
            headers={"Authorization": f"Bearer {auth_token}"}
        )

        client.post(
            "/api/tasks",
            json={
                "description": "Data task",
                "reward": 2000,
                "task_type": "DOCUMENTATION",
                "timeout": 3600
            },
            headers={"Authorization": f"Bearer {auth_token}"}
        )

        # 过滤 CODE_DEVELOPMENT 类型
        response = client.get("/api/tasks?task_type=CODE_DEVELOPMENT")
        assert response.status_code == 200
        data = response.json()
        assert all(task["task_type"] == "CODE_DEVELOPMENT" for task in data)

    def test_list_tasks_pagination(self, client, auth_token):
        """回归测试：skip 分页必须返回不同页。

        复现 bug：list_tasks 端点声明了 skip 参数，但 get_tasks_cached 从未
        应用 offset，导致任何 skip 都返回同一批「前 limit 条」，前端翻页失效。
        修复前 page1 与 page2 完全相同，下面的 isdisjoint 断言必然失败。
        """
        for i in range(7):
            resp = client.post(
                "/api/tasks",
                json={
                    "description": f"Pagination task {i}",
                    "reward": 1000 + i,
                    "task_type": "CODE_DEVELOPMENT",
                    "timeout": 3600
                },
                headers={"Authorization": f"Bearer {auth_token}"}
            )
            assert resp.status_code == 201

        page1 = client.get("/api/tasks?skip=0&limit=3")
        page2 = client.get("/api/tasks?skip=3&limit=3")
        assert page1.status_code == 200
        assert page2.status_code == 200
        ids1 = [t["id"] for t in page1.json()]
        ids2 = [t["id"] for t in page2.json()]

        assert len(ids1) <= 3
        assert len(ids2) <= 3
        # 两页不能有重叠记录（bug 修复前二者完全相同，此断言必失败）
        assert set(ids1).isdisjoint(set(ids2)), \
            f"翻页返回重叠记录，分页未生效: page1={ids1} page2={ids2}"

    def test_me_stats_only_counts_own_data(self, client, auth_token):
        """回归：/api/auth/me/stats 只统计当前用户本人的任务，且收入/信誉来自
        本人 Agent（无 Agent 时为 0 / None）。

        复现 bug：个人中心此前 fetch 无过滤的 /api/tasks（=全平台）当作个人统计，
        累计收入/支出硬编码为 0、信誉用 completed/failed 在前端乱算。
        """
        # auth_token fixture 已注册 testuser；直接按其钱包地址造数据（避免依赖建单细节）
        db = TestingSessionLocal()
        try:
            me = db.query(User).filter(User.username == "testuser").first()
            wallet = me.wallet_address
            for i in range(2):
                db.add(Task(
                    task_id=f"mine_{i}", publisher=wallet,
                    description=f"my task {i}", reward=1000,
                    task_type=TaskType.CODE_DEVELOPMENT,
                    status=TaskStatus.OPEN, timeout=3600,
                ))
            # 别人发布的已完成任务，绝不能被算进本人统计
            db.add(Task(
                task_id="other_1", publisher="0x0000000000000000000000000000000000000abc",
                description="not mine", reward=999,
                task_type=TaskType.CODE_DEVELOPMENT,
                status=TaskStatus.COMPLETED, timeout=3600,
            ))
            db.commit()
        finally:
            db.close()

        resp = client.get(
            "/api/auth/me/stats",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_tasks"] == 2                  # 别人那条不计入
        assert data["completed_tasks"] == 0              # 别人的 COMPLETED 不算本人
        assert len(data["recent_tasks"]) == 2
        assert data["total_earnings"] == "0"             # 无 Agent
        assert data["reputation"] is None                # 无 Agent
        assert all(t["description"].startswith("my task") for t in data["recent_tasks"])

    def test_me_stats_counts_agent_executed_tasks_and_survival_income(self, client):
        """回归：智能体所有者的个人中心统计。

        复现 bug（用户 u_test_case_design_senior 实测）：
        1. 累计收入读 Agent.total_earnings —— 结算路径从不写它，且它是 BigInteger
           （上限 ≈9.22 华币），装不下 10 华币的 wei。真值在 AgentSurvival.total_income。
        2. 任务口径只算「我发布的」，导致名下 Agent 承接并完成的任务在
           「任务总数 / 已完成 / 最近任务」里全为 0。
        """
        from utils.auth import create_access_token
        from models.agent_survival import AgentSurvival

        wallet = "0x00000000000000000000000000000000000ab101"
        db = TestingSessionLocal()
        try:
            db.add(User(
                username="owner1", email="owner1@example.com",
                hashed_password=hash_password("password123"), wallet_address=wallet,
            ))
            db.add(Agent(
                agent_id=9901, name="测试用例设计专家", owner=wallet,
                total_earnings=0, reputation_score=72.5,
            ))
            # 10 华币收入：超过 int64（≈9.22 华币），只有 WeiInt 列存得下
            db.add(AgentSurvival(
                agent_id=9901, total_income=10 * 10**18, total_cost=2 * 10**17,
            ))
            # 别人发布、由本人 Agent 承接并完成的任务
            db.add(Task(
                task_id="exec_1", publisher="0x00000000000000000000000000000000000abcde",
                agent=wallet, description="executed by my agent", reward=5 * 10**18,
                task_type=TaskType.TEST_CASE_DESIGN, status=TaskStatus.COMPLETED, timeout=3600,
            ))
            db.commit()
        finally:
            db.close()

        token = create_access_token(data={"sub": "owner1"})
        resp = client.get("/api/auth/me/stats", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()["data"]

        assert data["total_tasks"] == 1          # 修复前为 0（只算 publisher）
        assert data["completed_tasks"] == 1      # 修复前为 0
        assert len(data["recent_tasks"]) == 1    # 修复前为空
        # 收入取自 AgentSurvival.total_income，而非恒为 0 的 Agent.total_earnings
        assert data["total_earnings"] == str(10 * 10**18)
        # 承接的任务不产生支出，累计支出只算本人发布且已完成的任务
        assert data["total_spent"] == "0"
        assert data["reputation"] == 72.5

    def test_get_task_by_id(self, client, auth_token):
        """测试获取单个任务"""
        # 创建任务
        create_response = client.post(
            "/api/tasks",
            json={
                "description": "Test task",
                "reward": 1000,
                "task_type": "CODE_DEVELOPMENT",
                "timeout": 3600
            },
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        task_data = create_response.json()
        task_db_id = task_data["id"]  # 使用数据库ID

        # 获取任务
        response = client.get(f"/api/tasks/{task_db_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == task_db_id

    def test_get_task_not_found(self, client):
        """测试获取不存在的任务"""
        response = client.get("/api/tasks/99999")
        assert response.status_code == 404

    def test_accept_task_success(self, client, agent_with_token):
        """测试成功接受任务"""
        agent_token, agent_wallet = agent_with_token

        # 创建任务
        db = TestingSessionLocal()
        task = Task(
            task_id="0x1234",
            description="Test task",
            reward=1000,
            task_type=TaskType.CODE_DEVELOPMENT,
            status=TaskStatus.OPEN,
            publisher="0x1234567890123456789012345678901234567890",
            timeout=3600
        )
        db.add(task)
        db.commit()
        task_db_id = task.id
        db.close()

        # 接受任务
        response = client.post(
            f"/api/tasks/{task_db_id}/accept",
            headers={"Authorization": f"Bearer {agent_token}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "Accepted"

    def test_accept_task_not_found(self, client, agent_with_token):
        """测试接受不存在的任务"""
        agent_token, _ = agent_with_token
        response = client.post(
            "/api/tasks/99999/accept",
            headers={"Authorization": f"Bearer {agent_token}"}
        )
        assert response.status_code == 404

    def test_accept_task_not_open(self, client, agent_with_token):
        """测试接受非Open状态的任务"""
        agent_token, agent_wallet = agent_with_token

        # 创建已接受的任务
        db = TestingSessionLocal()
        task = Task(
            task_id="0x1235",
            description="Test task",
            reward=1000,
            task_type=TaskType.CODE_DEVELOPMENT,
            status=TaskStatus.ACCEPTED,
            publisher="0x1234567890123456789012345678901234567890",
            agent=agent_wallet,
            timeout=3600
        )
        db.add(task)
        db.commit()
        task_db_id = task.id
        db.close()

        # 尝试再次接受
        response = client.post(
            f"/api/tasks/{task_db_id}/accept",
            headers={"Authorization": f"Bearer {agent_token}"}
        )
        assert response.status_code == 400

    def test_submit_task_success(self, client, agent_with_token):
        """测试成功提交任务"""
        agent_token, agent_wallet = agent_with_token

        # 创建已接受的任务
        db = TestingSessionLocal()
        task = Task(
            task_id="0x1236",
            description="Test task",
            reward=1000,
            task_type=TaskType.CODE_DEVELOPMENT,
            status=TaskStatus.ACCEPTED,
            publisher="0x1234567890123456789012345678901234567890",
            agent=agent_wallet,
            timeout=3600
        )
        db.add(task)
        db.commit()
        task_db_id = task.id
        db.close()

        # 提交任务
        response = client.post(
            f"/api/tasks/{task_db_id}/submit",
            json={"result": "Task completed successfully"},
            headers={"Authorization": f"Bearer {agent_token}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "Submitted"

    def test_submit_task_not_accepted(self, client, agent_with_token):
        """测试提交未接受的任务"""
        agent_token, _ = agent_with_token

        # 创建Open状态的任务
        db = TestingSessionLocal()
        task = Task(
            task_id="0x1237",
            description="Test task",
            reward=1000,
            task_type=TaskType.CODE_DEVELOPMENT,
            status=TaskStatus.OPEN,
            publisher="0x1234567890123456789012345678901234567890",
            timeout=3600
        )
        db.add(task)
        db.commit()
        task_db_id = task.id
        db.close()

        # 尝试提交
        response = client.post(
            f"/api/tasks/{task_db_id}/submit",
            json={"result": "Task completed"},
            headers={"Authorization": f"Bearer {agent_token}"}
        )
        assert response.status_code == 400

    def test_submit_task_wrong_agent(self, client, agent_with_token):
        """测试错误的agent提交任务"""
        agent_token, _ = agent_with_token

        # 创建分配给其他agent的任务
        db = TestingSessionLocal()
        task = Task(
            task_id="0x1238",
            description="Test task",
            reward=1000,
            task_type=TaskType.CODE_DEVELOPMENT,
            status=TaskStatus.ACCEPTED,
            publisher="0x1234567890123456789012345678901234567890",
            agent="0x9999999999999999999999999999999999999999",
            timeout=3600
        )
        db.add(task)
        db.commit()
        task_db_id = task.id
        db.close()

        # 尝试提交
        response = client.post(
            f"/api/tasks/{task_db_id}/submit",
            json={"result": "Task completed"},
            headers={"Authorization": f"Bearer {agent_token}"}
        )
        assert response.status_code == 403

    def test_dispute_task_success(self, client, agent_with_token):
        """测试成功提出争议"""
        agent_token, agent_wallet = agent_with_token

        # 创建已失败的任务
        db = TestingSessionLocal()
        task = Task(
            task_id="0x1241",
            description="Test task",
            reward=1000,
            task_type=TaskType.CODE_DEVELOPMENT,
            status=TaskStatus.FAILED,
            publisher="0x1234567890123456789012345678901234567890",
            agent=agent_wallet,
            timeout=3600
        )
        db.add(task)
        db.commit()
        task_db_id = task.id
        db.close()

        # 提出争议
        response = client.post(
            f"/api/tasks/{task_db_id}/dispute",
            json={"reason": "Task was completed correctly"},
            headers={"Authorization": f"Bearer {agent_token}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "Disputed"
