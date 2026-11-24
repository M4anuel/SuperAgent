import asyncio
from copy import deepcopy
import os
from pathlib import Path
import sys

import pytest

from superagent.brain import SuperagentBrain
from superagent.config import DEFAULT_CONFIG, SuperagentConfig
from superagent.execution import agent_command
from superagent.processes import ProcessSupervisor
from superagent.runner import SubagentRunner, TaskRecord


@pytest.mark.parametrize('agent', ['codex', 'agy', 'claude'])
@pytest.mark.parametrize('profile', ['read-only', 'workspace-write', 'unrestricted'])
def test_execution_policy_requires_explicit_unrestricted_opt_in(tmp_path, agent, profile):
    command = agent_command(agent, 'inspect the project', str(tmp_path), profile=profile)
    bypass = any('dangerously' in arg for arg in command)
    assert bypass is (profile == 'unrestricted')
    if profile == 'read-only':
        assert 'read-only' in command or 'plan' in command
    if agent == 'codex' and profile != 'unrestricted':
        assert command[command.index('--sandbox') + 1] == profile


def test_default_worker_policy_and_invalid_dispatch(tmp_path):
    runner = SubagentRunner()
    record = runner.create_task('codex', 'edit', 'p', str(tmp_path))
    assert record.execution_profile == 'workspace-write'
    assert '--dangerously-bypass-approvals-and-sandbox' not in record.cmd
    for invalid in ['unregistered-executable', 'python']:
        with pytest.raises(ValueError, match='Unknown agent'):
            runner.create_task(invalid, 'edit', 'p', str(tmp_path))
    with pytest.raises(ValueError, match='Unknown execution profile'):
        agent_command('codex', 'edit', str(tmp_path), profile='typo')


def test_fresh_config_has_no_implicit_project_or_shared_defaults(tmp_path):
    before = deepcopy(DEFAULT_CONFIG)
    one = SuperagentConfig(tmp_path / 'one')
    assert one.active_project is None
    assert one.list_projects() == []
    assert one.execution_profile == 'workspace-write'
    one.add_project('explicit', str(tmp_path))
    assert DEFAULT_CONFIG == before
    assert SuperagentConfig(tmp_path / 'two').list_projects() == []
    assert SuperagentConfig(tmp_path / 'one').active_project.name == 'explicit'


def test_project_paths_must_exist_and_deepest_match_wins(tmp_path):
    cfg = SuperagentConfig(tmp_path / 'config')
    child = tmp_path / 'nested'
    child.mkdir()
    cfg.add_project('parent', str(tmp_path))
    cfg.add_project('child', str(child))
    assert cfg.detect_project(str(child)).name == 'child'
    assert cfg.detect_project(str(tmp_path)).name == 'parent'
    assert cfg.detect_project(str(tmp_path.parent)) is None
    for invalid in ['', str(tmp_path / 'missing'), str(cfg.config_file)]:
        with pytest.raises(ValueError):
            cfg.add_project('bad', invalid)
        with pytest.raises(ValueError):
            SubagentRunner().create_task('codex', 'edit', 'bad', invalid)


async def wait_until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(.01)
    await asyncio.wait_for(poll(), timeout=5)


def process_alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
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
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f'/proc/{pid}/stat')
    return not stat.exists() or stat.read_text().split()[2] != 'Z'


