from __future__ import annotations
import asyncio
import codecs
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional
from superagent.execution import agent_command, validate_project_path
from superagent.processes import ProcessSupervisor

ANSI_REGEX = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')

def strip_ansi(text: str) -> str:
    """Removes ANSI terminal escape color sequences from string."""
    return ANSI_REGEX.sub('', text)

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
        self._execution_task: Optional[asyncio.Task] = None
        self.execution_profile = 'workspace-write'
        self._cancel_requested = False

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
            "Reading additional input", "--------", "[Superagent Error]"
        )
        for line in self.log_lines:
            plain = strip_ansi(line).strip()
            if not plain:
                continue
            # Skip verbatim CLI role headers
            if plain.lower() in ("user", "codex", "user:", "codex:"):
                continue
            if any(plain.startswith(p) for p in skip_prefixes):
                continue
            filtered.append(line)
        return "\n".join(filtered).strip()

class SubagentRunner:
    def __init__(self, execution_profile: str = 'workspace-write', task_timeout: float = 1800):
        if not 0 < task_timeout < float('inf'):
            raise ValueError('Task timeout must be positive and finite.')
        self.execution_profile = execution_profile
        self.task_timeout = task_timeout
        self.supervisor = ProcessSupervisor()
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
        cwd = validate_project_path(cwd)
        task_id = str(uuid.uuid4())[:8]

        # Contextual prompt: prepend guidelines reminder and boundary containment
        directives: List[str] = []
        if (Path(cwd) / "SUPERAGENT.md").exists():
            directives.append("[Follow SUPERAGENT.md in repo]")
        if (Path(cwd) / "AGENTS.md").exists():
            directives.append("[Follow AGENTS.md in repo]")
        directives.append(f"[Remain strictly inside repository root: {cwd}]")
        prefix = f"{' '.join(directives)} "
        prompt_with_rules = f"{prefix}{task_desc}"

        # Resolve command based on agent
        if custom_cmd:
            cmd = custom_cmd
        else:
            cmd = agent_command(agent, prompt_with_rules, cwd, effort, self.execution_profile)

        record = TaskRecord(
            task_id=task_id,
            agent=agent,
            task_desc=task_desc,
            project_name=project_name,
            cwd=cwd,
            cmd=cmd,
            effort=effort
        )
        record.execution_profile = 'custom-command' if custom_cmd else self.execution_profile
        self.tasks[task_id] = record
        self._notify(record, "new")
        return record

    async def execute_task(self, task: TaskRecord) -> None:
        if task.status != 'QUEUED':
            return
        task._execution_task = asyncio.current_task()
        task.status = "RUNNING"
        task.start_time = time.time()
        self._notify(task, "status")

        try:
            await asyncio.wait_for(self._run_process(task), timeout=self.task_timeout)
            if task.status != "CANCELLED":
                task.status = "DONE" if task.returncode == 0 else "FAILED"
                try:
                    from superagent.git_utils import GitManager
                    gm = GitManager(task.cwd)
                    if await asyncio.to_thread(gm.is_git_repo):
                        task.diff_summary = (await asyncio.to_thread(gm.get_uncommitted_diff_stat)
                                             or await asyncio.to_thread(gm.get_status_short))
                except Exception:
                    pass
                if task.status == "FAILED" and task.log_lines:
                    non_empty = [l for l in task.log_lines if l.strip()]
                    task.error_summary = "\n".join(non_empty[-5:])
        except asyncio.CancelledError:
            task.status = 'CANCELLED'
            raise
        except asyncio.TimeoutError:
            task.status = 'FAILED'
            task.error_summary = f'Task timed out after {self.task_timeout:g} seconds.'
            task.log_lines.append(task.error_summary)
        except Exception as e:
            err_msg = f"[Superagent Error] {task.agent}: {str(e)}"
            task.log_lines.append(err_msg)
            task.error_summary = err_msg
            if task.status != 'CANCELLED':
                task.status = "FAILED"
        finally:
            if task._process is not None:
                task.returncode = task._process.returncode
            task._process = None
            task._execution_task = None
            task.end_time = time.time()
            self._notify(task, "status")

    async def _run_process(self, task: TaskRecord) -> None:
        # Revalidate stored project paths immediately before launching.
        validate_project_path(task.cwd)
        actual_args = resolve_executable(task.cmd[0]) + task.cmd[1:]
        async with self.supervisor.process(
            *actual_args, cwd=task.cwd, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=os.environ.copy(),
        ) as proc:
            task._process = proc
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            pending = ''
            while True:
                chunk = await proc.stdout.read(16384)
                pending += decoder.decode(chunk, final=not chunk)
                while '\n' in pending or len(pending) >= 65536:
                    split = pending.find('\n')
                    if split < 0:
                        split = 65536
                        line, pending = pending[:split], pending[split:]
                    else:
                        line, pending = pending[:split], pending[split + 1:]
                    task.log_lines.append(line.rstrip('\r'))
                    self._notify(task, 'log')
                if not chunk:
                    break
            if pending:
                task.log_lines.append(pending.rstrip('\r'))
                self._notify(task, 'log')
            await proc.wait()
            task.returncode = proc.returncode

    def cancel_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task:
            return False
        if task.status in ('QUEUED', 'RUNNING', 'TESTING'):
            if task._cancel_requested:
                return False
            task._cancel_requested = True
            if task._execution_task:
                task._execution_task.cancel()
            else:
                task.status = 'CANCELLED'
                task.end_time = time.time()
            self._notify(task, "status")
            return True
        return False

    async def aclose(self) -> None:
        executions = [t._execution_task for t in self.tasks.values() if t._execution_task]
        for task_id in list(self.tasks):
            self.cancel_task(task_id)
        await asyncio.gather(*executions, return_exceptions=True)
        await self.supervisor.aclose()
