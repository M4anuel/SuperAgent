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
from superagent.execution import validate_project_path
from superagent.processes import ProcessSupervisor
from superagent.adapters import (INPUT_PROTOCOL, PendingChoice, ProcessAdapter,
                                 TaskInput, UnsupportedContinuationError, completion_choice)

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
        self.status = "QUEUED"  # QUEUED, RUNNING, AWAITING_INPUT, TESTING, DONE, FAILED, CANCELLED
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.log_lines: List[str] = []
        self.returncode: Optional[int] = None
        self.diff_summary: Optional[str] = None
        self.error_summary: Optional[str] = None
        self._process: Optional[asyncio.subprocess.Process] = None
        self._execution_task: Optional[asyncio.Task] = None
        self._execution_done: Optional[asyncio.Event] = None
        self.execution_profile = 'workspace-write'
        self._cancel_requested = False
        self._stream_result: Optional[str] = None
        self.pending_choice: Optional[PendingChoice] = None
        self.session_id: Optional[str] = None
        self.context: List[TaskInput] = []
        self.continuation_error: Optional[str] = None
        self.adapter = ProcessAdapter(agent)
        self.prompt = task_desc
        self._pending_context: List[str] = []
        self._input_lock = asyncio.Lock()
        self._attempt_log_start = 0

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
        if getattr(self, "_stream_result", None):
            return self._stream_result.strip()
        if not self.log_lines:
            return ""

        filtered: List[str] = []
        skip_prefixes = (
            "OpenAI Codex", "workdir:", "model:", "provider:", "approval:", "sandbox:",
            "reasoning effort:", "reasoning summaries:", "session id:", "tokens used",
            "Reading additional input", "--------", "[Superagent Error]"
        )
        for line in self.log_lines[self._attempt_log_start:]:
            plain = strip_ansi(line).strip()
            if not plain:
                filtered.append("")
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
        """Subscribe to new/log/status, choice, answered, context, cancelled,
        and continuation_unsupported events. Payload is always the targeted record.
        """
        self.subscribers.append(callback)

    @property
    def pending_choices(self) -> List[PendingChoice]:
        return [t.pending_choice for t in self.tasks.values()
                if t.status == 'AWAITING_INPUT' and t.pending_choice is not None]

    def _await_input(self, task: TaskRecord, choice: PendingChoice) -> None:
        if task.status in ('CANCELLED', 'FAILED', 'DONE') or task._cancel_requested:
            return
        if task.pending_choice == choice:
            return
        task.pending_choice = choice
        task.status = 'AWAITING_INPUT'
        task.end_time = None
        self._notify(task, 'choice')
        self._notify(task, 'status')

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
        prompt_with_rules = f"{prefix}{task_desc}{INPUT_PROTOCOL}"
        adapter = ProcessAdapter(agent, custom=bool(custom_cmd))

        # Resolve command based on agent
        if custom_cmd:
            cmd = custom_cmd
        else:
            cmd = adapter.start_command(prompt_with_rules, cwd, effort, self.execution_profile)

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
        record.adapter = adapter
        record.prompt = prompt_with_rules
        self.tasks[task_id] = record
        self._notify(record, "new")
        return record

    async def execute_task(self, task: TaskRecord) -> None:
        if task.status != 'QUEUED':
            return
        task._execution_task = asyncio.current_task()
        task._execution_done = asyncio.Event()
        task.status = "RUNNING"
        task.start_time = task.start_time or time.time()
        task.end_time = None
        task.returncode = None
        task.error_summary = None
        task._stream_result = None
        task._attempt_log_start = len(task.log_lines)
        self._notify(task, "status")

        try:
            await asyncio.wait_for(self._run_process(task), timeout=self.task_timeout)
            if task.status != "CANCELLED":
                if task.returncode != 0:
                    task.status = 'FAILED'
                    task.pending_choice = None
                else:
                    if task.pending_choice is None:
                        output = task._stream_result or '\n'.join(
                            task.log_lines[task._attempt_log_start:])
                        choice = completion_choice(task.task_id, strip_ansi(output))
                        if choice:
                            self._await_input(task, choice)
                    task.status = 'AWAITING_INPUT' if task.pending_choice else 'DONE'
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
            task.pending_choice = None
            raise
        except asyncio.TimeoutError:
            task.status = 'FAILED'
            task.pending_choice = None
            task.error_summary = f'Task timed out after {self.task_timeout:g} seconds.'
            task.log_lines.append(task.error_summary)
        except Exception as e:
            err_msg = f"[Superagent Error] {task.agent}: {str(e)}"
            task.log_lines.append(err_msg)
            task.error_summary = err_msg
            if task.status != 'CANCELLED':
                task.status = "FAILED"
                task.pending_choice = None
        finally:
            if task._process is not None:
                task.returncode = task._process.returncode
            task._process = None
            task._execution_task = None
            task.end_time = None if task.status == 'AWAITING_INPUT' else time.time()
            task._execution_done.set()
            self._notify(task, "status")

    def _append_log_line(self, task: TaskRecord, line: str) -> None:
        raw = line.rstrip('\r')
        stripped = raw.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                import json
                data = json.loads(stripped)
                if not isinstance(data, dict):
                    raise ValueError('Expected a JSON event object')
                session, final_text, choice = task.adapter.read_signal(task.task_id, data)
                if session:
                    task.session_id = session
                if final_text is not None:
                    task._stream_result = final_text
                if choice:
                    self._await_input(task, choice)
                event = data.get("event")
                if data.get('type') == 'item.completed' and final_text is not None:
                    task.log_lines.extend(final_text.splitlines())
                    self._notify(task, 'log')
                    return
                if data.get('type') == 'thread.started' or choice:
                    task.log_lines.append(raw)
                    self._notify(task, 'log')
                    return
                if event == "step_update":
                    step = data.get("step_update", {})
                    if "tool_name" in step:
                        tool_name = step["tool_name"]
                        info = step.get("tool_info") or {}
                        params = info.get("parameters") or {} if isinstance(info, dict) else {}
                        target = params.get("Path") or params.get("CommandLine") or params.get("query") or ""
                        target_str = str(target).strip().replace("\n", " ")
                        if len(target_str) > 60:
                            target_str = target_str[:57] + "..."
                        suffix = f" → {target_str}" if target_str else ""
                        task.log_lines.append(f"[{task.agent.upper()}] 🛠️ {tool_name}{suffix}")
                        self._notify(task, 'log')
                        return
                    elif "text_delta" in step:
                        delta = step["text_delta"]
                        if delta.strip():
                            for l in delta.splitlines():
                                if l.strip():
                                    task.log_lines.append(f"[{task.agent.upper()}] {l.strip()}")
                                    self._notify(task, 'log')
                            return
                elif event == "result":
                    res = data.get("result", {})
                    task._stream_result = res.get("response", "")
                    task.log_lines.append(f"[{task.agent.upper()}] ✔️ Task finished ({res.get('status', 'SUCCESS')})")
                    self._notify(task, 'log')
                    return
                elif event == "init":
                    cid = (task.session_id or '')[:8]
                    task.log_lines.append(f"[{task.agent.upper()}] ⚡ Session initialized ({cid})")
                    self._notify(task, 'log')
                    return
            except Exception:
                pass
        task.log_lines.append(raw)
        self._notify(task, 'log')

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
                    self._append_log_line(task, line)
                if not chunk:
                    break
            if pending:
                self._append_log_line(task, pending)
            await proc.wait()
            task.returncode = proc.returncode

    def cancel_task(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if not task:
            return False
        if task.status in ('QUEUED', 'RUNNING', 'TESTING', 'AWAITING_INPUT'):
            if task._cancel_requested:
                return False
            task._cancel_requested = True
            task.pending_choice = None
            task.status = 'CANCELLED'
            task.end_time = time.time()
            if task._execution_task:
                task._execution_task.cancel()
            self._notify(task, "status")
            self._notify(task, 'cancelled')
            return True
        return False

    async def _settle_execution(self, task: TaskRecord) -> None:
        execution = task._execution_task
        if execution is not None:
            if execution is asyncio.current_task():
                raise RuntimeError('Task input must be submitted outside its execution callback.')
            # Wait for this process attempt, not the calling coroutine, which
            # may continue doing unrelated orchestration after execute_task.
            # Cancelling an input submission must not cancel the worker.
            await task._execution_done.wait()

    def _continuation_command(self, task: TaskRecord, message: str) -> List[str]:
        prompt = '\n\n'.join([task.prompt, *task._pending_context, message])
        try:
            command = task.adapter.continuation_command(
                task.session_id, prompt, task.cwd, task.effort, task.execution_profile)
        except UnsupportedContinuationError as exc:
            task.continuation_error = str(exc)
            self._notify(task, 'continuation_unsupported')
            raise
        task.continuation_error = None
        return command

    async def answer_task(self, task_id: str, answer: str) -> bool:
        """Answer one pending question and await that task's resumed execution.

        Unsupported adapters raise UnsupportedContinuationError and retain the choice.
        Unknown/nonwaiting tasks return False. Blank or disallowed answers raise ValueError.
        """
        task = self.tasks.get(task_id)
        if task is None:
            return False
        # Capture identity before waiting so simultaneous replies cannot answer a
        # different question produced by the first reply's continuation.
        choice = task.pending_choice
        async with task._input_lock:
            if choice is None or task.pending_choice is not choice or task.status != 'AWAITING_INPUT':
                return False
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError('An answer is required.')
            if not choice.allow_free_text and answer not in choice.choices:
                raise ValueError('Select one of the offered choices.')
            await self._settle_execution(task)
            if task.status != 'AWAITING_INPUT' or task.pending_choice is not choice:
                return False
            command = self._continuation_command(
                task, f'Question: {choice.question}\nUser answer: {answer}')
            task.context.append(TaskInput('answer', answer, choice.question))
            task._pending_context.clear()
            task.pending_choice = None
            task.cmd = command
            task.status = 'QUEUED'
            self._notify(task, 'answered')
            await self.execute_task(task)
            return True

    async def send_task_context(self, task_id: str, context: str) -> bool:
        """Deliver context only to this task, without answering a pending question.

        Running processes finish before continuation. Waiting tasks retain context
        until answered; queued workers receive it in their original prompt.
        """
        task = self.tasks.get(task_id)
        if task is None or task.status in ('FAILED', 'CANCELLED'):
            return False
        if not isinstance(context, str) or not context.strip():
            raise ValueError('Task context is required.')
        async with task._input_lock:
            if task.status in ('FAILED', 'CANCELLED'):
                return False
            task.context.append(TaskInput('context', context))
            task._pending_context.append(context)
            self._notify(task, 'context')
            if task.status == 'QUEUED':
                if task.adapter.custom:
                    self._continuation_command(task, '')  # Report unsupported delivery explicitly.
                task.prompt = '\n\n'.join([task.prompt, *task._pending_context])
                task.cmd = task.adapter.start_command(
                    task.prompt,
                    task.cwd, task.effort, task.execution_profile)
                task._pending_context.clear()
                return True
            await self._settle_execution(task)
            if task.status == 'AWAITING_INPUT':
                return True
            if task.status != 'DONE':
                return False
            command = self._continuation_command(task, 'Continue with the task-specific context above.')
            task._pending_context.clear()
            task.cmd = command
            task.status = 'QUEUED'
            await self.execute_task(task)
            return True

    async def aclose(self) -> None:
        executions = [t._execution_task for t in self.tasks.values() if t._execution_task]
        for task_id in list(self.tasks):
            self.cancel_task(task_id)
        await asyncio.gather(*executions, return_exceptions=True)
        await self.supervisor.aclose()
