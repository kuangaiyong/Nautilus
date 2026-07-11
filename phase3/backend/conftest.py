"""
Pytest配置文件
确保测试隔离和指标重置
"""
import pytest
import sys
import os

from sqlalchemy import event
from sqlalchemy.engine import Engine

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# 以下测试引用了本代码库从未实现的 services.*_service 模块（phase2 时代规划、从未落地的
# 功能），import 阶段即 ModuleNotFoundError，无法用文件内 skip 跳过。集中忽略以恢复测试
# 套件可收集性；与私有化 SQLite→MySQL 改造无关。若将来实现对应 service，从本列表移除即可。
collect_ignore = [
    "tests/test_agent_evolution.py",          # services.agent_evolution_service
    "tests/test_cache_performance.py",         # services.wallet_service
    "tests/test_capability_capsule.py",        # services.capability_capsule_service
    "tests/test_capability_transfer.py",       # services.capability_capsule_service
    "tests/test_enhanced_reflection.py",       # services.enhanced_reflection_service
    "tests/test_knowledge_emergence.py",       # services.knowledge_emergence_service
    "tests/test_knowledge_extraction.py",      # services.knowledge_extraction_service
    "tests/test_knowledge_visualization.py",   # services.knowledge_visualization_service
    "tests/test_learning_path.py",             # services.learning_path_service
    "tests/test_learning_tracking.py",         # services.learning_tracking_service
    "tests/test_phase2_integration.py",        # services.newbie_protection_service
    "tests/test_roi_enhancement.py",           # services.financial_service
    "tests/test_specialization.py",            # services.specialization_service
    "tests/test_task_recommendation.py",       # services.task_recommendation_service
    "tests/test_week4_integration.py",         # services.financial_service
    "tests/test_week5_performance.py",         # services.enhanced_reflection_service
]


@event.listens_for(Engine, "connect")
def _test_disable_mysql_fk_checks(dbapi_conn, conn_record):
    """测试连接关闭 MySQL 外键强制，等价于 SQLite 默认（不强制 FK）的宽松行为。

    历史测试大量按非 FK 顺序插数据（依赖 SQLite 不强制外键）；MySQL/InnoDB 默认强制，
    会 1452 报错。仅 pytest 加载本 conftest 时注册，生产后端不加载 conftest，不受影响。
    同时让 drop_all 无需理会表间 FK 依赖顺序。
    """
    try:
        cur = dbapi_conn.cursor()
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        cur.close()
    except Exception:
        pass


@pytest.fixture(scope="function", autouse=True)
def reset_metrics():
    """每个测试前重置Prometheus指标"""
    from utils.metrics_registry import reset_metrics
    reset_metrics()
    yield
    # 测试后清理
    reset_metrics()


@pytest.fixture(scope="session")
def test_db():
    """测试数据库配置（MySQL 测试库 nautilus_test，与运行库隔离）"""
    from tests.testdb import TEST_DATABASE_URL
    return TEST_DATABASE_URL


@pytest.fixture(scope="function")
def db_session(test_db):
    """创建测试数据库会话"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models.database import Base

    engine = create_engine(test_db)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    yield session

    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def client():
    """创建测试客户端"""
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app)
