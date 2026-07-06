"""
数据库模型单元测试

为 models/database.py 添加单元测试
"""
import pytest
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Model Tests
# ============================================================================

class TestDatabaseModels:
    """测试数据库模型"""

    def test_task_type_enum(self):
        """测试TaskType枚举（软件工程全生命周期 8 类，旧通用类型已下线）"""
        from models.database import TaskType

        assert TaskType.REQUIREMENT_ANALYSIS.value == "REQUIREMENT_ANALYSIS"
        assert TaskType.ARCHITECTURE_DESIGN.value == "ARCHITECTURE_DESIGN"
        assert TaskType.CODE_DEVELOPMENT.value == "CODE_DEVELOPMENT"
        assert TaskType.CODE_REVIEW.value == "CODE_REVIEW"
        assert TaskType.TEST_CASE_DESIGN.value == "TEST_CASE_DESIGN"
        assert TaskType.TEST_AUTOMATION.value == "TEST_AUTOMATION"
        assert TaskType.DEPLOYMENT_OPS.value == "DEPLOYMENT_OPS"
        assert TaskType.DOCUMENTATION.value == "DOCUMENTATION"
        # 恰好 8 类，且不含任何旧通用类型
        assert len(TaskType) == 8
        assert not {"CODE", "DATA", "COMPUTE", "RESEARCH", "DESIGN", "WRITING", "OTHER"} & {t.name for t in TaskType}

    def test_task_status_enum(self):
        """测试TaskStatus枚举"""
        from models.database import TaskStatus

        assert TaskStatus.OPEN.value == "Open"
        assert TaskStatus.ACCEPTED.value == "Accepted"
        assert TaskStatus.SUBMITTED.value == "Submitted"
        assert TaskStatus.VERIFIED.value == "Verified"
        assert TaskStatus.COMPLETED.value == "Completed"
        assert TaskStatus.FAILED.value == "Failed"
        assert TaskStatus.DISPUTED.value == "Disputed"

    def test_user_model(self):
        """测试User模型"""
        from models.database import User

        user = User(
            username="testuser",
            email="test@example.com",
            hashed_password="hashed_password",
            wallet_address="0x1234567890123456789012345678901234567890"
        )

        assert user.username == "testuser"
        assert user.email == "test@example.com"
        assert user.wallet_address == "0x1234567890123456789012345678901234567890"

    def test_agent_model(self):
        """测试Agent模型"""
        from models.database import Agent

        agent = Agent(
            agent_id=1,
            owner="0x1234567890123456789012345678901234567890",
            name="TestAgent",
            description="Test description",
            specialties="test,demo",
            reputation=100,
            completed_tasks=10,
            failed_tasks=1,
            total_earnings=1000
        )

        assert agent.name == "TestAgent"
        assert agent.reputation == 100
        assert agent.completed_tasks == 10
        assert agent.failed_tasks == 1
        assert agent.total_earnings == 1000

    def test_task_model(self):
        """测试Task模型"""
        from models.database import Task, TaskStatus, TaskType

        task = Task(
            task_id="task-1",
            description="Test description",
            reward=100,
            task_type=TaskType.CODE_DEVELOPMENT,
            status=TaskStatus.OPEN,
            publisher="0x123",
            agent=None,
            timeout=3600
        )

        assert task.task_id == "task-1"
        assert task.description == "Test description"
        assert task.reward == 100
        assert task.task_type == TaskType.CODE_DEVELOPMENT
        assert task.status == TaskStatus.OPEN
        assert task.publisher == "0x123"
        assert task.timeout == 3600

    def test_reward_model(self):
        """测试Reward模型"""
        from models.database import Reward

        reward = Reward(
            task_id="task-1",
            agent="0x123",
            amount=100,
            status="Distributed"
        )

        assert reward.task_id == "task-1"
        assert reward.agent == "0x123"
        assert reward.amount == 100
        assert reward.status == "Distributed"

    def test_api_key_model(self):
        """测试APIKey模型"""
        from models.database import APIKey

        api_key = APIKey(
            key="test-api-key",
            agent_id=1,
            name="Test Key"
        )

        assert api_key.key == "test-api-key"
        assert api_key.agent_id == 1
        assert api_key.name == "Test Key"

    def test_verification_log_model(self):
        """测试VerificationLog模型"""
        from models.database import VerificationLog

        log = VerificationLog(
            task_id="task-1",
            verification_method="automatic",
            is_valid=True,
            logs="Test details"
        )

        assert log.task_id == "task-1"
        assert log.verification_method == "automatic"
        assert log.is_valid is True
        assert log.logs == "Test details"

    def test_model_relationships(self):
        """测试模型关系"""
        from models.database import Task, Agent, User, Reward

        # 验证关系存在
        assert hasattr(Task, '__tablename__')
        assert hasattr(Agent, '__tablename__')
        assert hasattr(User, '__tablename__')
        assert hasattr(Reward, '__tablename__')

        # 验证表名
        assert Task.__tablename__ == "tasks"
        assert Agent.__tablename__ == "agents"
        assert User.__tablename__ == "users"
        assert Reward.__tablename__ == "rewards"
