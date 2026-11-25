import asyncio
from unittest.mock import AsyncMock

import pytest
from textual.widgets import RichLog, TabbedContent

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
        assert prompt.size.height == 30
        assert not prompt.show_vertical_scrollbar


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


@pytest.mark.asyncio
async def test_keyboard_card_navigation_and_selection():
    app = SuperagentApp()
    async with app.run_test(size=(120, 40)) as pilot:
        prompt = app.query_one('#prompt-input', PromptInput)
        t1 = app.runner.create_task(agent='codex', task_desc='Task 1', project_name='test', cwd='.', effort='low')
        t2 = app.runner.create_task(agent='agy', task_desc='Task 2', project_name='test', cwd='.', effort='medium')
        t3 = app.runner.create_task(agent='claude', task_desc='Task 3', project_name='test', cwd='.', effort='high')

        app._handle_runner_event_sync(t1, 'new')
        app._handle_runner_event_sync(t2, 'new')
        app._handle_runner_event_sync(t3, 'new')
        await pilot.pause()

        assert len(app.card_widgets) == 3
        assert app.active_task_id == t1.task_id
        assert app.card_widgets[t1.task_id].is_active_selection is True
        assert prompt.has_focus is True

        # Navigate next card from PromptInput using Alt+Down
        await pilot.press('alt+down')
        await pilot.pause()
        assert app.active_task_id == t3.task_id
        assert app.card_widgets[t3.task_id].is_active_selection is True
        assert app.query_one('#main-tabs', TabbedContent).active == 'tab-console'
        assert prompt.has_focus is True

        # Navigate next card using Alt+J
        await pilot.press('alt+j')
        await pilot.pause()
        assert app.active_task_id == t2.task_id
        assert app.card_widgets[t2.task_id].is_active_selection is True

        # Wrap around to first card
        await pilot.press('alt+down')
        await pilot.pause()
        assert app.active_task_id == t1.task_id

        # Navigate prev card using Alt+Up (wraps to t2)
        await pilot.press('alt+up')
        await pilot.pause()
        assert app.active_task_id == t2.task_id

        # Navigate prev card using Alt+K
        await pilot.press('alt+k')
        await pilot.pause()
        assert app.active_task_id == t3.task_id

        # Test direct card focus and key navigation (down/j, up/k, enter)
        card2 = app.card_widgets[t3.task_id]
        card2.focus()
        await pilot.pause()
        assert card2.has_focus is True

        await pilot.press('down')
        await pilot.pause()
        assert app.active_task_id == t2.task_id

        await pilot.press('up')
        await pilot.pause()
        assert app.active_task_id == t3.task_id

        # Enter on focused card selects it
        card1 = app.card_widgets[t1.task_id]
        card1.focus()
        await pilot.pause()
        await pilot.press('enter')
        await pilot.pause()
        assert app.active_task_id == t1.task_id
