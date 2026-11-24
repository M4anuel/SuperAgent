"""Regression evidence for the backend priorities recovered from Codex history."""
import asyncio
import ctypes
import json
import os
from pathlib import Path
import sys

import pytest

from superagent.brain import SuperagentBrain
from superagent.config import DEFAULT_CONFIG, SuperagentConfig
from superagent.execution import agent_command, validate_project_path
from superagent.processes import ProcessSupervisor
from superagent.runner import SubagentRunner, TaskRecord


@pytest.mark.parametrize("agent", ["codex", "agy", "antigravity", "claude"])
def test_explicit_routing_preserves_multiline_brief(agent):
    brain = SuperagentBrain(available_agents=["codex", "agy", "claude"])
    brief = "Fix this failure:\nTraceback:\n    ValueError('example')\nThen run tests."
    decision = brain._fast_triage(f"@{agent}:high {brief}", {"name": "test"})
    assert decision.action == "delegate"
    assert decision.tasks[0].agent == ("agy" if agent == "antigravity" else agent)
    assert decision.tasks[0].effort == "high"
    assert decision.tasks[0].task_brief == brief


@pytest.mark.parametrize("agent", ["codex", "agy", "antigravity", "claude"])
@pytest.mark.parametrize("profile", ["read-only", "workspace-write", "unrestricted"])
def test_execution_policy_requires_explicit_unrestricted_opt_in(tmp_path, agent, profile):
    command = agent_command(agent, "review this repository", str(tmp_path), "high", profile)
    bypass_flags = {"--dangerously-bypass-approvals-and-sandbox", "--dangerously-skip-permissions"}
    assert bool(bypass_flags.intersection(command)) == (profile == "unrestricted")
    if agent == "codex":
        assert command[:3] == ["codex", "-a", "never"]
        if profile != "unrestricted":
            assert command[command.index("--sandbox") + 1] == profile
    elif profile != "unrestricted":
        mode = command[command.index("--permission-mode" if agent == "claude" else "--mode") + 1]
        assert mode == ("plan" if profile == "read-only" else "acceptEdits" if agent == "claude" else "accept-edits")
        if agent != "claude":
            assert "--sandbox" in command


@pytest.mark.parametrize("kwargs", [{"agent": "unknown"}, {"effort": "max"}, {"profile": "unsafe"}])
def test_invalid_execution_policy_is_rejected(tmp_path, kwargs):
    arguments = dict(agent="codex", prompt="review", cwd=str(tmp_path))
    arguments.update(kwargs)
    with pytest.raises(ValueError):
        agent_command(**arguments)


def test_first_run_has_no_implicit_project_and_preserves_defaults(tmp_path):
    original = json.loads(json.dumps(DEFAULT_CONFIG))
    config = SuperagentConfig(config_dir=tmp_path / "first")
    assert config.active_project is None
    assert config.list_projects() == []
    assert config.execution_profile == "workspace-write"
    config.add_project("explicit", str(tmp_path))
    assert DEFAULT_CONFIG == original
    other = SuperagentConfig(config_dir=tmp_path / "second")
    assert other.active_project is None


def test_existing_registry_survives_loading_and_selects_deepest_project(tmp_path):
    nested = tmp_path / "outer" / "inner"
    nested.mkdir(parents=True)
    config = SuperagentConfig(config_dir=tmp_path / "settings")
    config.add_project("outer", str(nested.parent))
    config.add_project("inner", str(nested))
    config.set_active_project("outer")
    loaded = SuperagentConfig(config_dir=config.config_dir)
    assert loaded.active_project.name == "outer"
    assert loaded.detect_project(str(nested / "src")).name == "inner"
    assert loaded.detect_project(str(tmp_path)) is None
    assert loaded.get_project("inner").path == str(nested.resolve())


@pytest.mark.parametrize("kind", ["empty", "missing", "file"])
def test_invalid_project_paths_do_not_change_registry(tmp_path, kind):
    file = tmp_path / "regular-file"
    file.write_text("not a directory", encoding="utf-8")
    path = {"empty": " ", "missing": str(tmp_path / "missing"), "file": str(file)}[kind]
    config = SuperagentConfig(config_dir=tmp_path / "settings")
    with pytest.raises(ValueError):
        config.add_project("invalid", path)
    assert config.list_projects() == []
    with pytest.raises(ValueError):
        validate_project_path(path)


def pid_is_alive(pid):
    if os.name != "nt":
        # Linux may retain a killed descendant as a zombie until init reaps it.
        status = Path(f"/proc/{pid}/stat")
        if status.exists() and status.read_text().split(")", 1)[1].split()[0] == "Z":
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


async def wait_until(predicate, timeout=5):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(poll(), timeout)


def python_task(runner, tmp_path, script):
    return runner.create_task("codex", "controlled process", "test", str(tmp_path),
                              custom_cmd=[sys.executable, "-u", "-c", script])


