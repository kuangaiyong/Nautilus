"""发布任务唯一入口回归测试。

平台唯一任务发布入口 = POST /api/tasks（软件工程任务市场）。
1) 其余 7 个历史创建端点必须返回 410 Gone（读端点不受影响）；
2) POST /api/tasks 的请求模型只接受 8 个 SE 任务类型，旧通用类型一律 422。
"""
import sys
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SE8 = [
    "REQUIREMENT_ANALYSIS", "ARCHITECTURE_DESIGN", "CODE_DEVELOPMENT", "CODE_REVIEW",
    "TEST_CASE_DESIGN", "TEST_AUTOMATION", "DEPLOYMENT_OPS", "DOCUMENTATION",
]
LEGACY = ["CODE", "DATA", "COMPUTE", "RESEARCH", "DESIGN", "WRITING", "OTHER"]


# ---------------------------------------------------------------------------
# 1) 7 个历史创建端点 → 410 Gone
# ---------------------------------------------------------------------------

def _client_for(router, prefix=""):
    app = FastAPI()
    app.include_router(router, prefix=prefix)
    return TestClient(app, raise_server_exceptions=False)


RETIRED_CASES = [
    # (router 模块属性, 挂载 prefix, method, path)
    ("api.academic_tasks:router", "/api/academic", "post", "/api/academic/submit"),
    ("api.marketplace:task_router", "", "post", "/api/marketplace/tasks/submit"),
    ("api.labeling_service:router", "/api/labeling", "post", "/api/labeling/jobs"),
    ("api.labeling_service:router", "/api/labeling", "post", "/api/labeling/jobs/upload"),
    ("api.simulation_tasks:router", "/api/simulation", "post", "/api/simulation/submit"),
    ("api.simulation_tasks:router", "/api/simulation", "post", "/api/simulation/batch"),
    ("api.agent_hub:router", "/api/hub", "post", "/api/hub/bounties"),
]


@pytest.mark.parametrize("router_ref,prefix,method,path", RETIRED_CASES)
def test_retired_create_endpoint_returns_410(router_ref, prefix, method, path):
    mod_name, attr = router_ref.split(":")
    import importlib
    router = getattr(importlib.import_module(mod_name), attr)
    client = _client_for(router, prefix)
    resp = getattr(client, method)(path)
    assert resp.status_code == 410, f"{path} 应 410 Gone，实际 {resp.status_code}: {resp.text[:200]}"
    detail = resp.json().get("detail", {})
    assert detail.get("error", {}).get("code") == "ENDPOINT_RETIRED"
    assert "POST /api/tasks" in detail.get("error", {}).get("message", "")


# ---------------------------------------------------------------------------
# 2) POST /api/tasks 请求模型：8 个 SE 类型可用，旧类型一律拒绝
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("se_type", SE8)
def test_task_create_accepts_se_types(se_type):
    from api.tasks import TaskCreate

    body = TaskCreate(description="d", reward=10**18, task_type=se_type, timeout=3600)
    assert body.task_type.value == se_type


@pytest.mark.parametrize("legacy_type", LEGACY)
def test_task_create_rejects_legacy_types(legacy_type):
    from api.tasks import TaskCreate

    with pytest.raises(ValidationError):
        TaskCreate(description="d", reward=10**18, task_type=legacy_type, timeout=3600)
