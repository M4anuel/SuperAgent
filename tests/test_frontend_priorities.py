import asyncio
from unittest.mock import AsyncMock

import pytest
from textual.widgets import RichLog

from superagent.brain import TriageDecision
from superagent.tui.app import PromptInput, SuperagentApp


@pytest.mark.asyncio
async def test_multiline_prompt_submission_and_growth(tmp_path, monkeypatch):
    app = SuperagentApp()
    app.cfg.add_project('test', str(tmp_path))
    app.cfg.set_active_project('test')
    triage = AsyncMock(return_value=TriageDecision(action='reply', reply_text='ok'))
    monkeypatch.setattr(app.brain, 'triage', triage)
    async with app.run_test() as pilot:
        prompt = app.query_one('#prompt-input', PromptInput)
        await pilot.press('a', 'shift+enter', 'b')
        await pilot.pause()
        assert prompt.text == 'a\nb'
        assert prompt.size.height == 2
        assert not triage.called
        await pilot.press('enter')
        await pilot.pause()
        assert triage.call_args.args[0] == 'a\nb'
        assert prompt.text == ''
        assert prompt.size.height == 1
        prompt.load_text('\n'.join(['x'] * 30))
        await pilot.pause()
        assert prompt.size.height == 6


@pytest.mark.asyncio
async def test_triage_error_visible_and_retry_succeeds(tmp_path, monkeypatch):
    app = SuperagentApp()
    app.cfg.add_project('test', str(tmp_path))
    app.cfg.set_active_project('test')
    triage = AsyncMock(side_effect=[RuntimeError('network [offline]'), TriageDecision(action='reply', reply_text='recovered')])
    monkeypatch.setattr(app.brain, 'triage', triage)
    async with app.run_test() as pilot:
        prompt = app.query_one('#prompt-input', PromptInput)
        prompt.load_text('hello')
        await pilot.press('enter')
        await pilot.pause()
        feed = app.query_one('#superagent-feed', RichLog)
        rendered = '\n'.join(line.text for line in feed.lines)
        assert 'Request failed: network [offline]' in rendered
        assert ':retry' in rendered
        assert prompt.has_focus
        await app._handle_command(':retry')
        await pilot.pause()
        assert triage.call_count == 2
        assert app.current_conversation.turns[-1].direct_reply == 'recovered'


@pytest.mark.asyncio
async def test_large_logs_are_bounded_batched_and_pageable(monkeypatch):
    app = SuperagentApp()
    async with app.run_test(size=(160, 45)) as pilot:
        task = app.runner.create_task(agent='codex', task_desc='test', project_name='test', cwd='.', effort='medium')
        task.log_lines = [f'LINE {n:05d} [literal]' for n in range(10000)]
        await pilot.pause()
        log = app.query_one('#subagent-log', RichLog)
        writes = []
        original = log.write
        def write(value, *args, **kwargs):
            writes.append(value)
            return original(value, *args, **kwargs)
        monkeypatch.setattr(log, 'write', write)
        app.select_task(task.task_id)
        assert len(writes) < 12
        assert 'LINE 09999 [literal]' in str(writes[-1])
        assert 'LINE 09599' not in str(writes[-1])
        assert str(writes[-1]).count('LINE') == 400
        await app._handle_command(':logs earlier')
        assert 'LINE 09599 [literal]' in str(writes[-1])
        assert 'LINE 09999' not in str(writes[-1])
        count = len(writes)
        app._handle_runner_event_sync(task, 'log')
        assert len(writes) == count
        await app._handle_command(':logs latest')
        assert 'LINE 09999 [literal]' in str(writes[-1])
        assert len(task.log_lines) == 10000

