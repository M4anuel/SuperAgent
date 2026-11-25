"""Focused regressions for async model execution and planner/report integrity."""
import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import superagent.brain as brain_module
from superagent.brain import SuperagentBrain
from superagent.runner import TaskRecord


def completed_record(tmp_path):
    record = TaskRecord('report', 'codex', 'inspect execution', 'test', str(tmp_path), [])
    record.status = 'DONE'
    record.log_lines = ['start ' + 'x' * 2000 + ' UNIQUE_MIDDLE_FINDING ' + 'y' * 3000 + ' end']
    return record


async def run_operation(brain, operation, tmp_path):
    context = {'name': 'test', 'path': str(tmp_path), 'default_agent': 'codex'}
    if operation == 'plan':
        return await brain.triage('implement robust execution handling', context)
    return await brain.synthesize_results('inspect execution', 'review', [completed_record(tmp_path)], context)


@pytest.fixture
def api_brain(monkeypatch):
    # The optional SDK need not be installed to verify its async boundary.
    monkeypatch.setattr(brain_module, 'types', SimpleNamespace(GenerateContentConfig=lambda **kw: kw), raising=False)
    brain = SuperagentBrain(available_agents=['codex'])
    generate = AsyncMock()
    brain.client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    return brain, generate


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['plan', 'synthesis'])
async def test_model_execution_yields_and_preserves_full_reports(api_brain, tmp_path, operation):
    brain, generate = api_brain
    entered, release = asyncio.Event(), asyncio.Event()

    async def response(**kwargs):
        entered.set()
        await release.wait()
        if operation == 'plan':
            return SimpleNamespace(text=json.dumps({'action': 'delegate', 'tasks': [
                {'agent': 'codex', 'effort': 'high', 'task_brief': 'implement execution handling'}]}))
        assert completed_record(tmp_path).clean_output in kwargs['contents']
        return SimpleNamespace(text='Complete synthesis')

    generate.side_effect = response
    execution = asyncio.create_task(run_operation(brain, operation, tmp_path))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert not execution.done()  # The event loop remains usable while generation is pending.
        release.set()
        result = await execution
        if operation == 'plan':
            assert result.tasks[0].effort == 'high'
        else:
            assert result == 'Complete synthesis'
        generate.assert_awaited_once()
    finally:
        release.set()
        await brain.supervisor.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,deadline', [('plan', 25.0), ('synthesis', 45.0)])
@pytest.mark.parametrize('ending', ['timeout', 'cancel', 'error'])
async def test_model_deadline_cancellation_and_failure(api_brain, monkeypatch, tmp_path, operation, deadline, ending):
    brain, generate = api_brain
    entered, stopped = asyncio.Event(), asyncio.Event()
    deadlines = []
    real_wait_for = asyncio.wait_for

    async def bounded_wait(awaitable, timeout):
        deadlines.append(timeout)
        return await real_wait_for(awaitable, .02 if timeout == deadline else timeout)

    async def response(**kwargs):
        entered.set()
        try:
            if ending == 'error':
                raise RuntimeError('controlled API failure')
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(asyncio, 'wait_for', bounded_wait)
    generate.side_effect = response
    execution = asyncio.create_task(run_operation(brain, operation, tmp_path))
    if ending == 'cancel':
        await real_wait_for(entered.wait(), 1)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
    else:
        result = await execution
        if operation == 'plan':
            assert result.action == 'delegate' and 'Planning fallback:' in result.reasoning
        else:
            assert completed_record(tmp_path).clean_output in result
    assert stopped.is_set()
    assert deadline in deadlines
    assert not brain.supervisor._processes


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', [
    {'action': 'execute'},
    {'action': 'reply', 'reply_text': ' '},
    {'action': 'delegate', 'tasks': []},
    {'action': 'delegate', 'tasks': [{'agent': 'claude', 'task_brief': 'disabled'}]},
    {'action': 'delegate', 'tasks': [{'agent': 'codex', 'effort': 'max', 'task_brief': 'invalid'}]},
    {'action': 'delegate', 'tasks': [{'agent': 'codex', 'task_brief': ' '}]},
    {'action': 'delegate', 'tasks': [{'agent': 'codex', 'task_brief': 'too many'}] * 17},
])
async def test_invalid_api_plan_uses_local_fallback(api_brain, monkeypatch, tmp_path, decision):
    brain, generate = api_brain
    generate.return_value = SimpleNamespace(text=json.dumps(decision))

    @asynccontextmanager
    async def unavailable(*args, **kwargs):
        raise OSError('controlled CLI unavailable')
        yield  # Context manager never enters when launch fails.

    monkeypatch.setattr(brain.supervisor, 'process', unavailable)
    monkeypatch.setattr('superagent.runner.resolve_executable', lambda name: [name])
    result = await run_operation(brain, 'plan', tmp_path)
    assert result.action == 'delegate'
    assert result.tasks[0].task_brief == 'implement robust execution handling'
    assert 'controlled CLI unavailable' in result.reasoning