def worker(runner, tmp_path, script):
    return runner.create_task('codex', 'controlled Python worker', 'p', str(tmp_path),
                              custom_cmd=[sys.executable, '-u', '-c', script])


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['cancel-button', 'cancel-coroutine', 'timeout', 'shutdown', 'reader-error'])
async def test_process_tree_is_reaped_on_every_exit(tmp_path, monkeypatch, mode):
    runner = SubagentRunner(task_timeout=1 if mode == 'timeout' else 10)
    script = ("import subprocess,sys,time; "
              "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
              "print('child:'+str(child.pid),flush=True);time.sleep(60)")
    record = worker(runner, tmp_path, script)
    execution = asyncio.create_task(runner.execute_task(record))
    await wait_until(lambda: any(line.startswith('child:') for line in record.log_lines))
    proc = record._process
    child_pid = int(next(line.split(':')[1] for line in record.log_lines if line.startswith('child:')))
    assert process_alive(proc.pid) and process_alive(child_pid)
    try:
        if mode == 'cancel-button':
            assert runner.cancel_task(record.task_id)
            assert not runner.cancel_task(record.task_id)
        elif mode == 'cancel-coroutine':
            execution.cancel()
        elif mode == 'shutdown':
            await runner.aclose()
        elif mode == 'reader-error':
            # Inject a transport failure into the real active stream read.
            proc.stdout.set_exception(RuntimeError('controlled stream failure'))
        if mode in ('cancel-button', 'cancel-coroutine', 'shutdown'):
            with pytest.raises(asyncio.CancelledError):
                await execution
        else:
            await execution
        assert record.status == ('CANCELLED' if mode in ('cancel-button', 'cancel-coroutine', 'shutdown') else 'FAILED')
        assert record.end_time is not None
        assert proc.returncode is not None
        assert not process_alive(proc.pid)
        await wait_until(lambda: not process_alive(child_pid))
        assert not runner.supervisor._processes
        if mode == 'timeout':
            assert 'timed out' in record.error_summary
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_queued_cancel_never_launches_and_paths_are_rechecked(tmp_path):
    runner = SubagentRunner()
    record = worker(runner, tmp_path, "raise AssertionError('must not run')")
    assert runner.cancel_task(record.task_id)
    await runner.execute_task(record)
    assert record.status == 'CANCELLED' and record._process is None
    folder = tmp_path / 'disappearing'
    folder.mkdir()
    record = worker(runner, folder, "print('must not run')")
    folder.rmdir()
    await runner.execute_task(record)
    assert record.status == 'FAILED'
    assert 'does not exist' in record.error_summary


@pytest.mark.asyncio
async def test_large_lines_and_successful_worker_are_cleaned_up(tmp_path):
    runner = SubagentRunner()
    record = worker(runner, tmp_path, "import sys;sys.stdout.reconfigure(encoding='utf-8');print('x'*200000);print('Grüezi')")
    await runner.execute_task(record)
    assert record.status == 'DONE', record.error_summary
    assert sum(len(line) for line in record.log_lines[:-1]) == 200000
    assert record.log_lines[-1] == 'Grüezi'
    assert not runner.supervisor._processes


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['plan', 'synthesis'])
async def test_model_timeout_uses_same_supervision_and_readonly_policy(tmp_path, monkeypatch, operation):
    import superagent.brain as brain_module
    brain = SuperagentBrain()
    marker = tmp_path / 'model.pid'
    policies = []
    original_command = brain_module.agent_command
    def controlled_command(agent, prompt, cwd, effort, profile):
        policies.append(original_command(agent, prompt, cwd, effort, profile))
        script = f"import os,time;from pathlib import Path;Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(60)"
        return [sys.executable, '-u', '-c', script]
    monkeypatch.setattr(brain_module, 'agent_command', controlled_command)
    original_wait = asyncio.wait_for
    async def short_model_timeout(awaitable, timeout):
        return await original_wait(awaitable, .3 if timeout in (25.0, 45.0) else timeout)
    monkeypatch.setattr(asyncio, 'wait_for', short_model_timeout)
    context = {'name': 'p', 'path': str(tmp_path), 'default_agent': 'codex'}
    if operation == 'plan':
        result = await brain.triage('audit architecture thoroughly', context)
        assert result.action == 'delegate'
    else:
        record = TaskRecord('x', 'codex', 'inspect', 'p', str(tmp_path), [])
        record.status = 'DONE'
        record.log_lines = ['Controlled finding preserved.']
        result = await brain.synthesize_results('inspect', 'plan', [record], context)
        assert 'Controlled finding preserved.' in result
    assert policies and all('--dangerously-bypass-approvals-and-sandbox' not in c for c in policies)
    assert all(c[c.index('--sandbox') + 1] == 'read-only' for c in policies)
    assert marker.exists()
    assert not process_alive(int(marker.read_text()))
    assert not brain.supervisor._processes


