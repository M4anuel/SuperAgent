"""Coordinator input lifecycle, with controlled processes and no agent launches."""
import asyncio
import json
import sys

import pytest

from superagent.adapters import PendingChoice, ProcessAdapter, UnsupportedContinuationError
from superagent.brain import SuperagentBrain
from superagent.runner import SubagentRunner


def python_command(lines, exitcode=0, delay=0):
    script = ('import time;'
              + ''.join(f'print({line!r},flush=True);' for line in lines)
              + f'time.sleep({delay});raise SystemExit({exitcode})')
    return [sys.executable, '-u', '-c', script]


class ControlledAdapter(ProcessAdapter):
    def __init__(self, agent='codex', responses=None):
        super().__init__(agent)
        self.responses = list(responses or [['Implemented the requested change.']])
        self.calls = []

    def continuation_command(self, session_id, prompt, cwd, effort, profile):
        self.calls.append((session_id, prompt, cwd, effort, profile))
        return python_command(self.responses.pop(0))


def worker(runner, tmp_path, lines, *, agent='codex', exitcode=0, delay=0):
    task = runner.create_task(agent, 'Implement this task', 'project', str(tmp_path),
                              custom_cmd=python_command(lines, exitcode, delay))
    task.adapter = ControlledAdapter(agent)
    task.execution_profile = 'workspace-write'
    return task


def request(question='Which database should I use?', choices=None, free_text=True):
    return json.dumps({'event': 'awaiting_input', 'question': question,
                       'choices': choices or [], 'allow_free_text': free_text})