TREE_SCRIPT = """
import os, subprocess, sys, time
time.sleep(0.05)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
print('PIDS', os.getpid(), child.pid, flush=True)
time.sleep(60)
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["cancel", "timeout", "shutdown"])
async def test_worker_cleanup_reaps_root_and_descendant(tmp_path, ending):
    runner = SubagentRunner(task_timeout=2 if ending == "timeout" else 30)
    record = python_task(runner, tmp_path, TREE_SCRIPT)
    execution = runner.supervisor.track(runner.execute_task(record))
    pids = []
    try:
        await wait_until(lambda: any(line.startswith("PIDS ") for line in record.log_lines))
        pids = [int(pid) for pid in next(line for line in record.log_lines if line.startswith("PIDS ")).split()[1:]]
        assert all(pid_is_alive(pid) for pid in pids)
        if ending == "cancel":
            assert runner.cancel_task(record.task_id)
            assert not runner.cancel_task(record.task_id)
        elif ending == "shutdown":
            await asyncio.wait_for(runner.aclose(), 8)
        await asyncio.wait_for(asyncio.gather(execution, return_exceptions=True), 8)
        assert record.status == ("FAILED" if ending == "timeout" else "CANCELLED")
        assert record.end_time is not None
        assert record._process is None
        if ending == "timeout":
            assert "timed out" in record.error_summary
        await wait_until(lambda: not any(pid_is_alive(pid) for pid in pids))
    finally:
        await runner.aclose()
    assert not runner.supervisor._processes
    assert not runner.supervisor._background


@pytest.mark.asyncio
async def test_cancel_before_worker_launch_never_starts_process(tmp_path):
    marker = tmp_path / "should-not-exist"
    runner = SubagentRunner()
    record = python_task(runner, tmp_path, f"from pathlib import Path; Path({str(marker)!r}).touch()")
    assert runner.cancel_task(record.task_id)
    await runner.execute_task(record)
    assert record.status == "CANCELLED"
    assert not marker.exists()
    await runner.aclose()


@pytest.mark.asyncio
async def test_deleted_project_is_rejected_before_launch(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runner = SubagentRunner()
    record = python_task(runner, project, "raise AssertionError('must not launch')")
    project.rmdir()
    await runner.execute_task(record)
    assert record.status == "FAILED"
    assert "does not exist" in record.error_summary
    assert record._process is None
    await runner.aclose()


@pytest.mark.asyncio
async def test_worker_stream_handles_large_line_and_split_unicode(tmp_path):
    runner = SubagentRunner()
    script = "import os,time; os.write(1,b'x'*100000); os.write(1,b'\\n'); os.write(1,b'\\xe2'); time.sleep(.05); os.write(1,b'\\x82\\xac\\n')"
    record = python_task(runner, tmp_path, script)
    try:
        await runner.execute_task(record)
        assert record.status == "DONE", record.error_summary
        assert "".join(record.log_lines) == "x" * 100000 + "\u20ac"
        assert record.returncode == 0
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_supervisor_cancellation_during_launch_cleans_child(tmp_path, monkeypatch):
    original_spawn = asyncio.create_subprocess_exec
    launched = asyncio.Event()
    release = asyncio.Event()
    processes = []

    async def delayed_spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        processes.append(process)
        launched.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    supervisor = ProcessSupervisor()

    async def work():
        async with supervisor.process(sys.executable, "-c", "import time; time.sleep(60)"):
            pytest.fail("Cancelled launch must not yield a process")

    execution = supervisor.track(work())
    try:
        await asyncio.wait_for(launched.wait(), 5)
        execution.cancel()
        release.set()
        await asyncio.wait_for(asyncio.gather(execution, return_exceptions=True), 8)
        assert execution.cancelled()
        assert all(not pid_is_alive(process.pid) for process in processes)
    finally:
        release.set()
        await supervisor.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["planner", "synthesizer"])
async def test_coordinator_uses_read_only_policy_and_cleans_up_timeout(tmp_path, monkeypatch, role):
    import superagent.brain as brain_module
    import superagent.runner as runner_module

    marker = tmp_path / "coordinator.pid"
    script = f"import os,time; from pathlib import Path; Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"
    commands = []

    def controlled_command(agent, prompt, cwd, effort, profile):
        commands.append(agent_command(agent, prompt, cwd, effort, profile))
        assert profile == "read-only"
        return [sys.executable, "-u", "-c", script]

    original_wait_for = asyncio.wait_for

    async def short_coordinator_timeout(awaitable, timeout):
        return await original_wait_for(awaitable, 0.5 if timeout in (25.0, 45.0) else timeout)

    monkeypatch.setattr(brain_module, "agent_command", controlled_command)
    monkeypatch.setattr(runner_module, "resolve_executable", lambda executable: [executable])
    monkeypatch.setattr(asyncio, "wait_for", short_coordinator_timeout)
    brain = SuperagentBrain(available_agents=["codex"])
    project = {"name": "test", "path": str(tmp_path), "default_agent": "codex"}
    try:
        if role == "planner":
            result = await brain.triage("review architecture and identify reliability improvements", project)
            assert result.action == "delegate"
        else:
            record = TaskRecord("test", "codex", "review architecture", "test", str(tmp_path), ["codex"])
            record.status = "DONE"
            record.log_lines = ["Preserved finding from completed worker"]
            result = await brain.synthesize_results("review architecture", "review", [record], project)
            assert "Preserved finding" in result
        assert len(commands) == 1
        assert marker.exists()
        await wait_until(lambda: not pid_is_alive(int(marker.read_text())))
        assert not brain.supervisor._processes
    finally:
        await brain.supervisor.aclose()