@pytest.mark.asyncio
async def test_supervisor_shutdown_cancels_tracked_background_tasks():
    supervisor = ProcessSupervisor()
    stopped = asyncio.Event()
    async def background():
        try:
            await asyncio.sleep(60)
        finally:
            stopped.set()
    task = supervisor.track(background())
    await asyncio.sleep(0)
    await supervisor.aclose()
    assert task.cancelled() and stopped.is_set()
    assert not supervisor._background


@pytest.mark.asyncio
async def test_wrapper_exit_still_reaps_descendant_holding_stdout(tmp_path):
    runner = SubagentRunner(task_timeout=.5)
    marker = tmp_path / 'child.pid'
    script = ("import subprocess,sys;from pathlib import Path;"
              "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
              f"Path({str(marker)!r}).write_text(str(child.pid))")
    record = worker(runner, tmp_path, script)
    await runner.execute_task(record)
    assert record.status == 'FAILED' and 'timed out' in record.error_summary
    assert marker.exists()
    assert not process_alive(int(marker.read_text()))


@pytest.mark.asyncio
async def test_cancellation_during_launch_still_reaps_child(tmp_path, monkeypatch):
    real_spawn = asyncio.create_subprocess_exec
    spawned = asyncio.Event()
    release = asyncio.Event()
    processes = []
    async def delayed_spawn(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        processes.append(proc)
        spawned.set()
        await release.wait()
        return proc
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed_spawn)
    runner = SubagentRunner()
    marker = tmp_path / 'child.pid'
    script = ("import subprocess,sys,time;from pathlib import Path;"
              "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
              f"Path({str(marker)!r}).write_text(str(child.pid));time.sleep(60)")
    record = worker(runner, tmp_path, script)
    execution = asyncio.create_task(runner.execute_task(record))
    await spawned.wait()
    await wait_until(marker.exists)
    execution.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await execution
    assert record.status == 'CANCELLED'
    assert processes[0].returncode is not None
    assert not process_alive(processes[0].pid)
    assert not process_alive(int(marker.read_text()))


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['plan', 'synthesis'])
async def test_model_coroutine_cancellation_cleans_up(tmp_path, monkeypatch, operation):
    import superagent.brain as brain_module
    brain = SuperagentBrain()
    marker = tmp_path / 'model.pid'
    script = f"import os,time;from pathlib import Path;Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(60)"
    monkeypatch.setattr(brain_module, 'agent_command', lambda *args: [sys.executable, '-u', '-c', script])
    context = {'name': 'p', 'path': str(tmp_path), 'default_agent': 'codex'}
    record = TaskRecord('x', 'codex', 'inspect', 'p', str(tmp_path), [])
    record.status = 'DONE'
    record.log_lines = ['Finding']
    coroutine = (brain.triage('audit architecture', context) if operation == 'plan'
                 else brain.synthesize_results('inspect', 'plan', [record], context))
    execution = asyncio.create_task(coroutine)
    await wait_until(marker.exists)
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution
    assert not process_alive(int(marker.read_text()))
    assert not brain.supervisor._processes


@pytest.mark.asyncio
async def test_tui_shutdown_awaits_owned_worker_cleanup(tmp_path):
    from superagent.tui.app import SuperagentApp
    app = SuperagentApp()
    app.cfg.add_project('controlled', str(tmp_path))
    async with app.run_test():
        record = worker(app.runner, tmp_path, "print('ready',flush=True);import time;time.sleep(60)")
        execution = app.runner.supervisor.track(app.runner.execute_task(record))
        await wait_until(lambda: 'ready' in record.log_lines)
        proc = record._process
    assert execution.cancelled()
    assert record.status == 'CANCELLED'
    assert proc.returncode is not None
    assert not process_alive(proc.pid)
    assert not app.runner.supervisor._background