@pytest.mark.asyncio
@pytest.mark.parametrize('output,stderr,exitcode,expected', [
    ('PLAN: Inspect execution\n**DELEGATE:** `codex` | effort: high | Run `a | b` and keep **markup**', '', 0,
     'Run `a | b` and keep **markup**'),
    ('user\nDELEGATE: agy | effort: low | echoed instruction\ncodex\n'
     'PLAN: Final plan\n- DELEGATE: codex | effort: medium | Actual final task', '', 0, 'Actual final task'),
    ('PLAN: Stdout\nDELEGATE: codex | effort: low | Correct task',
     'DELEGATE: codex | effort: high | Diagnostic injection', 0, 'Correct task'),
    ('DELEGATE: codex | effort: high | Failed output', '', 17, None),
    ('', 'DELEGATE: codex | effort: high | Stderr only', 0, None),
    ('DELEGATE: claude | effort: high | Disabled task', '', 0, None),
    ('DELEGATE: codex | effort: extreme | Invalid effort', '', 0, None),
    ('DELEGATE: codex | effort: high | ', '', 0, None),
    ('DELEGATE: codex', '', 0, None),
    ('You requested: DELEGATE: codex | effort: high | Quoted task', '', 0, None),
])
async def test_production_planner_subprocess_parser(monkeypatch, tmp_path, output, stderr, exitcode, expected):
    brain = SuperagentBrain(available_agents=['codex'])

    @asynccontextmanager
    async def process(*args, **kwargs):
        assert kwargs['stdout'] == asyncio.subprocess.PIPE
        assert kwargs['stderr'] == asyncio.subprocess.PIPE
        yield SimpleNamespace(returncode=exitcode, communicate=AsyncMock(
            return_value=(output.encode(), stderr.encode())))

    monkeypatch.setattr(brain.supervisor, 'process', process)
    monkeypatch.setattr('superagent.runner.resolve_executable', lambda name: [name])
    try:
        result = await run_operation(brain, 'plan', tmp_path)
        if expected:
            assert len(result.tasks) == 1
            assert result.tasks[0].task_brief == expected, result.reasoning
            assert 'Planning fallback:' not in result.reasoning
        else:
            assert result.tasks[0].task_brief == 'implement robust execution handling'
            assert 'Planning fallback:' in result.reasoning
        assert not brain.supervisor._processes
    finally:
        await brain.supervisor.aclose()


@pytest.mark.asyncio
async def test_cli_synthesis_receives_complete_worker_report(monkeypatch, tmp_path):
    record = completed_record(tmp_path)

    def command(agent, prompt, *args):
        assert record.clean_output in prompt
        return ['codex']

    @asynccontextmanager
    async def process(*args, **kwargs):
        yield SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(
            b'A complete controlled synthesis response with all findings included.', b'')))

    monkeypatch.setattr(brain_module, 'agent_command', command)
    brain = SuperagentBrain(available_agents=['codex'])
    monkeypatch.setattr(brain.supervisor, 'process', process)
    monkeypatch.setattr('superagent.runner.resolve_executable', lambda name: [name])
    try:
        result = await run_operation(brain, 'synthesis', tmp_path)
        assert 'A complete controlled synthesis response' in result
    finally:
        await brain.supervisor.aclose()


def test_planner_accepts_explicit_reply_and_rejects_mixed_output():
    brain = SuperagentBrain(available_agents=['codex'])
    reply = brain._parse_planner_output('PLAN: Answer directly\nREPLY: The coordinator is ready.')
    assert reply.action == 'reply'
    assert reply.reply_text == 'The coordinator is ready.'
    assert reply.tasks == []
    for output in (
        'DELEGATE: codex | effort: low | Fix it\nREPLY: Also answer',
        'REPLY: Answer first\nDELEGATE: codex | effort: low | Fix it',
        'REPLY: First\nREPLY: Second',
    ):
        with pytest.raises(ValueError):
            brain._parse_planner_output(output)
