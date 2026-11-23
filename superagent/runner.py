from __future__ import annotations
import asyncio
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional

def resolve_executable(name: str) -> List[str]:
    """Resolves CLI executable to native binary or node/powershell wrapper to avoid cmd.exe parsing errors on Windows."""
    p = shutil.which(name)
    if not p:
        return [name]
    path_obj = Path(p)
    if path_obj.suffix.lower() in (".cmd", ".ps1", ".bat"):
        npm_dir = path_obj.parent
        # Look for native binary in node_modules (e.g. codex.exe)
        possible_bin = list(npm_dir.glob(f"node_modules/@openai/{name}/**/{name}.exe"))
        if possible_bin:
            return [str(possible_bin[0])]
        possible_bin_any = list(npm_dir.glob(f"node_modules/**/bin/{name}.exe"))
        if possible_bin_any:
            return [str(possible_bin_any[0])]

        # Look for JS entrypoint with node
        possible_js = npm_dir / "node_modules" / f"@{name}" / "bin" / f"{name}.js"
        if not possible_js.exists():
            possible_js = npm_dir / "node_modules" / name / "bin" / f"{name}.js"
        node = shutil.which("node")
        if node and possible_js.exists():
            return [node, str(possible_js)]

        # Look for ps1 wrapper
        ps1 = path_obj.with_suffix(".ps1")
        if ps1.exists():
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)]

    return [str(path_obj)]

class TaskRecord:
    def __init__(
        self,
        task_id: str,
        agent: str,
        task_desc: str,
        project_name: str,
        cwd: str,
        cmd: List[str],
        effort: str = "medium"
    ):
        self.task_id = task_id
        self.agent = agent
        self.task_desc = task_desc
        self.project_name = project_name
        self.cwd = cwd
        self.cmd = cmd
        self.effort = effort
        self.status = "QUEUED"  # QUEUED, RUNNING, TESTING, DONE, FAILED, CANCELLED
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.log_lines: List[str] = []
        self.returncode: Optional[int] = None
        self.diff_summary: Optional[str] = None
        self.error_summary: Optional[str] = None
        self._process: Optional[asyncio.subprocess.Process] = None

    @property
    def elapsed_seconds(self) -> int:
        if self.start_time is None:
            return 0
        end = self.end_time or time.time()
        return max(0, int(end - self.start_time))

    @property
    def elapsed_formatted(self) -> str:
        secs = self.elapsed_seconds
        mins, s = divmod(secs, 60)
        hours, mins = divmod(mins, 60)
        if hours > 0:
            return f"{hours:02d}h:{mins:02d}m:{s:02d}s"
        return f"{mins:02d}m:{s:02d}s"

    @property
    def clean_output(self) -> str:
        """Extracts substantive output from subagent CLI logs, omitting startup banner noise."""
        if not self.log_lines:
            return ""

        filtered: List[str] = []
        skip_prefixes = (
            "OpenAI Codex", "workdir:", "model:", "provider:", "approval:", "sandbox:",
            "reasoning effort:", "reasoning summaries:", "session id:", "tokens used",
            "Reading additional input", "--------", "user", "codex", "[Superagent Error]"
        )
        for line in self.log_lines:
            stripped = line.strip()
            if not stripped:
                continue
            if any(stripped.startswith(p) for p in skip_prefixes):
                continue
            filtered.append(line)
        return "\n".join(filtered).strip()

class SubagentRunner:
    def __init__(self):
        self.tasks: Dict[str, TaskRecord] = {}
        self.subscribers: List[Callable[[TaskRecord, str], None]] = []

    def subscribe(self, callback: Callable[[TaskRecord, str], None]) -> None:
        """callback(task_record, event_type) where event_type is 'log', 'status', 'new'"""
        self.subscribers.append(callback)

    def _notify(self, task: TaskRecord, event: str) -> None:
        for cb in self.subscribers:
            try:
                cb(task, event)
            except Exception:
                pass

    def create_task(
        self,
        agent: str,
        task_desc: str,
        project_name: str,
        cwd: str,
        effort: str = "medium",
        custom_cmd: Optional[List[str]] = None
    ) -> TaskRecord:
        task_id = str(uuid.uuid4())[:8]

        # Contextual prompt: prepend AGENTS.md reminder if present in repo
        agents_file = Path(cwd) / "AGENTS.md"
        prompt_with_rules = f"[Follow AGENTS.md in repo] {task_desc}" if agents_file.exists() else task_desc

        # Resolve command based on agent
        if custom_cmd:
            cmd = custom_cmd
        else:
            if agent.lower() == "claude":
                cmd = ["claude", "-p", prompt_with_rules]
            elif agent.lower() == "codex":
                # Codex CLI with adaptive reasoning effort and bypassed approval prompts
                cmd = [
                    "codex", "exec",
                    "--skip-git-repo-check",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-c", f'model_reasoning_effort="{effort}"',
                    "-C", cwd,
                    prompt_with_rules
                ]
            elif agent.lower() in ("agy", "antigravity"):
                cmd = ["agy", "--dangerously-skip-permissions", "--effort", effort, "-p", prompt_with_rules]
            else:
                # Default fallback
                cmd = [agent, prompt_with_rules]

        record = TaskRecord(
            task_id=task_id,
            agent=agent,
            task_desc=task_desc,
            project_name=project_name,
            cwd=cwd,
            cmd=cmd,
            effort=effort
        )
        self.tasks[task_id] = record
        self._notify(record, "new")
        return record

    async def execute_task(self, task: TaskRecord) -> None:
        task.status = "RUNNING"
        task.start_time = time.time()
        self._notify(task, "status")

        # Resolve binary to avoid Windows cmd.exe / quoting syntax errors
        exec_prefix = resolve_executable(task.cmd[0])
        actual_args = exec_prefix + task.cmd[1:]

        try:
            task._process = await asyncio.create_subprocess_exec(
                *actual_args,
                cwd=task.cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=os.environ.copy()
            )

            while True:
                line_bytes = await task._process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                task.log_lines.append(line)
                self._notify(task, "log")

            await task._process.wait()
            task.returncode = task._process.returncode
            task.end_time = time.time()

            if task.status != "CANCELLED":
                task.status = "DONE" if task.returncode == 0 else "FAILED"
                try:
                    from superagent.git_utils import GitManager
                    gm = GitManager(task.cwd)
                    if gm.is_git_repo():
                        task.diff_summary = gm.get_uncommitted_diff_stat() or gm.get_status_short()
                except Exception:
                    pass
                if task.status == "FAILED" and task.log_lines:
                    non_empty = [l for l in task.log_lines if l.strip()]
                    task.error_summary = "\n".join(non_empty[-5:])
            self._notify(task, "status")

        except Exception as e:
            err_msg = f"[Superagent Error] Failed to launch {task.agent}: {str(e)}"
            task.log_lines.append(err_msg)
            task.end_time = time.time()
            task.status = "FAILED"
            self._notify(task, "status")

    def cancel_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task:
            return False
        if task._process and task._process.returncode is None:
            try:
                task._process.terminate()
            except Exception:
                pass
            task.status = "CANCELLED"
            task.end_time = time.time()
            self._notify(task, "status")
            return True
        return False
