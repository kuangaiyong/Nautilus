"""
Code Executor - Generates and executes code tasks using LLM + Docker sandbox.
"""
import docker
import tempfile
import os
import json
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


class CodeExecutor:
    """Execute code tasks using LLM for generation and Docker for safe execution."""

    def __init__(self):
        self._llm = None
        try:
            self.docker_client = docker.from_env()
            logger.info("Docker client initialized")
        except Exception as e:
            logger.warning(
                "Docker not available — code execution will be REFUSED (no host "
                "fallback, for security): %s", e,
            )
            self.docker_client = None

    @property
    def llm(self):
        if self._llm is None:
            from agent_engine.llm.client import get_llm_client
            self._llm = get_llm_client()
        return self._llm

    async def execute(self, state) -> str:
        """Execute code task. Generates code via LLM if input isn't code."""
        logger.info(f"Executing code task {state.task_id}")
        input_data = state.input_data or ""

        if _looks_like_code(input_data):
            code = input_data
        else:
            code = await self._generate_code(state)

        # 交付物以生成的代码为主（"实现"类任务的产出就是代码），附运行输出；
        # 运行失败不影响交付（代码仍返回），便于发布者评审。
        try:
            output = await self._run_code(code)
        except Exception as e:
            output = f"(执行失败: {e})"

        return f"```python\n{code}\n```\n\n--- 运行输出 ---\n{output}".strip()

    async def _generate_code(self, state) -> str:
        """Generate Python code using LLM."""
        prompt = f"""Write Python code to solve this task.

Task: {state.description}
Input/Requirements: {(state.input_data or '')[:3000]}
Expected Output: {state.expected_output or 'Print the result to stdout'}

Requirements:
- Complete, runnable Python script
- Available libraries: numpy, scipy, matplotlib, pandas, sympy, scikit-learn, statsmodels, seaborn, networkx, torch (CPU), lmfit (curve fitting), gmsh (mesh generation), meshio (mesh I/O), pyvista (3D visualization), pyDOE2 (design of experiments), uncertainties (error propagation), cantera (chemical kinetics, if available), fenics-dolfinx (FEM solver, if available)
- For plots: save to /workspace/output/ directory (use plt.savefig('/workspace/output/plot.png'))
- Print numerical results to stdout

Respond with ONLY Python code in ```python ... ``` markers."""

        import asyncio
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self.llm.chat,
                    prompt=prompt,
                    system="You are an expert Python programmer. Write clean, correct, runnable code.",
                    temperature=0.2,
                    max_tokens=4096,
                ),
                timeout=60,  # 60 second hard timeout
            )
        except asyncio.TimeoutError:
            logger.warning("LLM call timed out after 60s, retrying with shorter prompt")
            short_prompt = (
                f"Write a concise Python script (under 100 lines) for: "
                f"{state.description[:500]}\n\n"
                f"Use numpy, scipy, matplotlib. Print results. "
                f"Save plot to /workspace/output/result.png"
            )
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self.llm.chat,
                    prompt=short_prompt,
                    system="Write short, working Python code.",
                    temperature=0.3,
                    max_tokens=4096,
                ),
                timeout=45,
            )
        return _extract_code(response)

    async def _run_code(self, code: str) -> str:
        """Run code in an isolated Docker sandbox.

        SECURITY: if Docker is unavailable we REFUSE to run — never fall back to
        executing untrusted (LLM- or task-supplied) code on the host. The backend
        process can read .env (WALLET_MASTER_KEY / BLOCKCHAIN_PRIVATE_KEY) and
        custodial private keys, so host execution is a remote-code-execution /
        key-theft vector. The generated code is still delivered to the caller;
        only the run-output is skipped (see execute()'s try/except).
        """
        if not self.docker_client:
            raise RuntimeError(
                "安全沙箱(Docker)不可用，已拒绝在宿主直接执行不可信代码（防 RCE / 私钥窃取）"
            )
        return await self._run_in_docker(code)

    async def _run_in_docker(self, code: str) -> str:
        """Run code in Docker container (non-blocking), with a hard wall-clock
        timeout so a runaway / while-True submission cannot pin the worker."""
        import asyncio

        def _docker_run(code_text):
            with tempfile.TemporaryDirectory() as tmpdir:
                # Create output dir for plots
                os.makedirs(os.path.join(tmpdir, "output"), exist_ok=True)
                code_file = os.path.join(tmpdir, "solution.py")
                with open(code_file, "w") as f:
                    f.write(code_text)
                # detach so we can enforce a wall-clock timeout and kill a hung container.
                container = self.docker_client.containers.run(
                    image="nautilus-scientific:latest",
                    command="python solution.py",
                    volumes={tmpdir: {"bind": "/workspace", "mode": "rw"}},
                    working_dir="/workspace",
                    mem_limit="512m",
                    cpu_quota=100000,
                    network_mode="none",
                    detach=True, stdout=True, stderr=True,
                )
                try:
                    result = container.wait(timeout=60)  # 墙钟超时：超时抛异常 → 下方 kill
                    logs = container.logs(stdout=True, stderr=True).decode("utf-8")
                    if result.get("StatusCode", 0) != 0:
                        raise RuntimeError(f"Code execution failed: {logs}")
                    return logs
                except Exception:
                    try:
                        container.kill()
                    except Exception:
                        pass
                    raise
                finally:
                    try:
                        container.remove(force=True)
                    except Exception:
                        pass

        return await asyncio.to_thread(_docker_run, code)

    async def run_tests(self, code: str, tests: str) -> Dict[str, Any]:
        """Run unit tests on code."""
        try:
            output = await self._run_code(f"{code}\n\n{tests}")
            return {"passed": True, "output": output}
        except RuntimeError as e:
            return {"passed": False, "error": str(e)}

    async def static_analysis(self, code: str) -> Dict[str, Any]:
        """Run static analysis using LLM."""
        prompt = f'Analyze this Python code for issues:\n\n```python\n{code}\n```\n\nRespond in JSON: {{"issues": [], "score": 0}}'
        response = self.llm.chat(prompt=prompt, temperature=0.1, max_tokens=2048)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {"issues": [], "score": 7}


def _looks_like_code(text: str) -> bool:
    """Heuristic: does text look like Python code?"""
    indicators = ["def ", "class ", "import ", "print(", "for ", "if __name__", "return "]
    if len(text.strip().split("\n")) < 2:
        return False
    return sum(1 for i in indicators if i in text) >= 2


def _extract_code(response: str) -> str:
    """Extract Python code from LLM response. Handles various formats."""
    response = response.strip()

    # Try ```python ... ``` first
    for marker in ["```python", "```py", "```"]:
        if marker in response:
            start = response.index(marker) + len(marker)
            try:
                end = response.index("```", start)
                return response[start:end].strip()
            except ValueError:
                # No closing ```, take everything after the marker
                return response[start:].strip()

    # Try <code> ... </code> or <python> ... </python>
    for open_tag, close_tag in [("<code>", "</code>"), ("<python>", "</python>")]:
        if open_tag in response:
            start = response.index(open_tag) + len(open_tag)
            end = response.find(close_tag, start)
            if end == -1:
                end = len(response)
            return response[start:end].strip()

    # If response looks like code already, return as-is
    if _looks_like_code(response):
        return response

    # Last resort: strip any non-code preamble (text before first import/def/class)
    for keyword in ["import ", "from ", "def ", "class ", "#!"]:
        idx = response.find(keyword)
        if idx != -1:
            return response[idx:].strip()

    return response