@pytest.mark.asyncio
@pytest.mark.parametrize('answer', ['SQLite', 'Use the existing PostgreSQL cluster'])
async def test_choice_to_resume_preserves_identity_and_context(tmp_path, answer):
    runner = SubagentRunner()
    task = worker(runner, tmp_path, [
        json.dumps({'type': 'thread.started', 'thread_id': 'thread-one'}),
        json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': request(choices=['SQLite', 'PostgreSQL'])}})])
    events = []
    runner.subscribe(lambda record, event: events.append((record.task_id, event, record.status)))
    try:
        await runner.execute_task(task)
        assert task.status == 'AWAITING_INPUT' and task.returncode == 0
        assert task.end_time is None and task._process is None
        assert runner.pending_choices == [PendingChoice(
            task.task_id, 'Which database should I use?', ('SQLite', 'PostgreSQL'), True)]
        assert await runner.send_task_context(task.task_id, 'Keep the migration backwards compatible.')
        assert task.status == 'AWAITING_INPUT'
        assert not task.adapter.calls  # Context alone must not answer the question.
        assert await runner.answer_task(task.task_id, answer)
        assert task.status == 'DONE' and task.pending_choice is None
        assert task.end_time is not None and not runner.pending_choices
        assert task.clean_output == 'Implemented the requested change.'
        session, prompt, cwd, effort, profile = task.adapter.calls[0]
        assert (session, cwd, effort, profile) == ('thread-one', str(tmp_path.resolve()), 'medium', 'workspace-write')
        assert answer in prompt and 'Keep the migration backwards compatible.' in prompt
        assert task.task_desc == 'Implement this task'
        assert [entry.kind for entry in task.context] == ['context', 'answer']
        assert any(e[1:] == ('status', 'RUNNING') for e in events)
        assert any(e[1] == 'choice' for e in events)
        assert any(e[1] == 'answered' for e in events)
        assert all(e[0] == task.task_id for e in events)
    finally:
        await runner.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('agent,lines,question,choices', [
    ('codex', ['Which environment should I target?'], 'Which environment should I target?', ()),
    ('codex', ['Which backend should I use?', '1. SQLite', '2. PostgreSQL'],
     'Which backend should I use?', ('SQLite', 'PostgreSQL')),
    ('agy', [json.dumps({'event': 'init', 'conversation_id': 'full-conversation-id'}),
             json.dumps({'event': 'result', 'result': {'status': 'SUCCESS',
                         'response': 'Should I migrate the existing data?'}})],
     'Should I migrate the existing data?', ()),
    ('agy', [json.dumps({'event': 'result', 'result': {'status': 'SUCCESS',
                         'response': request('What is the deployment target?')}})],
     'What is the deployment target?', ()),
])
async def test_completed_process_question_is_unresolved(tmp_path, agent, lines, question, choices):
    runner = SubagentRunner()
    task = worker(runner, tmp_path, lines, agent=agent)
    try:
        await runner.execute_task(task)
        assert task.returncode == 0 and task.status == 'AWAITING_INPUT'
        assert task.pending_choice.question == question
        assert task.pending_choice.choices == choices
        assert task.pending_choice.allow_free_text
        if agent == 'agy' and len(lines) == 2:
            assert task.session_id == 'full-conversation-id'
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_context_and_answers_never_cross_tasks(tmp_path):
    runner = SubagentRunner()
    one = worker(runner, tmp_path, [request('Which backend should I use?')])
    two = worker(runner, tmp_path, [request('Which frontend should I use?')], agent='agy')
    try:
        await asyncio.gather(runner.execute_task(one), runner.execute_task(two))
        await runner.send_task_context(one.task_id, 'BACKEND_SECRET_CONTEXT')
        await runner.send_task_context(two.task_id, 'FRONTEND_SECRET_CONTEXT')
        await runner.answer_task(one.task_id, 'BACKEND_ANSWER')
        assert two.status == 'AWAITING_INPUT' and not two.adapter.calls
        assert 'FRONTEND_SECRET_CONTEXT' not in one.adapter.calls[0][1]
        await runner.answer_task(two.task_id, 'FRONTEND_ANSWER')
        assert 'BACKEND_SECRET_CONTEXT' not in two.adapter.calls[0][1]
        assert 'BACKEND_ANSWER' not in two.adapter.calls[0][1]
        assert 'FRONTEND_ANSWER' not in one.adapter.calls[0][1]
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_answer_waits_for_process_exit_and_duplicate_reply_is_rejected(tmp_path):
    runner = SubagentRunner()
    task = worker(runner, tmp_path, [request()], delay=.15)
    task.adapter.responses = [[request('Should I add an index?')]]
    choice_seen = asyncio.Event()
    runner.subscribe(lambda record, event: choice_seen.set() if event == 'choice' else None)
    execution = asyncio.create_task(runner.execute_task(task))
    try:
        await asyncio.wait_for(choice_seen.wait(), 5)
        assert task._process is not None
        answers = await asyncio.gather(runner.answer_task(task.task_id, 'SQLite'),
                                       runner.answer_task(task.task_id, 'PostgreSQL'))
        await execution
        assert answers == [True, False]
        assert len(task.adapter.calls) == 1
        assert task.pending_choice.question == 'Should I add an index?'
        assert task.status == 'AWAITING_INPUT'
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_context_for_running_task_uses_its_own_continuation(tmp_path):
    runner = SubagentRunner()
    task = worker(runner, tmp_path, ['Original turn completed.'], delay=.1)
    running = asyncio.Event()
    runner.subscribe(lambda record, event: running.set() if event == 'log' else None)
    execution = asyncio.create_task(runner.execute_task(task))
    try:
        await asyncio.wait_for(running.wait(), 5)
        assert await runner.send_task_context(task.task_id, 'Also check the index.')
        await execution
        assert task.status == 'DONE'
        assert 'Also check the index.' in task.adapter.calls[0][1]
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_input_waits_for_attempt_not_parent_coordinator(tmp_path):
    runner = SubagentRunner()
    task = worker(runner, tmp_path, [request()], delay=.1)
    choice_seen, keep_open = asyncio.Event(), asyncio.Event()
    runner.subscribe(lambda record, event: choice_seen.set() if event == 'choice' else None)

    async def coordinator():
        await runner.execute_task(task)
        await keep_open.wait()

    parent = asyncio.create_task(coordinator())
    try:
        await asyncio.wait_for(choice_seen.wait(), 5)
        assert await asyncio.wait_for(runner.answer_task(task.task_id, 'SQLite'), 5)
        assert task.status == 'DONE' and not parent.done()
    finally:
        keep_open.set()
        await parent
        await runner.aclose()


@pytest.mark.asyncio
async def test_cancel_waiting_live_process_does_not_cancel_sibling(tmp_path):
    runner = SubagentRunner()
    task = worker(runner, tmp_path, [request()], delay=30)
    sibling = worker(runner, tmp_path, ['Sibling completed.'])
    choice_seen = asyncio.Event()
    runner.subscribe(lambda record, event: choice_seen.set() if event == 'choice' else None)
    execution = asyncio.create_task(runner.execute_task(task))
    try:
        await asyncio.wait_for(choice_seen.wait(), 5)
        proc = task._process
        assert runner.cancel_task(task.task_id)
        with pytest.raises(asyncio.CancelledError):
            await execution
        await runner.execute_task(sibling)
        assert proc.returncode is not None
        assert task.status == 'CANCELLED' and task.pending_choice is None
        assert sibling.status == 'DONE'
    finally:
        await runner.aclose()


def test_queued_context_only_changes_target_command(tmp_path):
    async def submit():
        runner = SubagentRunner()
        one = runner.create_task('codex', 'Backend task', 'project', str(tmp_path))
        two = runner.create_task('agy', 'Frontend task', 'project', str(tmp_path))
        original = list(two.cmd)
        try:
            assert await runner.send_task_context(one.task_id, 'FIRST_CONTEXT')
            assert await runner.send_task_context(one.task_id, 'SECOND_CONTEXT')
            assert 'FIRST_CONTEXT' in one.cmd[-1] and 'SECOND_CONTEXT' in one.cmd[-1]
            assert two.cmd == original and not two.context
            assert one.status == 'QUEUED' and not one._pending_context
        finally:
            await runner.aclose()
    asyncio.run(submit())


@pytest.mark.asyncio
@pytest.mark.parametrize('adapter', [ProcessAdapter('agy'), ProcessAdapter('codex'),
                                    ProcessAdapter('codex', custom=True)])
async def test_unsupported_continuation_retains_pending_choice(tmp_path, adapter):
    runner = SubagentRunner()
    task = worker(runner, tmp_path, [request()])
    task.adapter = adapter
    events = []
    runner.subscribe(lambda record, event: events.append(event))
    try:
        await runner.execute_task(task)
        choice = task.pending_choice
        with pytest.raises(UnsupportedContinuationError):
            await runner.answer_task(task.task_id, 'SQLite')
        assert task.status == 'AWAITING_INPUT' and task.pending_choice is choice
        assert task.continuation_error and 'continuation_unsupported' in events
        assert not task.context
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_validation_and_cancel_are_targeted(tmp_path):
    runner = SubagentRunner()
    task = worker(runner, tmp_path, [request(choices=['yes', 'no'], free_text=False)])
    other = worker(runner, tmp_path, [request('What name should I use?')])
    try:
        await asyncio.gather(runner.execute_task(task), runner.execute_task(other))
        for invalid in ['', 'maybe']:
            with pytest.raises(ValueError):
                await runner.answer_task(task.task_id, invalid)
        assert not await runner.answer_task('missing', 'yes')
        assert not await runner.send_task_context('missing', 'hello')
        assert runner.cancel_task(task.task_id)
        assert not runner.cancel_task(task.task_id)
        assert task.status == 'CANCELLED' and task.pending_choice is None
        assert task.end_time is not None
        assert other.status == 'AWAITING_INPUT'
        assert not await runner.answer_task(task.task_id, 'yes')
        assert not await runner.send_task_context(task.task_id, 'late context')
        assert runner.pending_choices == [other.pending_choice]
    finally:
        await runner.aclose()


@pytest.mark.asyncio
async def test_failed_question_is_failed_and_live_question_logs_do_not_prompt(tmp_path):
    runner = SubagentRunner()
    failed = worker(runner, tmp_path, [request()], exitcode=2)
    done = worker(runner, tmp_path, ['What is this tool doing?', 'All changes completed.'])
    try:
        await asyncio.gather(runner.execute_task(failed), runner.execute_task(done))
        assert failed.status == 'FAILED' and failed.pending_choice is None
        assert done.status == 'DONE' and done.pending_choice is None
        assert not runner.pending_choices
    finally:
        await runner.aclose()


def test_codex_adapter_targets_session_and_retains_execution_policy(tmp_path):
    adapter = ProcessAdapter('codex')
    for profile in ('read-only', 'workspace-write', 'unrestricted'):
        command = adapter.continuation_command('unique-session', 'answer', str(tmp_path), 'high', profile)
        assert command[-2:] == ['unique-session', 'answer']
        assert command[command.index('exec') + 1] == 'resume'
        assert '--last' not in command and '--json' in command
        assert 'model_reasoning_effort="high"' in command
        if profile == 'unrestricted':
            assert '--dangerously-bypass-approvals-and-sandbox' in command
        else:
            assert f'sandbox_mode="{profile}"' in command


@pytest.mark.asyncio
async def test_waiting_fleet_report_does_not_claim_completion(tmp_path):
    runner = SubagentRunner()
    task = worker(runner, tmp_path, [request()])
    try:
        await runner.execute_task(task)
        brain = SuperagentBrain(available_agents=[])
        report = await brain.synthesize_results('implement', 'plan', [task], {'path': str(tmp_path)})
        assert task.task_id in report and task.pending_choice.question in report
        assert 'awaiting' in report.lower() and 'Analysis complete' not in report
    finally:
        await runner.aclose()
