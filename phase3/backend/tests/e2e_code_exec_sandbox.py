"""F6 回归：Docker 不可用时 CodeExecutor 拒绝在宿主执行不可信代码（防 RCE）。

真实运行（无 mock）：构造一段会写标记文件的"恶意"input_data，让其走 CodeExecutor。
修复前：_run_directly 会在宿主 venv 裸跑 → 标记文件被创建（RCE）。
修复后：Docker 不可用时 _run_code 直接拒绝 → 标记文件不存在，且交付物含拒绝说明。

运行：C:/nautilus-venv/Scripts/python.exe tests/e2e_code_exec_sandbox.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_engine.executors.code_executor import CodeExecutor

MARKER = os.path.join(tempfile.gettempdir(), "nautilus_rce_proof.txt")


class _State:
    task_id = 999999
    task_type = "CODE"
    description = "demo code task"
    expected_output = ""
    # 多行 + >=2 代码特征 → _looks_like_code 为真 → 直接作为待执行代码（不经 LLM）
    input_data = (
        "import os\n"
        "def pwn():\n"
        f"    open(r'{MARKER}', 'w').write('pwned')\n"
        "    return 'pwned'\n"
        "print(pwn())\n"
    )


async def main():
    if os.path.exists(MARKER):
        os.remove(MARKER)

    ex = CodeExecutor()
    assert ex.docker_client is None, "测试前提：本机 Docker 不可用（与生产私有化一致）"

    out = await ex.execute(_State())
    rce = os.path.exists(MARKER)
    if rce:
        os.remove(MARKER)

    print("OUTPUT(head):", out[:160].replace("\n", " "))
    print("RCE_FILE_CREATED:", rce)
    refused = ("沙箱" in out) or ("拒绝" in out) or ("执行失败" in out)
    ok = (not rce) and refused
    print("F6_PASS" if ok else "F6_FAIL")
    sys.exit(0 if ok else 1)


asyncio.run(main())
